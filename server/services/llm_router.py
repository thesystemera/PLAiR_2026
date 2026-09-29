import asyncio
import json
import time
from typing import Any, Callable, Optional, Type

import httpx
from google.genai import types
from google.genai.errors import APIError
from pydantic import BaseModel, ValidationError

from config.settings import settings
from services import log_service
from services.http_client import fetch
from services.llm_telemetry import (
    record_deepseek_usage,
    record_error,
    record_fallback,
    record_gemini_usage,
)

LLM_LIVE = "LLM_LIVE"
LLM_DJ = "LLM_DJ"
LLM_BACKGROUND = "LLM_BACKGROUND"
LLM_ANNOUNCE = "LLM_ANNOUNCE"
LLM_INTERPRET = "LLM_INTERPRET"

LLM_ERRORS = (httpx.HTTPError, APIError, asyncio.TimeoutError, OSError, RuntimeError, ValueError)

_circuit_state: dict[str, dict[str, Any]] = {}


class LLMCircuitOpen(RuntimeError):
    pass


class LLMInvalidOutput(ValueError):
    pass


def role_label(spec: str) -> str:
    return spec.removeprefix("LLM_").lower()


def resolve_llm(spec: str) -> list[tuple[str, str]]:
    value = getattr(settings, spec, "") or ""
    return [
        (entry.split(":", 1)[0].strip(), entry.split(":", 1)[1].strip())
        for entry in value.split(",")
        if ":" in entry
    ]


def role_timeout(spec: str) -> float:
    return float(getattr(settings, f"{spec}_TIMEOUT_S", settings.LLM_BACKGROUND_TIMEOUT_S))


def _circuit_key(spec: str, provider: str, model: str) -> str:
    return f"{provider}:{model}:{spec}"


def _err_line(err: Exception) -> str:
    lines = str(err).strip().splitlines()
    return f"{type(err).__name__}: {lines[0][:200] if lines else ''}"


def _circuit_open(key: str) -> bool:
    state = _circuit_state.get(key)
    if not state:
        return False
    if state.get("opened_until", 0) <= time.time():
        if state.get("opened_until"):
            _circuit_state.pop(key, None)
            log_service.ai(f"[LLM] {key} circuit half-open after cooldown")
        return False
    return True


def _record_success(key: str) -> None:
    if key in _circuit_state:
        _circuit_state.pop(key, None)
        log_service.ai(f"[LLM] {key} circuit reset after success")


def _record_failure(key: str, err: Exception) -> None:
    now = time.time()
    state = _circuit_state.setdefault(key, {"failures": [], "opened_until": 0, "last_error": ""})
    window_start = now - settings.LLM_CIRCUIT_WINDOW_SECONDS
    failures = [t for t in state.get("failures", []) if t >= window_start]
    failures.append(now)
    state["failures"] = failures
    state["last_error"] = f"{type(err).__name__}: {str(err)[:200]}"
    if len(failures) >= settings.LLM_CIRCUIT_FAILURE_THRESHOLD:
        state["opened_until"] = now + settings.LLM_CIRCUIT_COOLDOWN_SECONDS
        log_service.warning(
            f"[LLM] {key} circuit OPEN - {len(failures)} failures in {settings.LLM_CIRCUIT_WINDOW_SECONDS}s; "
            f"cooldown={settings.LLM_CIRCUIT_COOLDOWN_SECONDS}s; last={state['last_error']}"
        )


def candidates_for(spec: str) -> list[tuple[str, str]]:
    chain = resolve_llm(spec)
    if not chain:
        raise ValueError(f"No LLM chain configured for {spec}")
    available = []
    for provider, model in chain:
        if provider == "deepseek" and not settings.DEEPSEEK_API_KEY:
            continue
        if _circuit_open(_circuit_key(spec, provider, model)):
            log_service.ai(f"[LLM] {provider}:{model} circuit open for {spec} - skipping")
            continue
        available.append((provider, model))
    if not available:
        available = chain[:1]
    if not settings.LLM_FALLBACK_ENABLED:
        available = available[:1]
    return available


def gemini_visible_text(response) -> str:
    candidates = getattr(response, "candidates", None) or []
    if not candidates or not candidates[0].content or not candidates[0].content.parts:
        return ""
    return "".join(p.text for p in candidates[0].content.parts if p.text and not p.thought)


async def deepseek_chat(
    *,
    spec: str,
    model: str,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    json_mode: bool = False,
    timeout: Optional[float] = None,
) -> dict:
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "thinking": {"type": "disabled"},
        "stream": False,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    t0 = time.perf_counter()
    try:
        response = await fetch(
            "POST",
            settings.DEEPSEEK_API_BASE_URL,
            retries=0,
            json=body,
            headers={"Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}"},
            timeout=httpx.Timeout(timeout or role_timeout(spec), connect=5.0),
        )
        if response.status_code != 200:
            log_service.throttled(
                f"deepseek_http:{response.status_code}",
                f"[LLM] DeepSeek HTTP {response.status_code}: {' '.join(response.text[:300].split())}", "error")
            response.raise_for_status()
        data = response.json()
    except LLM_ERRORS:
        record_error(role_label(spec), "deepseek", model, (time.perf_counter() - t0) * 1000)
        raise
    ms = (time.perf_counter() - t0) * 1000
    usage = record_deepseek_usage(role_label(spec), model, data.get("usage"), ms)
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    return {
        "text": (message.get("content") or "").strip(),
        "finish_reason": choice.get("finish_reason"),
        "usage": usage,
        "ms": ms,
    }


