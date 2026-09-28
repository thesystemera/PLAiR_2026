import httpx
import time
import asyncio
import aiofiles
import random
from typing import Optional, Dict, Any, Callable
from pathlib import Path
from services import log_service
from services.base_service import SingletonService
from services.http_client import fetch
from config import settings

SUNO_HTTP_TIMEOUT = httpx.Timeout(60.0, connect=5.0)
SUNO_POLL_MAX_BACKOFF_S = 60
SUNO_TRANSIENT_CODES = {430, 455}

class SunoAPIError(Exception):
    def __init__(self, code: Optional[int], msg: str, http_status: Optional[int] = None):
        self.code = code
        self.msg = msg or ""
        self.http_status = http_status
        super().__init__(f"Suno API error (code={code}): {self.msg}")

    @property
    def is_insufficient_credits(self) -> bool:
        lowered = self.msg.lower()
        if self.code == 429:
            return True
        return "credit" in lowered and ("insufficient" in lowered or "not enough" in lowered)

class SunoTransientError(Exception):
    pass

class SunoSubmitUnconfirmed(SunoAPIError):
    pass

SUBMIT_NEVER_SENT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout, httpx.UnsupportedProtocol)
SUBMIT_AMBIGUOUS_ERRORS = (httpx.TimeoutException, httpx.TransportError, SunoTransientError)

def _parse_json_response(response: httpx.Response) -> Dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        if response.status_code >= 500 or response.status_code == 429:
            raise SunoTransientError(f"HTTP {response.status_code} with non-JSON body")
        raise SunoAPIError(None, response.text[:300], http_status=response.status_code)
    if not isinstance(data, dict):
        raise SunoAPIError(None, f"Unexpected response payload: {str(data)[:300]}", http_status=response.status_code)
    return data

