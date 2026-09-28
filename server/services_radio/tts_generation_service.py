import asyncio
import aiofiles
import aiofiles.os
import uuid
import json
import random
import os
import io
from pydub import AudioSegment
from mutagen.mp3 import MP3
from mutagen.id3 import ID3
from mutagen.id3._frames import TIT2, TIT3
import re
import time
import itertools
import aiohttp
import soundfile as sf
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, Hashable, List, Optional, Set, Tuple
import traceback

from config.settings import settings
from services import log_service
from services import usage_tracking
from services_radio.tts_processing_service import decode_mp3
from services.task_utils import spawn

PRIORITY_HIGH = "high"
PRIORITY_LOW = "low"

ENGINE_SLOTS = max(1, int(os.getenv("TTS_ENGINE_SLOTS", os.getenv("TTS_NUM_WORKERS", "2"))))
TURN_GENERATION_PARALLEL_START = max(1, int(os.getenv("TTS_TURN_GENERATION_PARALLEL_START", "1")))
TURN_GENERATION_PARALLEL = max(1, int(os.getenv("TTS_TURN_GENERATION_PARALLEL", "2")))
PROCESSING_PARALLEL = max(1, int(os.getenv("TTS_PROCESSING_PARALLEL", "2")))
LOOKUP_PARALLEL = max(1, int(os.getenv("TTS_LOOKUP_PARALLEL", "2")))
RANK_UNRANKED_HIGH = (1,)
RANK_LOW = (2,)
ENGINE_OPTIONS = ("seed", "max_tokens", "top_p")
BREATH_LIBRARY_COUNT_TTL_S = 300

EMBEDDINGS_BY_CONTENT_TYPE = {
    'meta': 'meta_embeddings',
    'impulse': 'impulse_embeddings',
    'sentence': 'tts_embeddings',
    'audio': 'audio_embeddings'
}

FILLER_TYPES = {
    'breath_embeddings': 'breath',
    'tts_embeddings': 'sentence',
    'meta_embeddings': 'meta',
    'impulse_embeddings': 'impulse',
}
EXACT_REFRESH_TYPES = ('tts_embeddings', 'meta_embeddings', 'impulse_embeddings')

INVALID_TITLE_CHARS = re.compile(r'[*\n\[\]@$%"&.!?]|N/A')

def sanitize_clip_title(text: str) -> str:
    return re.sub(r'\s+', ' ', INVALID_TITLE_CHARS.sub(' ', text or '')).strip()[:200]

class PriorityLimiter:
    def __init__(self, capacity: int):
        self.capacity = capacity
        self.active = 0
        self._group_active: Dict[Hashable, int] = {}
        self._group_limits: Dict[Hashable, int] = {}
        self._waiters: List[Tuple[Tuple, int, asyncio.Future, Optional[Hashable]]] = []
        self._order = itertools.count()

    def set_group_limit(self, group: Hashable, limit: Optional[int]):
        if limit is None:
            self._group_limits.pop(group, None)
        else:
            self._group_limits[group] = limit
        self._dispatch()

    def _eligible(self, group: Optional[Hashable]) -> bool:
        if self.active >= self.capacity:
            return False
        limit = self._group_limits.get(group) if group is not None else None
        return limit is None or self._group_active.get(group, 0) < limit

    def _grant(self, group: Optional[Hashable]):
        self.active += 1
        if group is not None:
            self._group_active[group] = self._group_active.get(group, 0) + 1

    def _release(self, group: Optional[Hashable]):
        self.active -= 1
        if group is not None:
            remaining = self._group_active.get(group, 1) - 1
            if remaining > 0:
                self._group_active[group] = remaining
            else:
                self._group_active.pop(group, None)
        self._dispatch()

    def _dispatch(self):
        self._waiters.sort(key=lambda waiter: (waiter[0], waiter[1]))
        for waiter in list(self._waiters):
            future, group = waiter[2], waiter[3]
            if future.done():
                self._waiters.remove(waiter)
                continue
            if self._eligible(group):
                self._waiters.remove(waiter)
                self._grant(group)
                future.set_result(True)
            elif self.active >= self.capacity:
                break

    @asynccontextmanager
    async def slot(self, rank: Tuple, group: Optional[Hashable] = None):
        if self._eligible(group):
            self._grant(group)
        else:
            future = asyncio.get_running_loop().create_future()
            waiter = (rank, next(self._order), future, group)
            self._waiters.append(waiter)
            try:
                await future
            except asyncio.CancelledError:
                if future.done() and not future.cancelled():
                    self._release(group)
                else:
                    if waiter in self._waiters:
                        self._waiters.remove(waiter)
                    self._dispatch()
                raise
        try:
            yield
        finally:
            self._release(group)

