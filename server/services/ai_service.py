import asyncio
import json
import re
from typing import Any, Dict, Optional, Type
from pydantic import BaseModel
from google import genai
from google.genai import types
from services import log_service
from services.base_service import SingletonService
from config import settings

class _GeminiMessage:
    def __init__(self, content):
        self.content = content

class _GeminiChoice:
    def __init__(self, message):
        self.message = message

class _GeminiResponse:
    def __init__(self, content, is_structured=False):
        if is_structured:
            self.choices = [_GeminiChoice(_GeminiMessage(json.dumps(content)))]
            self.structured_data = content
        else:
            self.choices = [_GeminiChoice(_GeminiMessage(content))]

class MusicGenerationParams(BaseModel):
    prompt: str
    style: str
    title: str
    artist_name: Optional[str] = None
    custom_mode: bool
    instrumental: bool
    model: str
    negative_tags: Optional[str] = None
    vocal_gender: Optional[str] = None
    style_weight: float
    weirdness: float
    audio_weight: float

class AIService(SingletonService):
    def __init__(self):
        if getattr(self, '_initialized', False):
            return

        self.client: Optional[genai.Client] = None
        self.gemini_configured = False
        self._initialized = True

    async def initialize(self):
        if self.gemini_configured:
            log_service.ai("AIService already initialized")
            return

        gemini_api_key = settings.load_api_key_from_file("GEMINI_API_KEY")

        if gemini_api_key:
            self.client = genai.Client(api_key=gemini_api_key)
            self.gemini_configured = True
            log_service.ai("AIService initialized - Gemini configured")
        else:
            log_service.error("Gemini API key not found")

    async def call_gemini_structured(
            self,
            prompt: str,
            response_schema: Type[BaseModel],
            model: Optional[str] = None,
            temperature: Optional[float] = None,
            system_instruction: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        if model is None:
            model = settings.GEMINI_MODEL
        if temperature is None:
            temperature = settings.GEMINI_TEMPERATURE

        log_service.ai(f"Calling Gemini API with structured output: {model}")

        config_params = {
            "response_mime_type": "application/json",
            "response_schema": response_schema,
            "temperature": temperature
        }

        if system_instruction:
            config_params["system_instruction"] = system_instruction

        response = await asyncio.to_thread(
            self.client.models.generate_content,  # type: ignore
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(**config_params)
        )

        if response.parsed is None:
            log_service.error("Gemini returned None for structured output")
            return None

        parsed_data = response.parsed
        if isinstance(parsed_data, dict):
            result = parsed_data
        else:
            result = parsed_data.model_dump()  # type: ignore
        log_service.ai("Structured output generated")
        return result

    async def generate(
            self,
            messages: list,
            model: Optional[str] = None,
            temperature: Optional[float] = None,
            max_tokens: Optional[int] = None,
            response_schema: Optional[Type[BaseModel]] = None
    ) -> _GeminiResponse:
        system_instruction = None
        user_content = ""

        for msg in messages:
            msg_role = msg.get("role")
            msg_content = msg.get("content", "")
            if msg_role == "system":
                system_instruction = msg_content
            elif msg_role == "user":
                user_content = msg_content

        if model is None:
            model = settings.GEMINI_DJ_MODEL
        if temperature is None:
            temperature = settings.GEMINI_DJ_TEMPERATURE

        if response_schema:
            result = await self.call_gemini_structured(
                prompt=user_content,
                response_schema=response_schema,
                model=model,
                temperature=temperature,
                system_instruction=system_instruction
            )
            return _GeminiResponse(result, is_structured=True)

        response_text = await self.call_gemini(
            prompt=user_content,
            system_instruction=system_instruction,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens
        )
        return _GeminiResponse(response_text, is_structured=False)

    async def call_gemini(
            self,
            prompt: str,
            system_instruction: Optional[str] = None,
            model: Optional[str] = None,
            temperature: Optional[float] = None,
            max_tokens: Optional[int] = None
    ) -> str:
        if model is None:
            model = settings.GEMINI_MODEL
        if temperature is None:
            temperature = settings.GEMINI_TEMPERATURE
        if max_tokens is None:
            max_tokens = 2048

        log_service.ai(f"Calling Gemini API for text generation: {model}")

        config = types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
            system_instruction=system_instruction
        )

        response = await asyncio.to_thread(
            self.client.models.generate_content,  # type: ignore
            model=model,
            contents=prompt,
            config=config
        )

        log_service.ai("Text generation completed")
        return response.text if response.text else ""

    async def call_gemini_with_tools(
            self,
            prompt: str,
            tools: list,
            tool_handlers: dict,
            response_schema: Optional[Type[BaseModel]] = None,
            system_instruction: Optional[str] = None,
            model: Optional[str] = None,
            temperature: Optional[float] = None,
            max_iterations: int = 5
    ) -> Dict[str, Any]:
        if model is None:
            model = settings.GEMINI_MODEL
        if temperature is None:
            temperature = settings.GEMINI_TEMPERATURE

        log_service.ai(f"Calling Gemini with tools: {model}")

        config_params = {
            "temperature": temperature,
            "tools": tools
        }

        if system_instruction:
            if response_schema:
                system_instruction += f"\n\nIMPORTANT: After using tools, you must return the final response as valid JSON matching this schema: {response_schema.model_json_schema()}"
            config_params["system_instruction"] = system_instruction

        config = types.GenerateContentConfig(**config_params)

        conversation: list[dict[str, Any]] = [{"role": "user", "parts": [{"text": prompt}]}]  # type: ignore

        for iteration in range(max_iterations):
            log_service.ai(f"Tool iteration {iteration + 1}/{max_iterations}")

            response = await asyncio.to_thread(
                self.client.models.generate_content,  # type: ignore
                model=model,
                contents=conversation,  # type: ignore
                config=config
            )

            function_call_found = False
            if response.candidates and len(response.candidates) > 0:
                candidate = response.candidates[0]
                if candidate.content and candidate.content.parts:
                    for part in candidate.content.parts:
                        if part.function_call:
                            function_call_found = True
                            fc = part.function_call
                            function_name = fc.name if fc.name else ""
                            function_args = dict(fc.args) if fc.args else {}

                            log_service.ai(f"🔧 Gemini calling tool: {function_name}({function_args})")

                            handler = tool_handlers.get(function_name)
                            if handler is not None:
                                tool_result = await handler(**function_args)

                                result_str = str(tool_result)
                                log_service.ai(f"Tool {function_name} returned: {len(result_str)} chars")

                                conversation.append({
                                    "role": "model",
                                    "parts": [{"function_call": fc}]  # type: ignore
                                })
                                conversation.append({
                                    "role": "user",
                                    "parts": [{
                                        "function_response": {
                                            "name": function_name,
                                            "response": tool_result
                                        }
                                    }]  # type: ignore
                                })
                            else:
                                raise ValueError(f"Unknown tool requested: {function_name}")
                            break

            if function_call_found:
                continue

            if response_schema:
                text_response = None

                if hasattr(response, 'text') and response.text:
                    text_response = response.text

                if not text_response and response.candidates and len(response.candidates) > 0:
                    candidate = response.candidates[0]
                    if candidate.content and candidate.content.parts:
                        text_parts = [p.text for p in candidate.content.parts if hasattr(p, 'text') and p.text]
                        if text_parts:
                            text_response = "".join(text_parts)

                if not text_response:
                    finish_reason = "Unknown"
                    if response.candidates:
                        finish_reason = response.candidates[0].finish_reason
                    log_service.error(f"Gemini returned empty text. Finish Reason: {finish_reason}")
                    raise ValueError(f"Gemini generated empty response (Finish Reason: {finish_reason})")

                cleaned_text = text_response.strip()
                if "```" in cleaned_text:
                    cleaned_text = re.sub(r"^```json\s*", "", cleaned_text, flags=re.MULTILINE)
                    cleaned_text = re.sub(r"^```\s*", "", cleaned_text, flags=re.MULTILINE)
                    cleaned_text = re.sub(r"\s*```$", "", cleaned_text, flags=re.MULTILINE)

                result = json.loads(cleaned_text)
                log_service.ai("Structured generation with tools completed")
                return result
            else:
                log_service.ai("Text generation with tools completed")
                return {"text": response.text if response.text else ""}

        raise RuntimeError(f"Tool call loop exceeded {max_iterations} iterations without final response")