class SunoAPI:
    def __init__(self, api_key: str, base_url: str = None):
        self.api_key = api_key
        self.base_url = base_url or settings.SUNO_BASE_URL
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

    async def generate_music(
            self,
            prompt: str,
            custom_mode: bool = False,
            style: Optional[str] = None,
            title: Optional[str] = None,
            instrumental: bool = False,
            model: str = settings.SUNO_MODEL_VERSION,
            negative_tags: Optional[str] = None,
            vocal_gender: Optional[str] = None,
            style_weight: Optional[float] = None,
            weirdness: Optional[float] = None,
            audio_weight: Optional[float] = None
    ) -> Dict[str, Any]:
        payload = {
            "prompt": prompt,
            "customMode": custom_mode,
            "instrumental": instrumental,
            "model": model,
            "callBackUrl": settings.SUNO_CALLBACK_URL
        }

        if custom_mode:
            if not style or not title:
                raise ValueError("style and title required in custom mode")
            payload.update({"style": style, "title": title})

        if negative_tags:
            payload["negativeTags"] = negative_tags
        if vocal_gender:
            payload["vocalGender"] = vocal_gender
        if style_weight is not None:
            payload["styleWeight"] = style_weight
        if weirdness is not None:
            payload["weirdnessConstraint"] = weirdness
        if audio_weight is not None:
            payload["audioWeight"] = audio_weight

        log_service.api(f"Submitting music generation: {title or 'Untitled'}")

        response = await fetch(
            "POST",
            f"{self.base_url}/generate",
            retries=0,
            headers=self.headers,
            json=payload,
            timeout=SUNO_HTTP_TIMEOUT
        )

        return _parse_json_response(response)

    async def get_credits(self) -> Optional[float]:
        response = await fetch(
            "GET",
            f"{self.base_url}/generate/credit",
            headers=self.headers,
            timeout=SUNO_HTTP_TIMEOUT
        )
        data = _parse_json_response(response)
        credits = data.get("data")
        if data.get("code") != 200 or isinstance(credits, bool) or not isinstance(credits, (int, float)):
            return None
        return float(credits)

    async def get_task_status(self, task_id: str) -> Dict[str, Any]:
        response = await fetch(
            "GET",
            f"{self.base_url}/generate/record-info?taskId={task_id}",
            headers=self.headers,
            timeout=SUNO_HTTP_TIMEOUT
        )
        if response.status_code >= 500:
            raise SunoTransientError(f"HTTP {response.status_code} from Suno status endpoint")
        return _parse_json_response(response)

    async def poll_until_complete(
            self,
            task_id: str,
            interval: int = None,
            max_wait: int = None,
            status_callback=None
    ) -> Optional[Dict[str, Any]]:
        if interval is None:
            interval = settings.SUNO_POLL_INTERVAL
        if max_wait is None:
            max_wait = settings.SUNO_MAX_WAIT
        log_service.system(f"Polling task {task_id}...")
        start_time = time.time()
        backoff = 0.0
        consecutive_errors = 0

        while True:
            await asyncio.sleep(interval + random.uniform(0, 3) + backoff)

            if time.time() - start_time > max_wait:
                log_service.error(f"Task {task_id} timed out after {max_wait}s")
                return None

            try:
                result = await self.get_task_status(task_id)
            except (httpx.TransportError, httpx.TimeoutException, SunoTransientError) as e:
                consecutive_errors += 1
                backoff = min(SUNO_POLL_MAX_BACKOFF_S, 2 ** consecutive_errors)
                log_service.warning(
                    f"Transient error polling task {task_id} ({type(e).__name__}: {e}) - "
                    f"retrying in ~{interval + backoff:.0f}s (error {consecutive_errors})"
                )
                continue

            code = result.get("code")

            if code == 430:
                log_service.warning("Rate limit hit (430) - backing off 30 seconds...")
                await asyncio.sleep(30)
                continue

            if code in SUNO_TRANSIENT_CODES or (isinstance(code, int) and code >= 500):
                consecutive_errors += 1
                backoff = min(SUNO_POLL_MAX_BACKOFF_S, 2 ** consecutive_errors)
                log_service.warning(
                    f"Suno status endpoint returned code {code} ({result.get('msg')}) for task {task_id} - retrying"
                )
                continue

            if code != 200:
                log_service.error(f"Error: {result.get('msg')}")
                log_service.error(f"Full response: {result}")
                return None

            consecutive_errors = 0
            backoff = 0.0

            status = (result.get("data") or {}).get("status")
            if status is None:
                log_service.warning(f"Task {task_id} status missing from response - continuing to poll")
                continue
            log_service.system(f"Task {task_id} Status: {status}")

            if status_callback:
                await status_callback(status)

            if status == "SUCCESS":
                log_service.success(f"Task {task_id} completed successfully")
                return result["data"]
            elif status == "FAILED":
                log_service.error(f"Task {task_id} failed")
                log_service.error(f"Full response: {result}")
                return {"error": "FAILED", "details": result}
            elif status == "SENSITIVE_WORD_ERROR":
                log_service.error(f"Task {task_id} rejected - sensitive word detected")
                log_service.error(f"Full API response: {result}")
                log_service.error(f"Full data object: {result.get('data')}")
                return {"error": "SENSITIVE_WORD_ERROR", "details": result}

