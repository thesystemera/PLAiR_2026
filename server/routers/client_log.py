import json
import logging
import re
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from config.settings import settings
from routers.deps import RateLimit, request_identity
from security_middleware import SENSITIVE_QUERY_PATTERN
from services import log_service

MAX_BODY_BYTES = 32 * 1024
MAX_ENTRIES = 20
LOG_FILE_BYTES = 20 * 1024 * 1024
LOG_FILE_COUNT = 10

JWT_PATTERN = re.compile(r"eyJ[\w-]{6,}\.[\w-]{6,}\.[\w-]{6,}")
EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
COORDINATE_PATTERN = re.compile(r"-?\d{1,3}\.\d{4,}")
BEARER_PATTERN = re.compile(r"(Bearer\s+)[^\s\"']+", re.IGNORECASE)

router = APIRouter()
_client_logger: Optional[logging.Logger] = None


class ClientLogEntry(BaseModel):
    kind: str = Field(max_length=32)
    message: str = Field(max_length=1000)
    stack: Optional[str] = Field(default=None, max_length=4096)
    at: Optional[int] = None
    extra: Optional[dict] = None


class ClientLogBatch(BaseModel):
    sid: str = Field(max_length=64)
    device: Optional[str] = Field(default=None, max_length=16)
    build: Optional[str] = Field(default=None, max_length=64)
    ua: Optional[str] = Field(default=None, max_length=300)
    standalone: Optional[bool] = None
    caps: Optional[dict] = None
    path: Optional[str] = Field(default=None, max_length=200)
    breadcrumbs: List[str] = Field(default_factory=list, max_length=40)
    entries: List[ClientLogEntry] = Field(max_length=MAX_ENTRIES)


def redact(text):
    if not isinstance(text, str):
        return text
    text = SENSITIVE_QUERY_PATTERN.sub(r"\1[redacted]", text)
    text = BEARER_PATTERN.sub(r"\1[redacted]", text)
    text = JWT_PATTERN.sub("[token]", text)
    text = EMAIL_PATTERN.sub("[email]", text)
    return COORDINATE_PATTERN.sub("[coord]", text)


def _redact_value(value, depth=0):
    if depth > 3:
        return None
    if isinstance(value, str):
        return redact(value[:500])
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_redact_value(v, depth + 1) for v in value[:20]]
    if isinstance(value, dict):
        return {str(k)[:40]: _redact_value(v, depth + 1) for k, v in list(value.items())[:30]}
    return None


def _logger() -> logging.Logger:
    global _client_logger
    if _client_logger is None:
        logs_dir = Path(settings.LOGS_DIR)
        logs_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(logs_dir / "client.jsonl", maxBytes=LOG_FILE_BYTES,
                                      backupCount=LOG_FILE_COUNT, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        client_logger = logging.getLogger("plair.client")
        client_logger.setLevel(logging.INFO)
        client_logger.propagate = False
        client_logger.addHandler(handler)
        _client_logger = client_logger
    return _client_logger


@router.post("/api/client-log")
async def client_log(request: Request, user=Depends(RateLimit("client_log", user_limit=(60, 3600), guest_limit=(30, 3600)))):
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Log batch too large")
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Log batch too large")
    try:
        batch = ClientLogBatch.model_validate_json(body)
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid log batch")

    identity = request_identity(request, user)
    who = f"user:{user.id}" if user is not None else ("guest" if identity.startswith("guest") else "anon")
    base = {
        "ts": int(time.time() * 1000),
        "who": who,
        "sid": redact(batch.sid),
        "device": redact(batch.device),
        "build": redact(batch.build),
        "ua": redact(batch.ua),
        "standalone": batch.standalone,
        "caps": _redact_value(batch.caps),
        "path": redact(batch.path),
        "breadcrumbs": [redact(b[:300]) for b in batch.breadcrumbs[-30:]],
    }
    log = _logger()
    for entry in batch.entries:
        record = {
            **base,
            "kind": redact(entry.kind),
            "message": redact(entry.message),
            "stack": redact(entry.stack),
            "at": entry.at,
            "extra": _redact_value(entry.extra),
        }
        log.info(json.dumps(record, ensure_ascii=False))
        if entry.kind in ("error", "unhandledrejection"):
            log_service.warning(f"[ClientLog] {who} {redact(entry.kind)}: {redact(entry.message)[:200]}")
    return {"ok": True, "stored": len(batch.entries)}