async def gemini_generate(
    *,
    spec: str,
    client,
    model: str,
    contents,
    config: types.GenerateContentConfig,
    timeout: Optional[float] = None,
):
    if client is None:
        raise RuntimeError("Gemini client not configured")
    t0 = time.perf_counter()
    try:
        response = await asyncio.wait_for(
            client.aio.models.generate_content(model=model, contents=contents, config=config),
            timeout=timeout or role_timeout(spec),
        )
    except LLM_ERRORS:
        record_error(role_label(spec), "gemini", model, (time.perf_counter() - t0) * 1000)
        raise
    ms = (time.perf_counter() - t0) * 1000
    usage = record_gemini_usage(role_label(spec), model, getattr(response, "usage_metadata", None), ms)
    return response, usage, ms


async def gemini_generate_chain(
    *,
    spec: str,
    client,
    contents,
    config: types.GenerateContentConfig,
    prefer: Optional[str] = None,
):
    models = [model for provider, model in candidates_for(spec) if provider == "gemini"]
    if not models:
        models = [model for provider, model in resolve_llm(spec) if provider == "gemini"][:1]
    if not models:
        raise ValueError(f"No Gemini model configured for {spec}")
    if prefer in models:
        models.remove(prefer)
        models.insert(0, prefer)
    for idx, model in enumerate(models):
        key = _circuit_key(spec, "gemini", model)
        try:
            response, usage, ms = await gemini_generate(spec=spec, client=client, model=model, contents=contents,
                                                        config=config)
        except LLM_ERRORS as err:
            _record_failure(key, err)
            if idx == len(models) - 1:
                log_service.error(f"[LLM] {role_label(spec)} gemini:{model} failed: {_err_line(err)}")
                raise
            record_fallback(role_label(spec), f"gemini:{model}", f"gemini:{models[idx + 1]}", _err_line(err))
            continue
        _record_success(key)
        return response, usage, ms, model
    raise RuntimeError(f"No Gemini model available for {spec}")


async def _generate_once(
    *,
    spec: str,
    provider: str,
    model: str,
    gemini_client,
    prompt: str,
    system: Optional[str],
    temperature: float,
    max_tokens: int,
    json_mode: bool,
    response_schema: Optional[Type[BaseModel]],
    provider_notes: Optional[dict[str, str]] = None,
) -> dict:
    note = (provider_notes or {}).get(provider)
    if note:
        system = f"{system}{note}" if system else note.strip()
    if provider == "deepseek":
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt or ""})
        return await deepseek_chat(
            spec=spec, model=model, messages=messages, temperature=temperature, max_tokens=max_tokens,
            json_mode=json_mode or response_schema is not None,
        )

    if provider == "gemini":
        config_params: dict[str, Any] = {"temperature": temperature, "max_output_tokens": max_tokens}
        if system:
            config_params["system_instruction"] = system
        if json_mode or response_schema is not None:
            config_params["response_mime_type"] = "application/json"
        if response_schema is not None:
            config_params["response_schema"] = response_schema
        response, usage, ms = await gemini_generate(
            spec=spec, client=gemini_client, model=model, contents=prompt,
            config=types.GenerateContentConfig(**config_params),
        )
        candidate = response.candidates[0] if response.candidates else None
        result = {
            "text": gemini_visible_text(response).strip(),
            "finish_reason": str(candidate.finish_reason) if candidate else "NO_CANDIDATE",
            "usage": usage,
            "ms": ms,
        }
        if response_schema is not None and getattr(response, "parsed", None) is not None:
            parsed = response.parsed
            result["parsed"] = parsed if isinstance(parsed, dict) else parsed.model_dump()
        return result

    raise ValueError(f"Unknown LLM provider: {provider}")