class TTSGenerationService:
    def __init__(self, vector_db_service, audio_processing_service, ai_service):
        self.vector_db_service = vector_db_service
        self.audio_processing_service = audio_processing_service
        self.ai_service = ai_service

        self.tts_directory = settings.TTS_AUDIO_DIR
        self.meta_directory = settings.META_AUDIO_DIR
        self.impulse_directory = settings.IMPULSE_AUDIO_DIR
        self.breath_directory = settings.BREATH_AUDIO_DIR
        self.audio_directory = os.path.join(settings.AUDIO_EFFECT_DIR, 'computer')
        self.engine_slots = PriorityLimiter(ENGINE_SLOTS)
        self.processing_slots = PriorityLimiter(PROCESSING_PARALLEL)
        self.lookup_slots = PriorityLimiter(LOOKUP_PARALLEL)
        self._http_session: Optional[aiohttp.ClientSession] = None
        self._low_priority_gate = asyncio.Event()
        self._low_priority_gate.set()
        self._live_turns = 0
        self._owner_jobs: Dict[str, Set[str]] = {}
        self._aborted_jobs: Set[str] = set()
        self._background_semaphore = asyncio.Semaphore(settings.TTS_BACKGROUND_CONCURRENCY)
        self._pending_refreshes: Set[Tuple[str, str, str]] = set()
        self._recent_refreshes: Dict[Tuple[str, str, str], float] = {}
        self._breath_refresh_times: Dict[str, List[float]] = {}
        self._exact_refresh_times: Dict[str, List[float]] = {}
        self._breath_library_counts: Dict[str, Tuple[float, int]] = {}

        self.metrics = {
            'hits': 0,
            'misses': 0
        }

        self._initialize_directories()

    def _initialize_directories(self):
        voices = ['tara', 'leo']

        for voice in voices:
            os.makedirs(os.path.join(self.tts_directory, voice), exist_ok=True)
            os.makedirs(os.path.join(self.meta_directory, voice), exist_ok=True)
            os.makedirs(os.path.join(self.impulse_directory, voice), exist_ok=True)
            os.makedirs(os.path.join(self.breath_directory, voice), exist_ok=True)

        os.makedirs(self.audio_directory, exist_ok=True)

        log_service.detail("TTS Generation: Initialized all audio directories", "tts_generation")

    def _log_metrics(self, event_type: str):
        total = self.metrics['hits'] + self.metrics['misses']
        ratio = (self.metrics['hits'] / total * 100) if total > 0 else 0

        log_service.detail(
            f"STATS [{event_type.upper()}]: Hits: {self.metrics['hits']} | Misses: {self.metrics['misses']} | Vector Efficiency: {ratio:.1f}%",
            "tts_generation"
        )

    def get_voice_directory(self, base_dir: str, voice: str) -> str:
        return os.path.join(base_dir, voice)

    def _http(self) -> aiohttp.ClientSession:
        if self._http_session is None or self._http_session.closed:
            self._http_session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=settings.TTS_REQUEST_TIMEOUT)
            )
        return self._http_session

    async def close(self):
        if self._http_session is not None and not self._http_session.closed:
            await self._http_session.close()
        self._http_session = None

    def open_turn(self, group: Hashable):
        self.engine_slots.set_group_limit(group, TURN_GENERATION_PARALLEL_START)

    def widen_turn(self, group: Hashable):
        self.engine_slots.set_group_limit(group, TURN_GENERATION_PARALLEL)

    def close_turn(self, group: Hashable):
        self.engine_slots.set_group_limit(group, None)

    @asynccontextmanager
    async def background_slot(self):
        async with self._background_semaphore:
            await self._low_priority_gate.wait()
            yield

    def low_priority_open(self) -> bool:
        return self._low_priority_gate.is_set()

    def block_low_priority(self):
        self._live_turns += 1
        self._low_priority_gate.clear()

    def allow_low_priority(self):
        self._live_turns = max(0, self._live_turns - 1)
        if self._live_turns == 0:
            self._low_priority_gate.set()

    def _register_job(self, owner: Optional[str], job_id: Optional[str]):
        if owner and job_id:
            self._owner_jobs.setdefault(owner, set()).add(job_id)

    def _release_job(self, owner: Optional[str], job_id: Optional[str]):
        if owner and job_id:
            jobs = self._owner_jobs.get(owner)
            if jobs is not None:
                jobs.discard(job_id)
                if not jobs:
                    del self._owner_jobs[owner]

    def owner_jobs(self, owner: str) -> List[str]:
        return list(self._owner_jobs.get(owner, set()))

    async def abort_jobs(self, owner: str, job_ids: List[str]):
        active_jobs = {job for jobs in self._owner_jobs.values() for job in jobs}
        self._aborted_jobs.update(job_id for job_id in job_ids if job_id in active_jobs)
        for job_id in job_ids:
            try:
                async with self._http().post(f"{settings.TTS_SERVER_URL}/abort/{job_id}") as response:
                    if response.status == 404:
                        self._aborted_jobs.discard(job_id)
                        log_service.detail(f"TTS abort: job {job_id} not abortable (finished or unsupported)", "tts_generation")
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                log_service.error(f"TTS abort failed for job {job_id}: {e}")
        if job_ids:
            log_service.detail(f"TTS abort: cancelled {len(job_ids)} job(s) for {log_service.who(owner)}", "tts_generation")

    async def _stream_engine(
            self,
            sentence: str,
            voice_settings: dict,
            job: Dict,
            priority: str = PRIORITY_HIGH,
            owner: Optional[str] = None,
            rank: Optional[Tuple] = None,
            group: Optional[Hashable] = None
    ) -> AsyncIterator[bytes]:
        if priority == PRIORITY_LOW:
            await self._low_priority_gate.wait()
            rank = RANK_LOW
        elif rank is None:
            rank = RANK_UNRANKED_HIGH

        payload = {
            "text": sentence,
            "voice": voice_settings["orpheus_voice"],
            "temperature": voice_settings["temperature"],
        }
        for option in ENGINE_OPTIONS:
            if voice_settings.get(option) is not None:
                payload[option] = voice_settings[option]

        job_id = None
        try:
            async with self.engine_slots.slot(rank, group):
                job['started'] = time.perf_counter()
                async with self._http().post(f"{settings.TTS_SERVER_URL}/tts", json=payload) as response:
                    if response.status != 200:
                        job['failed'] = True
                        log_service.error(f"TTS engine error {response.status}: {await response.text()}")
                        return
                    job_id = response.headers.get("X-Job-Id")
                    job['id'] = job_id
                    self._register_job(owner, job_id)
                    carry = b""
                    async for chunk in response.content.iter_any():
                        if carry:
                            chunk = carry + chunk
                            carry = b""
                        if len(chunk) % 2:
                            carry = chunk[-1:]
                            chunk = chunk[:-1]
                        if chunk:
                            yield chunk
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            job['failed'] = True
            log_service.error(f"TTS engine unreachable at {settings.TTS_SERVER_URL}: {e}")
        finally:
            self._release_job(owner, job_id)

    async def generate_pcm(
            self,
            sentence: str,
            voice_settings: dict,
            priority: str = PRIORITY_HIGH,
            owner: Optional[str] = None,
            rank: Optional[Tuple] = None,
            group: Optional[Hashable] = None,
            feature: str = "tts.orpheus"
    ) -> Optional[bytes]:
        pcm = bytearray()
        job: Dict = {}
        async for chunk in self._stream_engine(sentence, voice_settings, job, priority, owner, rank, group):
            pcm.extend(chunk)

        if 'started' in job:
            usage_tracking.record_gpu(feature, time.perf_counter() - job['started'],
                                      audio_seconds=len(pcm) / 2 / settings.TTS_SAMPLE_RATE, model="orpheus-3b",
                                      error=bool(job.get('failed')))
        job_id = job.get('id')
        if job_id is not None and job_id in self._aborted_jobs:
            self._aborted_jobs.discard(job_id)
            log_service.detail(f"TTS job {job_id} aborted - discarding {len(pcm)} bytes of partial audio", "tts_generation")
            return None
        if job.get('failed'):
            return None
        if not pcm:
            log_service.error(f"TTS engine returned no audio for: {sentence[:60]}")
            return None
        return bytes(pcm)

    async def generate_local_tts(
            self,
            sentence: str,
            voice_settings: dict,
            priority: str = PRIORITY_HIGH,
            owner: Optional[str] = None
    ) -> Optional[bytes]:
        pcm = await self.generate_pcm(sentence, voice_settings, priority, owner)
        if pcm is None:
            return None
        return await asyncio.to_thread(self._pcm_to_mp3, pcm, settings.TTS_SAMPLE_RATE)

    @staticmethod
    def _pcm_to_mp3(pcm: bytes, sample_rate: int) -> bytes:
        segment = AudioSegment(data=pcm[:len(pcm) - len(pcm) % 2], sample_width=2, frame_rate=sample_rate, channels=1)
        buffer = io.BytesIO()
        segment.export(buffer, format="mp3", bitrate="192k")
        return buffer.getvalue()

    def _tag_audio_sync(self, audio_path, tag, audio_description):
        try:
            audio = MP3(audio_path, ID3=ID3)
            if audio.tags is None:
                audio.add_tags()
            if audio.tags is not None:
                audio.tags.add(TIT2(encoding=3, text=tag))
                if audio_description:
                    audio.tags.add(TIT3(encoding=3, text=audio_description))
            audio.save()
            return True
        except Exception as e:
            log_service.error(f"Tagging failed: {e}")
            return False

    async def _save_audio_and_embedding(self, audio_path, audio_data, tag, audio_description, content_voice,
                                        embeddings_type):
        try:
            directory = os.path.dirname(audio_path)
            if not os.path.exists(directory):
                os.makedirs(directory, exist_ok=True)

            async with aiofiles.open(audio_path, 'wb') as f:
                await f.write(audio_data)

            await asyncio.to_thread(self._tag_audio_sync, audio_path, tag, audio_description)

            await asyncio.to_thread(
                self.vector_db_service.save_embedding,
                audio_path, tag, content_voice, embeddings_type
            )
            log_service.detail(
                f"Clip cache: saved {embeddings_type.removesuffix('_embeddings')} clip for {content_voice} "
                f"'{tag[:40]}' ({os.path.basename(audio_path)})", "tts_generation")
        except Exception as e:
            log_service.error(f"Background save failed: {e} {traceback.format_exc()}")

    async def save_generated_clip(self, pcm: bytes, audio_path: str, tag: str, audio_description: Optional[str],
                                  content_voice: str, embeddings_type: str):
        try:
            audio_data = await asyncio.to_thread(self._pcm_to_mp3, pcm, settings.TTS_SAMPLE_RATE)
        except Exception as e:
            log_service.error(f"Background MP3 encode failed for {audio_path}: {e}")
            return
        await self._save_audio_and_embedding(audio_path, audio_data, tag, audio_description, content_voice,
                                             embeddings_type)

    def clip_directory(self, embeddings_type: str, content_voice: str) -> Optional[str]:
        if embeddings_type == 'tts_embeddings':
            return self.get_voice_directory(str(self.tts_directory), content_voice)
        if embeddings_type == 'meta_embeddings':
            return self.get_voice_directory(str(self.meta_directory), content_voice)
        if embeddings_type == 'impulse_embeddings':
            return self.get_voice_directory(str(self.impulse_directory), content_voice)
        if embeddings_type == 'breath_embeddings':
            return self.get_voice_directory(str(self.breath_directory), content_voice)
        if embeddings_type == 'audio_embeddings':
            return self.audio_directory
        return None

    @staticmethod
    def similarity_threshold(embeddings_type: str) -> float:
        return {
            'audio_embeddings': settings.AUDIO_SIMILARITY_THRESHOLD,
            'meta_embeddings': settings.META_SIMILARITY_THRESHOLD,
            'impulse_embeddings': settings.IMPULSE_SIMILARITY_THRESHOLD,
            'breath_embeddings': settings.BREATH_SIMILARITY_THRESHOLD,
        }.get(embeddings_type, settings.TTS_SIMILARITY_THRESHOLD)

    async def resolve_breath_clip(self, context: str, content_voice: str,
                                  rank: Tuple = RANK_UNRANKED_HIGH) -> Optional[str]:
        cached = await self.lookup_clip(context, 'breath_embeddings', content_voice, float('-inf'), rank)
        if cached is None:
            self.schedule_refresh('breath_embeddings', content_voice, context)
            fallback = await self.find_random_breath_sound(content_voice)
            log_service.detail(
                f"Breath: no eligible cached breath for {content_voice} - random fallback "
                f"{os.path.basename(fallback) if fallback else 'none'}", "tts_generation")
            return fallback

        cached_file_path, similarity = cached
        if similarity < settings.BREATH_SIMILARITY_THRESHOLD:
            self.schedule_refresh('breath_embeddings', content_voice, context)
        log_service.detail(
            f"Breath: '{context[:40]}' -> {os.path.basename(cached_file_path)} (sim={similarity:.2f})", "tts_generation")
        return cached_file_path

    async def find_random_breath_sound(self, voice_name: str) -> Optional[str]:
        voice_breath_directory = self.get_voice_directory(str(self.breath_directory), voice_name)

        if not await aiofiles.os.path.exists(voice_breath_directory):
            return None

        cache_file = os.path.join(voice_breath_directory, f'{voice_name}_breath_sounds_cache.json')

        if await aiofiles.os.path.exists(cache_file):
            async with aiofiles.open(cache_file, 'r') as f:
                content = await f.read()
                breath_files = json.loads(content)
        else:
            breath_files = [f for f in await asyncio.to_thread(os.listdir, voice_breath_directory) if
                            f.endswith('.mp3')]
            async with aiofiles.open(cache_file, 'w') as f:
                await f.write(json.dumps(breath_files))

        if not breath_files:
            return None

        chosen_file = random.choice(breath_files)
        return os.path.join(voice_breath_directory, chosen_file)

    def schedule_refresh(self, embeddings_type: str, content_voice: str, tag: str):
        permission_key = FILLER_TYPES.get(embeddings_type)
        if permission_key is None or content_voice not in settings.GENERATION_PERMISSIONS.get(permission_key, set()):
            return

        key = (embeddings_type, content_voice, tag.strip().lower()[:200])
        now = time.time()
        cooldown = settings.TTS_BACKGROUND_REFRESH_COOLDOWN_S
        self._recent_refreshes = {k: t for k, t in self._recent_refreshes.items() if now - t < cooldown}
        if key in self._recent_refreshes or key in self._pending_refreshes:
            return
        if len(self._pending_refreshes) >= settings.TTS_BACKGROUND_MAX_PENDING:
            return
        if embeddings_type == 'breath_embeddings':
            if not self._breath_refresh_allowed(content_voice, now):
                return
            self._breath_refresh_times.setdefault(content_voice, []).append(now)
        elif embeddings_type in EXACT_REFRESH_TYPES:
            recent = [t for t in self._exact_refresh_times.get(embeddings_type, []) if now - t < 3600]
            if len(recent) >= settings.TTS_EXACT_REFRESH_MAX_PER_HOUR:
                self._exact_refresh_times[embeddings_type] = recent
                return
            recent.append(now)
            self._exact_refresh_times[embeddings_type] = recent

        self._recent_refreshes[key] = now
        self._pending_refreshes.add(key)
        spawn(self._refresh_clip(key, embeddings_type, content_voice, tag), name=f"tts_refresh:{permission_key}")

    def _breath_refresh_allowed(self, content_voice: str, now: float) -> bool:
        recent = [t for t in self._breath_refresh_times.get(content_voice, []) if now - t < 3600]
        self._breath_refresh_times[content_voice] = recent
        if len(recent) >= settings.BREATH_REFRESH_MAX_PER_HOUR:
            return False
        return self._breath_library_size(content_voice, now) < settings.BREATH_LIBRARY_TARGET_CLIPS

    def _breath_library_size(self, content_voice: str, now: float) -> int:
        cached = self._breath_library_counts.get(content_voice)
        if cached and now - cached[0] < BREATH_LIBRARY_COUNT_TTL_S:
            return cached[1]
        directory = self.clip_directory('breath_embeddings', content_voice)
        try:
            count = sum(1 for name in os.listdir(directory) if name.endswith('.mp3')) if directory else 0
        except OSError:
            count = 0
        self._breath_library_counts[content_voice] = (now, count)
        return count

    def note_cache_match(self, embeddings_type: str, content_voice: str, tag: str, similarity: float):
        if not settings.TTS_EXACT_REFRESH_ENABLED or embeddings_type not in EXACT_REFRESH_TYPES:
            return
        if similarity >= settings.TTS_EXACT_REFRESH_BELOW:
            return
        self.schedule_refresh(embeddings_type, content_voice, tag)

    async def _filler_description(self, embeddings_type: str, tag: str, content_voice: str) -> Optional[str]:
        if embeddings_type in EXACT_REFRESH_TYPES:
            return await self.generation_description(tag, embeddings_type, content_voice)
        if embeddings_type != 'breath_embeddings':
            return None
        result = await self.ai_service.generate_breath_gpt_response(tag)
        if not result or not result[1] or result[1] == "N/A":
            return None
        return result[1]

    async def _refresh_clip(self, key, embeddings_type: str, content_voice: str, tag: str):
        usage_tracking.bind(usage_tracking.system_subject("tts_library"))
        try:
            async with self._background_semaphore:
                await self._low_priority_gate.wait()
                voice_settings = settings.VOICE_PREFERENCES.get(content_voice)
                if not voice_settings:
                    return
                description = await self._filler_description(embeddings_type, tag, content_voice)
                if not description:
                    return
                audio_data = await self.generate_local_tts(description, voice_settings, priority=PRIORITY_LOW)
                if not audio_data:
                    return
                title = sanitize_clip_title(tag) if embeddings_type == 'breath_embeddings' else tag
                directory = self.clip_directory(embeddings_type, content_voice)
                audio_path = os.path.join(directory, f'{uuid.uuid4()}.mp3')
                await self._save_audio_and_embedding(
                    audio_path, audio_data, title or 'breath', description, content_voice, embeddings_type
                )
                log_service.detail(f"Background refresh: cached new {embeddings_type} clip for '{tag[:40]}'", "tts_generation")
                counted = self._breath_library_counts.get(content_voice)
                if embeddings_type == 'breath_embeddings' and counted:
                    self._breath_library_counts[content_voice] = (counted[0], counted[1] + 1)
        except Exception as e:
            log_service.error(f"Background refresh failed for {embeddings_type}: {e}")
        finally:
            self._pending_refreshes.discard(key)

    async def process_clip(
            self,
            audio_input,
            content_voice: str,
            audio_process_mix: float = 0.0,
            previous_segment_end_mix: Optional[float] = None,
            next_segment_start_mix: Optional[float] = None,
            rank: Tuple = RANK_UNRANKED_HIGH
    ) -> AudioSegment:
        async with self.processing_slots.slot(rank):
            return await asyncio.to_thread(
                self.audio_processing_service.process_audio,
                audio_input,
                audio_process_mix,
                previous_segment_end_mix,
                next_segment_start_mix,
                speaker=content_voice
            )

    async def load_clip(
            self,
            file_path: str,
            content_voice: str,
            audio_process_mix: float = 0.0,
            previous_segment_end_mix: Optional[float] = None,
            next_segment_start_mix: Optional[float] = None,
            process: bool = True,
            rank: Tuple = RANK_UNRANKED_HIGH
    ) -> Optional[AudioSegment]:
        try:
            async with aiofiles.open(file_path, 'rb') as f:
                audio_data = await f.read()
        except OSError as e:
            log_service.error(f"Failed to read cached clip {file_path}: {e}")
            return None
        if not process:
            return await asyncio.to_thread(decode_mp3, audio_data)
        return await self.process_clip(
            audio_data, content_voice, audio_process_mix, previous_segment_end_mix, next_segment_start_mix, rank
        )

    @staticmethod
    async def clip_rate(file_path: str) -> Optional[int]:
        try:
            info = await asyncio.to_thread(sf.info, file_path)
            return int(info.samplerate)
        except Exception:
            return None

    async def lookup_clip(
            self,
            tag: str,
            embeddings_type: str,
            content_voice: str,
            threshold: float,
            rank: Tuple = RANK_UNRANKED_HIGH
    ) -> Optional[Tuple[str, float]]:
        directory = self.clip_directory(embeddings_type, content_voice)
        if directory is None:
            return None

        async with self.lookup_slots.slot(rank):
            matches = await asyncio.to_thread(
                self.vector_db_service.query_embeddings,
                tag, content_voice, embeddings_type, 5
            )

        for filename, _title, similarity in matches:
            if similarity < threshold:
                break
            cached_file_path = os.path.join(directory, filename)
            if await aiofiles.os.path.exists(cached_file_path):
                return cached_file_path, float(similarity)
            log_service.warning(f"Cached file not found, removing stale embedding: {cached_file_path}")
            await asyncio.to_thread(self.vector_db_service.delete_embedding, filename, embeddings_type)

        return None

    async def generation_description(self, tag: str, embeddings_type: str, content_voice: str) -> Optional[str]:
        if embeddings_type == 'meta_embeddings':
            result = await self.ai_service.generate_meta_data_gpt_response(tag)
            return result[1] if result and result[1] != "N/A" else None
        if embeddings_type == 'impulse_embeddings':
            result = await self.ai_service.generate_impulse_gpt_response(tag, content_voice)
            return result[1] if result and result[1] != "N/A" else tag
        return tag

    async def render_clip(
            self,
            tag: str,
            embeddings_type: str,
            content_voice: str,
            cached_file_path: Optional[str],
            audio_process_mix: float = 0.0,
            previous_segment_end_mix: Optional[float] = None,
            next_segment_start_mix: Optional[float] = None,
            can_generate: bool = False,
            owner: Optional[str] = None,
            rank: Tuple = RANK_UNRANKED_HIGH,
            group: Optional[Hashable] = None,
            before_generation=None
    ) -> Tuple[Optional[AudioSegment], Optional[str]]:
        directory = self.clip_directory(embeddings_type, content_voice)
        if directory is None:
            log_service.error(f"Unknown embeddings type: {embeddings_type}")
            return None, None

        if cached_file_path:
            processed_audio = await self.load_clip(
                cached_file_path, content_voice, audio_process_mix, previous_segment_end_mix, next_segment_start_mix,
                rank=rank
            )
            if processed_audio is not None:
                self.metrics['hits'] += 1
                self._log_metrics("hit")
                usage_tracking.record_gpu(f"tts.clip_cache.{embeddings_type.removesuffix('_embeddings')}", 0.0,
                                          audio_seconds=len(processed_audio) / 1000, model="orpheus-3b",
                                          cache_hit=True)
                return processed_audio, cached_file_path

        if not can_generate:
            if embeddings_type == 'impulse_embeddings':
                log_service.detail(
                    "Impulse: No suitable impulse found in vector DB and generation not permitted.", "tts_generation")
            return None, None

        if embeddings_type not in ('tts_embeddings', 'meta_embeddings', 'impulse_embeddings'):
            return None, None

        voice_settings = settings.VOICE_PREFERENCES.get(content_voice)
        if not voice_settings:
            raise ValueError(f"Voice settings for {content_voice} not found.")

        self.metrics['misses'] += 1
        self._log_metrics("miss")

        audio_description = await self.generation_description(tag, embeddings_type, content_voice)
        if not audio_description:
            return None, None

        if before_generation is not None:
            await before_generation()
        pcm = await self.generate_pcm(audio_description, voice_settings, PRIORITY_HIGH, owner, rank, group)
        if pcm is None:
            return None, None

        raw_audio = AudioSegment(data=pcm, sample_width=2, frame_rate=settings.TTS_SAMPLE_RATE, channels=1)
        processed_audio = await self.process_clip(
            raw_audio, content_voice, audio_process_mix, previous_segment_end_mix, next_segment_start_mix, rank
        )

        audio_path = os.path.join(directory, f'{uuid.uuid4()}.mp3')
        spawn(self.save_generated_clip(
            pcm, audio_path, tag, audio_description, content_voice, embeddings_type
        ), name="tts_save_audio_and_embedding")

        return processed_audio, audio_path