class SunoService(SingletonService):
    def __init__(self):
        super().__init__()
        if self._initialized:
            return

        self.suno_api = None
        self._initialized = True

    async def initialize(self):
        suno_key = settings.load_api_key_from_file("SUNO_API_KEY")

        if not suno_key:
            log_service.error("Suno API key not found")
            return

        self.suno_api = SunoAPI(suno_key)
        log_service.system("SunoService initialized - Suno API ready")

    async def submit_task(self, music_params: Dict[str, Any]) -> Dict[str, Any]:
        if not self.suno_api:
            log_service.error("Suno API not initialized")
            raise SunoAPIError(None, "Suno API not initialized")

        try:
            title = music_params.get("title")
            if title and len(title) > 80:
                original_title = title
                title = title[:80].rsplit(' ', 1)[0]
                log_service.warning(f"Title truncated (>80 chars): '{original_title}' -> '{title}'")

            if not title and music_params.get("custom_mode"):
                title = "Untitled"
                log_service.warning("Custom mode track missing title, setting to 'Untitled'")

            result = await self.suno_api.generate_music(
                prompt=music_params.get("prompt"),
                custom_mode=music_params.get("custom_mode", False),
                style=music_params.get("style"),
                title=title,
                instrumental=music_params.get("instrumental", False),
                model=settings.SUNO_MODEL_VERSION,
                negative_tags=music_params.get("negative_tags"),
                vocal_gender=music_params.get("vocal_gender"),
                style_weight=music_params.get("style_weight"),
                weirdness=music_params.get("weirdness"),
                audio_weight=music_params.get("audio_weight")
            )

            code = result.get("code")
            msg = str(result.get("msg") or "")
            if code != 200:
                log_service.error(f"Generation failed: {msg}")
                if "title cannot exceed 80 characters" in msg:
                    log_service.error("Truncation may have failed, please check logic.")
                raise SunoAPIError(code, msg)

            task_id = (result.get("data") or {}).get("taskId")
            if not task_id:
                raise SunoAPIError(code, f"Suno response missing taskId: {str(result)[:300]}")

            log_service.system(f"Task submitted: {task_id}")
            return result

        except SunoAPIError:
            raise
        except SUBMIT_NEVER_SENT_ERRORS as e:
            log_service.error(f"Error submitting task (request never reached Suno): {str(e)}")
            raise SunoAPIError(None, f"{type(e).__name__}: {e}") from e
        except SUBMIT_AMBIGUOUS_ERRORS as e:
            log_service.error(f"Suno submit outcome unknown ({type(e).__name__}): {str(e)}")
            raise SunoSubmitUnconfirmed(None, f"{type(e).__name__}: {e}") from e
        except Exception as e:
            log_service.error(f"Error submitting task: {str(e)}")
            raise SunoAPIError(None, f"{type(e).__name__}: {e}") from e

    async def get_credits(self) -> Optional[float]:
        if not self.suno_api:
            return None
        try:
            return await self.suno_api.get_credits()
        except Exception as e:
            log_service.warning(f"Could not read Suno credits: {type(e).__name__}: {e}")
            return None

    async def await_task(self, task_id: str, status_callback: Optional[Callable] = None) -> Optional[Dict[str, Any]]:
        if not self.suno_api:
            log_service.error("Suno API not initialized")
            return None

        try:
            data = await self.suno_api.poll_until_complete(task_id, status_callback=status_callback)

            if not data:
                return None

            if isinstance(data, dict) and "error" in data:
                return data

            tracks = None
            if "response" in data and isinstance(data["response"], dict):
                tracks = data["response"].get("sunoData")

            if not tracks or not isinstance(tracks, list):
                log_service.error("Could not find tracks in response")
                return None

            return {
                "task_id": task_id,
                "tracks": tracks
            }

        except Exception as e:
            log_service.error(f"Error polling task {task_id}: {str(e)}")
            return None

    async def download_track(self, audio_url: str, output_path: Path) -> bool:
        try:
            log_service.system(f"Downloading track to {output_path}")

            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36",
                "Referer": "https://suno.com/"
            }

            response = await fetch(
                "GET",
                audio_url,
                headers=headers,
                timeout=SUNO_HTTP_TIMEOUT
            )

            response.raise_for_status()

            async with aiofiles.open(output_path, 'wb') as f:
                await f.write(response.content)

            log_service.success(f"Downloaded: {output_path}")
            return True

        except Exception as e:
            log_service.error(f"Error downloading track: {str(e)}")
            return False

    async def download_image(self, image_url: str, output_path: Path) -> bool:
        try:
            log_service.system(f"Downloading artwork to {output_path}")

            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36",
                "Referer": "https://suno.com/"
            }

            response = await fetch(
                "GET",
                image_url,
                headers=headers,
                timeout=SUNO_HTTP_TIMEOUT
            )

            response.raise_for_status()

            async with aiofiles.open(output_path, 'wb') as f:
                await f.write(response.content)

            log_service.success(f"Downloaded: {output_path}")
            return True

        except Exception as e:
            log_service.error(f"Error downloading image: {str(e)}")
            return False
