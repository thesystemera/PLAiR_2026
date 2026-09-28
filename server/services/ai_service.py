import asyncio
import json
import re
from typing import Any, Awaitable, Callable, Dict, Optional, Type
from pydantic import BaseModel
from google import genai
from google.genai import types
from services import log_service
from services import llm_router
from services.base_service import SingletonService
from config import settings

GEMINI_HTTP_TIMEOUT_MS = 120_000
GEMINI_HTTP_RETRY_ATTEMPTS = 3
GEMINI_HTTP_RETRY_INITIAL_DELAY_S = 1.0
GEMINI_HTTP_RETRY_MAX_DELAY_S = 10.0

def build_gemini_http_options(timeout_ms: int = GEMINI_HTTP_TIMEOUT_MS,
                              attempts: int = GEMINI_HTTP_RETRY_ATTEMPTS) -> types.HttpOptions:
    return types.HttpOptions(
        timeout=timeout_ms,
        retry_options=types.HttpRetryOptions(
            attempts=attempts,
            initial_delay=GEMINI_HTTP_RETRY_INITIAL_DELAY_S,
            max_delay=GEMINI_HTTP_RETRY_MAX_DELAY_S
        )
    )

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
            self.client = genai.Client(api_key=gemini_api_key, http_options=build_gemini_http_options())
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
            system_instruction: Optional[str] = None,
            role: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        if model is None:
            model = settings.GEMINI_MODEL
        if temperature is None:
            temperature = settings.GEMINI_TEMPERATURE

        if role:
            return await llm_router.generate_structured(
                spec=role,
                prompt=prompt,
                response_schema=response_schema,
                system=system_instruction,
                temperature=temperature,
                gemini_client=self.client,
                task=response_schema.__name__
            )

        log_service.ai(f"Calling Gemini API with structured output: {model}")

        config_params = {
            "response_mime_type": "application/json",
            "response_schema": response_schema,
            "temperature": temperature
        }

        if system_instruction:
            config_params["system_instruction"] = system_instruction

        response, _, _ = await llm_router.gemini_generate(
            spec="LLM_GEMINI_DIRECT",
            client=self.client,
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(**config_params),
            timeout=GEMINI_HTTP_TIMEOUT_MS / 1000
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
            response_schema: Optional[Type[BaseModel]] = None,
            role: Optional[str] = None
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
                system_instruction=system_instruction,
                role=role
            )
            return _GeminiResponse(result, is_structured=True)

        response_text = await self.call_gemini(
            prompt=user_content,
            system_instruction=system_instruction,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            role=role
        )
        return _GeminiResponse(response_text, is_structured=False)

    async def call_gemini(
            self,
            prompt: str,
            system_instruction: Optional[str] = None,
            model: Optional[str] = None,
            temperature: Optional[float] = None,
            max_tokens: Optional[int] = None,
            role: Optional[str] = None,
            validate: Optional[Callable[[str], bool]] = None,
            provider_notes: Optional[Dict[str, str]] = None
    ) -> str:
        if model is None:
            model = settings.GEMINI_MODEL
        if temperature is None:
            temperature = settings.GEMINI_TEMPERATURE
        if max_tokens is None:
            max_tokens = 2048

        if role:
            result = await llm_router.generate(
                spec=role,
                prompt=prompt,
                system=system_instruction,
                temperature=temperature,
                max_tokens=max_tokens,
                validate=validate,
                provider_notes=provider_notes,
                gemini_client=self.client
            )
            log_service.ai(f"Text generation completed via {result.get('provider')}:{result.get('model')}")
            return result.get("text") or ""

        log_service.ai(f"Calling Gemini API for text generation: {model}")

        config = types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
            system_instruction=system_instruction
        )

        response, _, _ = await llm_router.gemini_generate(
            spec="LLM_GEMINI_DIRECT",
            client=self.client,
            model=model,
            contents=prompt,
            config=config,
            timeout=GEMINI_HTTP_TIMEOUT_MS / 1000
        )

        log_service.ai("Text generation completed")
        return llm_router.gemini_visible_text(response)

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

            response, _, _ = await llm_router.gemini_generate(
                spec="LLM_GEMINI_TOOLS",
                client=self.client,
                model=model,
                contents=conversation,
                config=config,
                timeout=GEMINI_HTTP_TIMEOUT_MS / 1000
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

    @staticmethod
    def _visible_text(parts: list) -> str:
        return "".join(p.text for p in parts if p.text and not p.thought)

    @staticmethod
    async def _run_tool_call(fc, dispatch: Callable[[str, dict], Awaitable[dict]], timeout_s: float) -> dict:
        name = fc.name or ""
        args = dict(fc.args) if fc.args else {}
        task = asyncio.ensure_future(dispatch(name, args))
        done, _ = await asyncio.wait({task}, timeout=timeout_s)
        if not done:
            log_service.ai(f"Tool {name} still running after {timeout_s}s - continuing without waiting")
            return {"status": "still_running", "note": "The action is still in progress and will finish in the background."}
        try:
            result = task.result()
        except Exception as e:
            log_service.error(f"Tool {name} failed: {e}")
            return {"status": "error", "error": str(e)[:200]}
        return result if isinstance(result, dict) else {"result": result}

    async def run_gemini_tool_turn(
            self,
            system_instruction: str,
            user_message: str,
            function_declarations: list,
            dispatch: Callable[[str, dict], Awaitable[dict]],
            model: Optional[str] = None,
            temperature: Optional[float] = None,
            max_tokens: Optional[int] = None,
            max_rounds: int = 4,
            call_timeout_s: float = 8.0,
            on_preamble: Optional[Callable[[str], Awaitable[None]]] = None
    ) -> Dict[str, Any]:
        if model is None:
            model = settings.GEMINI_DJ_MODEL
        if temperature is None:
            temperature = settings.GEMINI_DJ_TEMPERATURE
        if max_tokens is None:
            max_tokens = 2048

        tool_config = types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
            system_instruction=system_instruction,
            tools=[types.Tool(function_declarations=function_declarations)],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)
        )
        final_config = tool_config.model_copy(update={
            "tool_config": types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode=types.FunctionCallingConfigMode.NONE)
            )
        })

        contents: list = [types.Content(role="user", parts=[types.Part.from_text(text=user_message)])]
        calls_log: list = []
        preambles: list = []
        round_usage: list = []

        for round_index in range(max_rounds + 1):
            is_last = round_index == max_rounds
            log_service.ai(f"DJ tool round {round_index + 1}/{max_rounds + 1}: {model}")

            if round_index > 1:
                compressed = self._compress_prior_tool_responses(contents, settings.LLM_TOOL_COMPRESS_MIN_CHARS)
                if compressed:
                    log_service.ai(f"Compressed {compressed} prior tool result(s) before round {round_index + 1}")

            response, usage, _ = await llm_router.gemini_generate(
                spec=llm_router.LLM_LIVE,
                client=self.client,
                model=model,
                contents=contents,
                config=final_config if is_last else tool_config
            )
            round_usage.append(usage)

            candidate = response.candidates[0] if response.candidates else None
            content = candidate.content if candidate else None
            parts = list(content.parts) if content and content.parts else []
            function_calls = [p.function_call for p in parts if p.function_call]
            text = self._visible_text(parts)

            if not function_calls or is_last:
                if not text:
                    finish_reason = candidate.finish_reason if candidate else "NO_CANDIDATE"
                    log_service.error(f"DJ tool turn returned no text (finish reason: {finish_reason})")
                return {
                    "text": text,
                    "preambles": preambles,
                    "tool_calls": calls_log,
                    "rounds": round_index + 1,
                    "usage": round_usage
                }

            if text.strip():
                preambles.append(text)
            if on_preamble is not None:
                try:
                    await on_preamble(text)
                except Exception as e:
                    log_service.error(f"DJ preamble handler failed: {e}")

            contents.append(content)

            for fc in function_calls:
                log_service.ai(f"🔧 DJ tool call: {fc.name}({dict(fc.args) if fc.args else {}})")

            results = await asyncio.gather(*(self._run_tool_call(fc, dispatch, call_timeout_s) for fc in function_calls))

            response_parts = []
            for fc, result in zip(function_calls, results):
                calls_log.append({"name": fc.name, "args": dict(fc.args) if fc.args else {}, "result": result})
                response_parts.append(types.Part(function_response=types.FunctionResponse(
                    id=fc.id,
                    name=fc.name,
                    response=self._cap_tool_result(result, settings.LLM_TOOL_RESULT_MAX_CHARS)
                )))
            contents.append(types.Content(role="user", parts=response_parts))

        return {"text": "", "preambles": preambles, "tool_calls": calls_log, "rounds": max_rounds + 1, "usage": round_usage}

    @staticmethod
    def _result_size(result: Any) -> int:
        return len(json.dumps(result, default=str, ensure_ascii=False))

    @staticmethod
    def _tool_history_summary(name: str, result: Any) -> dict:
        result = result if isinstance(result, dict) else {"result": result}
        summary: Dict[str, Any] = {"compressed": True, "tool": name, "status": result.get("status", "ok")}
        for key, value in result.items():
            if key in summary:
                continue
            if isinstance(value, (int, float, bool)) or value is None:
                summary[key] = value
            elif isinstance(value, str):
                summary[key] = value[:160]
            elif isinstance(value, list) and all(isinstance(v, str) for v in value):
                summary[key] = [v[:80] for v in value[:5]]
        return summary

    @classmethod
    def _cap_tool_result(cls, result: Any, max_chars: int) -> Any:
        if max_chars <= 0 or cls._result_size(result) <= max_chars:
            return result
        if not isinstance(result, dict):
            return {"result": str(result)[:max_chars], "truncated": True}
        capped: Dict[str, Any] = {}
        for key, value in result.items():
            if isinstance(value, str):
                capped[key] = value[:400]
            elif isinstance(value, list):
                capped[key] = [v[:200] if isinstance(v, str) else v for v in value[:5]]
            elif isinstance(value, dict):
                capped[key] = json.dumps(value, default=str, ensure_ascii=False)[:400]
            else:
                capped[key] = value
        capped["truncated"] = True
        if cls._result_size(capped) <= max_chars:
            return capped
        return {**cls._tool_history_summary(str(result.get("tool", "tool")), result), "truncated": True}

    @classmethod
    def _compress_prior_tool_responses(cls, contents: list, min_chars: int) -> int:
        if min_chars <= 0:
            return 0
        compressed = 0
        response_indexes = [i for i, c in enumerate(contents)
                            if c.role == "user" and c.parts and any(p.function_response for p in c.parts)]
        for idx in response_indexes[:-1]:
            content = contents[idx]
            rebuilt = []
            changed = False
            for part in content.parts:
                fr = part.function_response
                payload = fr.response if fr else None
                if fr is None or (isinstance(payload, dict) and payload.get("compressed")) or cls._result_size(payload) < min_chars:
                    rebuilt.append(part)
                    continue
                rebuilt.append(types.Part(function_response=types.FunctionResponse(
                    id=fr.id,
                    name=fr.name,
                    response=cls._tool_history_summary(fr.name or "tool", payload)
                )))
                changed = True
                compressed += 1
            if changed:
                contents[idx] = types.Content(role=content.role, parts=rebuilt)
        return compressed