async def generate(
    *,
    spec: str,
    prompt: str,
    system: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 2048,
    json_mode: bool = False,
    validate: Optional[Callable[[str], bool]] = None,
    provider_notes: Optional[dict[str, str]] = None,
    gemini_client=None,
    task: str = "",
) -> dict:
    candidates = candidates_for(spec)
    last_err: Optional[Exception] = None
    for idx, (provider, model) in enumerate(candidates):
        is_last = idx == len(candidates) - 1
        key = _circuit_key(spec, provider, model)
        try:
            result = await _generate_once(
                spec=spec, provider=provider, model=model, gemini_client=gemini_client, prompt=prompt,
                system=system, temperature=temperature, max_tokens=max_tokens, json_mode=json_mode,
                response_schema=None, provider_notes=provider_notes,
            )
        except LLM_ERRORS as err:
            last_err = err
            _record_failure(key, err)
            if is_last:
                log_service.error(f"[LLM] {task or role_label(spec)} {provider}:{model} failed: {_err_line(err)}")
                raise
            record_fallback(role_label(spec), f"{provider}:{model}", "%s:%s" % candidates[idx + 1], _err_line(err))
            continue
        _record_success(key)
        result.update({"provider": provider, "model": model})
        text = result["text"]
        valid = bool(text) and (validate is None or validate(text))
        if valid or is_last:
            if not valid:
                log_service.warning(f"[LLM] {task or role_label(spec)} {provider}:{model} output failed validation (no fallback left)")
            return result
        record_fallback(role_label(spec), f"{provider}:{model}", "%s:%s" % candidates[idx + 1],
                        "empty output" if not text else "invalid output")
    if last_err:
        raise last_err
    return {"text": "", "provider": None, "model": None}


def schema_instruction(response_schema: Type[BaseModel]) -> str:
    return (
        "Respond with a single JSON object (no markdown) that conforms to this JSON schema:\n"
        + json.dumps(response_schema.model_json_schema(), ensure_ascii=False)
    )


async def generate_structured(
    *,
    spec: str,
    prompt: str,
    response_schema: Type[BaseModel],
    system: Optional[str] = None,
    temperature: float = 0.3,
    max_tokens: int = 8192,
    gemini_client=None,
    task: str = "",
) -> Optional[dict]:
    candidates = candidates_for(spec)
    last_err: Optional[Exception] = None
    for idx, (provider, model) in enumerate(candidates):
        is_last = idx == len(candidates) - 1
        key = _circuit_key(spec, provider, model)
        provider_system = system
        if provider == "deepseek":
            provider_system = f"{system}\n\n{schema_instruction(response_schema)}" if system else schema_instruction(response_schema)
        try:
            result = await _generate_once(
                spec=spec, provider=provider, model=model, gemini_client=gemini_client, prompt=prompt,
                system=provider_system, temperature=temperature, max_tokens=max_tokens, json_mode=True,
                response_schema=response_schema if provider == "gemini" else None,
            )
            data = result.get("parsed")
            if data is None:
                parsed = parse_llm_json(result["text"])
                if "_error" in parsed:
                    raise LLMInvalidOutput(parsed.get("_parse_error") or parsed["_error"])
                data = response_schema.model_validate(parsed).model_dump()
            _record_success(key)
            return data
        except ValidationError as err:
            last_err = LLMInvalidOutput(str(err)[:300])
        except LLM_ERRORS as err:
            last_err = err
            if not isinstance(err, LLMInvalidOutput):
                _record_failure(key, err)
        if is_last:
            log_service.error(
                f"[LLM] {task or role_label(spec)} structured {provider}:{model} failed: {_err_line(last_err)}")
            raise last_err
        record_fallback(role_label(spec), f"{provider}:{model}", "%s:%s" % candidates[idx + 1], _err_line(last_err))
    if last_err:
        raise last_err
    return None


def _extract_json_substring(text: str) -> str | None:
    openers = "{["
    closers = "}]"
    stack = []
    start = -1
    in_string = False
    escape = False

    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in openers:
            if not stack:
                start = i
            stack.append(ch)
        elif ch in closers:
            if stack and (stack[-1], ch) in [("{", "}"), ("[", "]")]:
                stack.pop()
            if not stack and start >= 0:
                return text[start:i + 1]
    return None


def _strip_trailing_json_commas(text: str) -> str:
    chars = []
    in_string = False
    escape = False
    i = 0
    while i < len(text):
        ch = text[i]
        if escape:
            chars.append(ch)
            escape = False
            i += 1
            continue
        if ch == "\\" and in_string:
            chars.append(ch)
            escape = True
            i += 1
            continue
        if ch == '"':
            in_string = not in_string
            chars.append(ch)
            i += 1
            continue
        if ch == "," and not in_string:
            j = i + 1
            while j < len(text) and text[j].isspace():
                j += 1
            if j < len(text) and text[j] in "}]":
                i += 1
                continue
        chars.append(ch)
        i += 1
    return "".join(chars).strip()


def _normalise_llm_json(data: Any, fallback: dict[str, Any]) -> dict[str, Any]:
    if isinstance(data, dict):
        return data
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    return fallback


def parse_llm_json(text: str | None, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not text:
        return default or {"_error": "Empty response"}

    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    fallback = default or {"_error": "Failed to parse JSON", "_raw": text[:500]}
    for candidate in (text, _extract_json_substring(text)):
        if not candidate:
            continue
        for variant in (candidate, _strip_trailing_json_commas(candidate)):
            try:
                return _normalise_llm_json(json.loads(variant, strict=False), fallback)
            except json.JSONDecodeError as exc:
                fallback = {**fallback, "_parse_error": f"{exc.msg} at char {exc.pos}"}
    return fallback
