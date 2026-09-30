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

RESULTS_NUDGE = ("[STUDIO] The results are in above. Now perform the on-air reply to the listener using them, in the "
                 "usual performance format. Don't repeat the line you already said while looking.")

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
            *,
            role: str
    ) -> Optional[Dict[str, Any]]:
        if temperature is None:
            temperature = settings.GEMINI_TEMPERATURE
        return await llm_router.generate_structured(
            spec=role,
            prompt=prompt,
            response_schema=response_schema,
            system=system_instruction,
            temperature=temperature,
            gemini_client=self.client,
            task=response_schema.__name__
        )

    async def generate(
            self,
            messages: list,
            model: Optional[str] = None,
            temperature: Optional[float] = None,
            max_tokens: Optional[int] = None,
            response_schema: Optional[Type[BaseModel]] = None,
            *,
            role: str
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
            *,
            role: str,
            validate: Optional[Callable[[str], bool]] = None,
            provider_notes: Optional[Dict[str, str]] = None
    ) -> str:
        if temperature is None:
            temperature = settings.GEMINI_TEMPERATURE
        if max_tokens is None:
            max_tokens = 8192
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

    async def run_tool_turn(
            self,
            system_instruction: str,
            user_message: str,
            function_declarations: list,
            dispatch: Callable[[str, dict], Awaitable[dict]],
            temperature: float = 0.4,
            max_tokens: int = 8192,
            max_rounds: int = 4,
            call_timeout_s: float = 8.0,
            spec: str = llm_router.LLM_BACKGROUND
    ) -> Dict[str, Any]:
        gemini = dict(system_instruction=system_instruction, user_message=user_message,
                      function_declarations=function_declarations, dispatch=dispatch, temperature=temperature,
                      max_tokens=max_tokens, max_rounds=max_rounds, call_timeout_s=call_timeout_s, spec=spec)
        provider, model = llm_router.candidates_for(spec)[0]
        if provider != "deepseek":
            return await self.run_gemini_tool_turn(**gemini)
        key = llm_router._circuit_key(spec, provider, model)
        try:
            result = await self._run_deepseek_tool_turn(
                model, system_instruction, user_message, function_declarations, dispatch, temperature, max_tokens,
                max_rounds, call_timeout_s, spec)
            llm_router._record_success(key)
            return result
        except llm_router.LLM_ERRORS as err:
            llm_router._record_failure(key, err)
            llm_router.record_fallback(llm_router.role_label(spec), f"{provider}:{model}", "gemini tool loop",
                                       llm_router._err_line(err))
            return await self.run_gemini_tool_turn(**gemini)

    async def _run_deepseek_tool_turn(self, model: str, system_instruction: str, user_message: str,
                                      function_declarations: list, dispatch, temperature: float, max_tokens: int,
                                      max_rounds: int, call_timeout_s: float, spec: str) -> Dict[str, Any]:
        tools = [{"type": "function", "function": {
            "name": d.name, "description": d.description,
            "parameters": d.parameters_json_schema or {"type": "object", "properties": {}}}}
            for d in function_declarations]
        messages: list = [{"role": "system", "content": system_instruction},
                          {"role": "user", "content": user_message}]
        calls_log: list = []
        made: set = set()
        for round_no in range(1, max_rounds + 1):
            is_last = round_no == max_rounds
            reply = await llm_router.deepseek_chat(spec=spec, model=model, messages=messages, temperature=temperature,
                                                   max_tokens=max_tokens, tools=None if is_last else tools)
            message = reply["message"]
            tool_calls = [] if is_last else (message.get("tool_calls") or [])
            if not tool_calls:
                return {"text": reply["text"], "preambles": [], "tool_calls": calls_log, "rounds": round_no,
                        "usage": [], "trace": []}
            messages.append(message)
            fresh = []
            for call in tool_calls:
                name = (call.get("function") or {}).get("name") or ""
                try:
                    args = json.loads((call.get("function") or {}).get("arguments") or "{}")
                except ValueError:
                    args = None
                fresh.append((call, name, args if isinstance(args, dict) else None))

            async def run(name: str, args: Optional[dict]) -> dict:
                if args is None:
                    return {"status": "error", "error": "The arguments were not valid JSON."}
                if self._call_key(name, args) in made:
                    return {"status": "repeat", "note": "You already made this exact call this turn; use that result."}
                made.add(self._call_key(name, args))
                log_service.ai(f"🔧 Agent tool call: {name}({args})")
                return await self._run_tool_call(types.FunctionCall(name=name, args=args), dispatch, call_timeout_s)

            results = await asyncio.gather(*(run(name, args) for _, name, args in fresh))
            for (call, name, args), result in zip(fresh, results):
                calls_log.append({"name": name, "args": args or {}, "result": result})
                messages.append({"role": "tool", "tool_call_id": call.get("id"), "content": json.dumps(
                    self._cap_tool_result(result, settings.LLM_TOOL_RESULT_MAX_CHARS), default=str,
                    ensure_ascii=False)})
        raise RuntimeError("unreachable")

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
            on_preamble: Optional[Callable[[str, list], Awaitable[None]]] = None,
            followup_tools: Optional[set] = None,
            repeatable_tools: Optional[set] = None,
            review: Optional[Callable[[str, list], Optional[str]]] = None,
            refresh_tools: Optional[Callable[[], list]] = None,
            thinking_budget: Optional[int] = None,
            followup_thinking_budget: Optional[int] = None,
            cache_label: Optional[str] = None,
            spec: str = llm_router.LLM_LIVE
    ) -> Dict[str, Any]:
        if temperature is None:
            temperature = settings.GEMINI_DJ_TEMPERATURE
        if max_tokens is None:
            max_tokens = 8192

        def build_configs(declarations, budget=thinking_budget):
            base = dict(temperature=temperature, max_output_tokens=max_tokens, system_instruction=system_instruction)
            if budget is not None:
                base["thinking_config"] = types.ThinkingConfig(thinking_budget=budget)
            if not declarations:
                plain = types.GenerateContentConfig(**base)
                return plain, plain
            with_tools = types.GenerateContentConfig(
                **base,
                tools=[types.Tool(function_declarations=declarations)],
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)
            )

            def mode(value):
                return with_tools.model_copy(update={"tool_config": types.ToolConfig(
                    function_calling_config=types.FunctionCallingConfig(mode=value))})
            return with_tools, mode(types.FunctionCallingConfigMode.NONE)

        followup_budget = thinking_budget if followup_thinking_budget is None else followup_thinking_budget
        tool_config, final_config = build_configs(function_declarations)
        followup_tool_config, followup_final_config = build_configs(function_declarations, followup_budget)
        declared = {declaration.name for declaration in function_declarations or []}

        contents: list = [types.Content(role="user", parts=[types.Part.from_text(text=user_message)])]
        calls_log: list = []
        preambles: list = []
        round_usage: list = []
        strikes = 0
        reviewed = False
        made: set = set()
        trace: list = []
        tool_rounds = 0
        rounds = 0

        while True:
            rounds += 1
            is_last = not function_declarations or tool_rounds >= max_rounds
            log_service.detail(f"DJ tool round {rounds} (tool rounds {tool_rounds}/{max_rounds})", "ai")

            if tool_rounds > 1:
                compressed = self._compress_prior_tool_responses(contents, settings.LLM_TOOL_COMPRESS_MIN_CHARS)
                if compressed:
                    log_service.ai(f"Compressed {compressed} prior tool result(s) before round {rounds}")

            response, usage, _, model = await llm_router.gemini_generate_chain(
                spec=spec,
                client=self.client,
                contents=contents,
                config=((followup_final_config if is_last else followup_tool_config) if tool_rounds
                        else (final_config if is_last else tool_config)),
                prefer=model,
                cache_label=cache_label
            )
            round_usage.append(usage)

            candidate = response.candidates[0] if response.candidates else None
            content = candidate.content if candidate else None
            parts = list(content.parts) if content and content.parts else []
            function_calls = [p.function_call for p in parts if p.function_call] if not is_last else []
            aired = {self._echo_key(line) for line in preambles} - {""}
            echoed = [p for p in parts if p.text and not getattr(p, "thought", False)
                      and self._echo_key(p.text) in aired]
            repeats = [fc for fc in function_calls if self._call_key(fc.name, fc.args) in made
                       and (echoed or fc.name not in (repeatable_tools or set()))]
            if repeats or echoed:
                log_service.commands(
                    "DJ tool turn: the model echoed its earlier turn - dropped "
                    + (f"a repeat of {', '.join(fc.name for fc in repeats)}" if repeats else "the repeated line"))
                function_calls = [fc for fc in function_calls if fc not in repeats]
                parts = [p for p in parts if p.function_call not in repeats
                         and not any(p is part for part in echoed)]
                content = types.Content(role=content.role, parts=parts)
            made.update(self._call_key(fc.name, fc.args) for fc in function_calls)
            text = self._visible_text(parts)
            finish = str(getattr(candidate, "finish_reason", "") or "NO_CANDIDATE").rsplit(".", 1)[-1].upper()
            trace.append({"round": rounds, "model": model, "finish": finish, "text": text,
                          "thought": any(getattr(p, "thought", False) for p in parts),
                          "parts": [{"chars": len(p.text or ""), "thought": bool(getattr(p, "thought", False)),
                                     "signed": bool(getattr(p, "thought_signature", None)),
                                     "call": bool(p.function_call)} for p in parts],
                          "calls": [{"name": fc.name, "args": dict(fc.args) if fc.args else {}}
                                    for fc in function_calls]})

            if not function_calls and finish == "MAX_TOKENS" and strikes < settings.LLM_RECOVERY_MAX_STRIKES:
                strikes += 1
                log_service.warning(f"DJ tool turn: reply cut off at the token limit - asking again (strike {strikes})")
                if parts:
                    contents.append(content)
                trace[-1]["studio"] = self._recovery_message("MAX_TOKENS", bool(calls_log))
                contents.append(types.Content(role="user", parts=[types.Part.from_text(
                    text=self._recovery_message("MAX_TOKENS", bool(calls_log)))]))
                continue

            if not function_calls and review is not None and not reviewed and not is_last:
                prompt = review(text, calls_log)
                if prompt:
                    reviewed = True
                    log_service.commands(f"DJ tool turn: review step ({prompt[:80]})")
                    if text.strip():
                        preambles.append(text)
                        if on_preamble is not None:
                            try:
                                await on_preamble(text, [])
                            except Exception as e:
                                log_service.error(f"DJ preamble handler failed: {e}")
                    if parts:
                        contents.append(content)
                    trace[-1]["studio"] = prompt
                    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=prompt)]))
                    continue

            if not function_calls:
                said_enough = bool(preambles) and not any(
                    call["name"] in (followup_tools or set()) for call in calls_log)
                recovery = None if text.strip() or said_enough else self._recovery_message(finish, bool(calls_log))
                if recovery and strikes < settings.LLM_RECOVERY_MAX_STRIKES:
                    strikes += 1
                    log_service.warning(f"DJ tool turn: no reply (finish {finish}) - recovery {strikes}")
                    if parts:
                        contents.append(content)
                    trace[-1]["studio"] = recovery
                    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=recovery)]))
                    continue
                if not text.strip():
                    log_service.error(f"DJ tool turn returned no text (finish reason: {finish})")
                return {
                    "text": text,
                    "preambles": preambles,
                    "tool_calls": calls_log,
                    "rounds": rounds,
                    "usage": round_usage,
                    "trace": trace
                }

            tool_rounds += 1
            if text.strip():
                preambles.append(text)
            if on_preamble is not None:
                try:
                    await on_preamble(text, [(fc.name, dict(fc.args) if fc.args else {}) for fc in function_calls])
                except Exception as e:
                    log_service.error(f"DJ preamble handler failed: {e}")

            contents.append(content)

            done_with = self._done_with(function_calls)
            if done_with:
                released = self._release_done_results(contents, done_with)
                if released:
                    log_service.commands(f"Released {released} used tool result(s): {', '.join(sorted(done_with))}")

            for fc in function_calls:
                log_service.ai(f"🔧 DJ tool call: {fc.name}({dict(fc.args) if fc.args else {}})")

            results = await asyncio.gather(*(self._run_tool_call(fc, dispatch, call_timeout_s) for fc in function_calls))

            response_parts = []
            for fc, result in zip(function_calls, results):
                calls_log.append({"name": fc.name, "args": dict(fc.args) if fc.args else {}, "result": result})
                trace[-1].setdefault("results", []).append({"name": fc.name, "result": result})
                response_parts.append(types.Part(function_response=types.FunctionResponse(
                    id=fc.id,
                    name=fc.name,
                    response=self._cap_tool_result(result, settings.LLM_TOOL_RESULT_MAX_CHARS)
                )))
            if text.strip():
                response_parts.append(types.Part.from_text(
                    text="[STUDIO] Your line above has aired and these calls have run. Reply on air now; call a "
                         "tool only if you need something you don't have yet."))
            contents.append(types.Content(role="user", parts=response_parts))

            if refresh_tools is not None:
                refreshed = refresh_tools()
                if {declaration.name for declaration in refreshed} != declared:
                    function_declarations = refreshed
                    declared = {declaration.name for declaration in refreshed}
                    tool_config, final_config = build_configs(refreshed)
                    followup_tool_config, followup_final_config = build_configs(refreshed, followup_budget)
                    log_service.detail(f"DJ tools now: {', '.join(sorted(declared))}", "ai")

    @staticmethod
    def _echo_key(text: str) -> str:
        key = " ".join(re.sub(r"\(\d+ chars\)", "", text or "").split())[:80]
        return key if len(key) >= 20 else ""

    @staticmethod
    def _call_key(name: str, args) -> tuple:
        return name, json.dumps({k: v for k, v in dict(args or {}).items() if k != "_done_with"},
                                sort_keys=True, default=str)

    @staticmethod
    def _recovery_message(finish: str, used_tools: bool) -> Optional[str]:
        if finish == "MALFORMED_FUNCTION_CALL":
            return ("[STUDIO] Your last function call was malformed. Call the tool again with valid arguments, "
                    "or perform the on-air reply.")
        if finish == "MAX_TOKENS":
            return "[STUDIO] Your reply was cut off. Perform a shorter on-air reply now."
        if finish in ("STOP", "NO_CANDIDATE", "FINISH_REASON_UNSPECIFIED", "OTHER"):
            return RESULTS_NUDGE if used_tools else (
                "[STUDIO] Your previous turn produced no reply. Either call the tool you need now, or perform the "
                "on-air reply to the listener.")
        return None

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

    @staticmethod
    def _done_with(function_calls) -> Dict[str, str]:
        done: Dict[str, str] = {}
        for fc in function_calls:
            raw = (dict(fc.args) if fc.args else {}).get("_done_with")
            if isinstance(raw, dict):
                done.update({str(k): str(v or "")[:300] for k, v in raw.items() if k})
            elif isinstance(raw, list):
                done.update({str(item): "" for item in raw if item})
            elif isinstance(raw, str) and raw:
                done[raw] = ""
        return done

    @staticmethod
    def _release_done_results(contents: list, done_with: Dict[str, str]) -> int:
        released = 0
        for idx, content in enumerate(contents):
            if content.role != "user" or not content.parts or not any(p.function_response for p in content.parts):
                continue
            rebuilt = []
            changed = False
            for part in content.parts:
                fr = part.function_response
                payload = fr.response if fr else None
                if fr is None or fr.name not in done_with or (isinstance(payload, dict) and payload.get("released")):
                    rebuilt.append(part)
                    continue
                summary = {"released": True, "tool": fr.name}
                if done_with[fr.name]:
                    summary["what_you_took"] = done_with[fr.name]
                rebuilt.append(types.Part(function_response=types.FunctionResponse(id=fr.id, name=fr.name,
                                                                                   response=summary)))
                changed = True
                released += 1
            if changed:
                contents[idx] = types.Content(role=content.role, parts=rebuilt)
        return released

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