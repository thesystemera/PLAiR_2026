import asyncio
import base64
import bisect
import time
from typing import Dict, List, Optional, Tuple

import numpy as np
import soxr
from pydub import AudioSegment

from config.settings import settings
from services import log_service
from services_radio import station_blips
from services_radio.tts_voice_threads import voice_thread

ENCODER_SAMPLE_RATE = 48000
ENCODER_BITRATE = "128k"
EMIT_INTERVAL_S = 0.05
MONO_UPMIX_GAIN = float(np.sqrt(0.5))
CLUSTER_ID = b'\x1f\x43\xb6\x75'
CLUSTER_TIMECODE_ID = 0xE7


def _read_vint(buf, pos: int) -> Optional[Tuple[int, int]]:
    if pos >= len(buf):
        return None
    first = buf[pos]
    if first == 0:
        return None
    length = 9 - first.bit_length()
    if pos + length > len(buf):
        return None
    value = first & (0xFF >> length)
    for offset in range(1, length):
        value = (value << 8) | buf[pos + offset]
    return value, length


def _cluster_timecode(buf, pos: int) -> Optional[int]:
    size = _read_vint(buf, pos + 4)
    if size is None:
        return None
    child = pos + 4 + size[1]
    if child >= len(buf) or buf[child] != CLUSTER_TIMECODE_ID:
        return None
    timecode_size = _read_vint(buf, child + 1)
    if timecode_size is None or not 1 <= timecode_size[0] <= 8:
        return None
    start = child + 1 + timecode_size[1]
    end = start + timecode_size[0]
    if end > len(buf):
        return None
    return int.from_bytes(bytes(buf[start:end]), 'big')


class _StreamResampler:
    def __init__(self, out_rate: int):
        self.out_rate = out_rate
        self.in_rate: Optional[int] = None
        self._stream: Optional[soxr.ResampleStream] = None

    def _flush(self) -> np.ndarray:
        if self._stream is None:
            return np.zeros((0, 2), dtype=np.float32)
        tail = self._stream.resample_chunk(np.zeros((0, 2), dtype=np.float32), last=True)
        self._stream = None
        return tail

    def process(self, audio: AudioSegment) -> bytes:
        samples = np.frombuffer(audio.raw_data, dtype=np.int16).astype(np.float32) / 32768.0
        if audio.channels == 1:
            samples = np.repeat(samples[:, None] * MONO_UPMIX_GAIN, 2, axis=1)
        else:
            samples = samples.reshape(-1, audio.channels)[:, :2]

        parts = []
        if audio.frame_rate != self.in_rate:
            parts.append(self._flush())
            self.in_rate = audio.frame_rate
            if self.in_rate != self.out_rate:
                self._stream = soxr.ResampleStream(self.in_rate, self.out_rate, 2, dtype='float32', quality='HQ')
        if self._stream is None:
            parts.append(samples)
        else:
            parts.append(self._stream.resample_chunk(np.ascontiguousarray(samples)))
        return self._to_pcm(np.concatenate(parts))

    def finish(self) -> bytes:
        return self._to_pcm(self._flush())

    @staticmethod
    def _to_pcm(samples: np.ndarray) -> bytes:
        if samples.size == 0:
            return b""
        return np.clip(np.round(samples * 32768.0), -32768, 32767).astype(np.int16).tobytes()


class LiveStreamEncoder:
    def __init__(self, sio, room: str, user_id, tts_type: str, stream_id: str, blips: Optional[str] = None):
        self.sio = sio
        self.room = room
        self.user_id = user_id
        self.tts_type = tts_type
        self.stream_id = stream_id
        self.blips = blips
        self.started = False
        self.failed = False
        self.first_audio_at: Optional[float] = None
        self.fed_seconds = 0.0
        self.blip_seconds = 0.0
        self.talk_end_s: Optional[float] = None
        self._intro_done = False
        self._held: List[Tuple[AudioSegment, Dict]] = []
        self.chunks_emitted = 0
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._flusher_task: Optional[asyncio.Task] = None
        self._resampler = _StreamResampler(ENCODER_SAMPLE_RATE)
        self.first_emit_at: Optional[float] = None
        self._out = bytearray()
        self._data_ready = asyncio.Event()
        self._emit_lock = asyncio.Lock()
        self._samples_fed = 0
        self._mark_starts: List[int] = []
        self._mark_intensities: List[Dict] = []
        self._last_intensities: Dict = {}

    async def start(self):
        if self.started:
            return
        self.started = True
        await self.sio.emit('tts_stream_start', {
            'session_id': self.room,
            'tts_type': self.tts_type,
            'unique_id': self.stream_id
        }, room=self.room)
        try:
            self._proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-probesize", "32", "-analyzeduration", "0",
                "-f", "s16le", "-ar", str(ENCODER_SAMPLE_RATE), "-ac", "2",
                "-i", "pipe:0",
                "-c:a", "libopus", "-b:a", ENCODER_BITRATE,
                "-ar", str(ENCODER_SAMPLE_RATE), "-ac", "2",
                "-flush_packets", "1",
                "-max_delay", "0", "-muxdelay", "0", "-muxpreload", "0",
                "-cluster_time_limit", "20",
                "-f", "webm", "pipe:1",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError as e:
            log_service.error(f"Live TTS encoder failed to start: {e}")
            self._proc = None
            self.failed = True
            raise RuntimeError(f"Live TTS encoder (ffmpeg) failed to start: {e}") from e
        self._reader_task = asyncio.create_task(self._read_loop(), name=f"tts_encoder_reader:{self.stream_id}")
        self._flusher_task = asyncio.create_task(self._flush_loop(), name=f"tts_encoder_flusher:{self.stream_id}")

    async def _read_loop(self):
        assert self._proc is not None and self._proc.stdout is not None
        try:
            while True:
                data = await self._proc.stdout.read(65536)
                if not data:
                    break
                self._out.extend(data)
                self._data_ready.set()
        except (OSError, RuntimeError) as e:
            log_service.error(f"Live TTS encoder read failed: {e}")

    async def _flush_loop(self):
        while True:
            await self._data_ready.wait()
            self._data_ready.clear()
            await self._emit(final=False)
            await asyncio.sleep(EMIT_INTERVAL_S)

    def _intensities_at(self, timecode_ms: int) -> Dict:
        index = bisect.bisect_right(self._mark_starts, timecode_ms * ENCODER_SAMPLE_RATE // 1000) - 1
        return self._mark_intensities[max(0, index)] if self._mark_intensities else {}

    def _take_pieces(self, final: bool) -> List[Tuple[bytes, Dict]]:
        clusters = []
        pos = self._out.find(CLUSTER_ID)
        while pos >= 0:
            timecode = _cluster_timecode(self._out, pos)
            if timecode is not None:
                clusters.append((pos, timecode))
            pos = self._out.find(CLUSTER_ID, pos + 1)

        if final:
            limit = len(self._out)
        elif len(clusters) >= 2:
            limit = clusters[-1][0]
            clusters = clusters[:-1]
        else:
            return []

        pieces = []
        piece_start = 0
        label = self._last_intensities
        for cluster_pos, timecode in clusters:
            cluster_label = self._intensities_at(timecode)
            if cluster_label != label and cluster_pos > piece_start:
                pieces.append((bytes(self._out[piece_start:cluster_pos]), label))
                piece_start = cluster_pos
            label = cluster_label
        if limit > piece_start:
            pieces.append((bytes(self._out[piece_start:limit]), label))
        self._last_intensities = label
        del self._out[:limit]
        return pieces

    async def _emit(self, final: bool):
        async with self._emit_lock:
            for data, intensities in self._take_pieces(final):
                if self.first_emit_at is None:
                    self.first_emit_at = time.perf_counter()
                await self.sio.emit('tts_stream_audio_chunk', {
                    'user_id': self.user_id,
                    'tts_type': self.tts_type,
                    'unique_id': self.stream_id,
                    'chunk': base64.b64encode(data).decode('utf-8'),
                    'speaker_intensities': dict(intensities),
                    'chunk_num': self.chunks_emitted
                }, room=self.room)
                self.chunks_emitted += 1

    async def _write(self, pcm: bytes):
        if not pcm or self._proc is None or self._proc.stdin is None:
            return
        self._proc.stdin.write(pcm)
        await self._proc.stdin.drain()

    async def feed(self, audio: AudioSegment, speaker_intensities: Dict):
        if len(audio) == 0:
            return
        if not self.started:
            await self.start()
        if not self.blips:
            await self._feed_audio(audio, speaker_intensities)
            return
        self._held.append((audio, dict(speaker_intensities or {})))
        if not self._intro_done and self._held_ms() < settings.BLIPS_HOLD_MS:
            return
        if not self._intro_done:
            await voice_thread(self._blip_in)
        await self._release(settings.BLIPS_HOLD_MS)

    def _held_ms(self) -> int:
        return sum(len(audio) for audio, _ in self._held)

    def _held_audio(self) -> AudioSegment:
        voice = self._held[0][0]
        for audio, _ in self._held[1:]:
            voice += audio
        return voice

    def _resplit(self, mixed: AudioSegment, offset: int):
        pieces, position = [], offset
        if offset > 0:
            pieces.append((mixed[:offset], {}))
        for audio, intensities in self._held:
            pieces.append((mixed[position:position + len(audio)], intensities))
            position += len(audio)
        if position < len(mixed):
            pieces.append((mixed[position:], {}))
        self.blip_seconds += (len(mixed) - self._held_ms()) / 1000.0
        self._held = pieces

    def _blip_in(self):
        self._intro_done = True
        blip = station_blips.pick(self.blips, "in")
        if blip is not None and self._held:
            mixed, voice_at = station_blips.mix_in(blip, self._held_audio())
            self._resplit(mixed, voice_at)

    def _blip_out(self):
        blip = station_blips.pick(self.blips, "out")
        if blip is not None and self._held:
            held = self._held_audio()
            self.talk_end_s = round(self.fed_seconds + station_blips.speech_bounds(held)[1] / 1000.0, 3)
            self._resplit(station_blips.mix_out(held, blip), 0)

    async def _release(self, keep_ms: int):
        while self._held and self._held_ms() - len(self._held[0][0]) >= keep_ms:
            audio, intensities = self._held.pop(0)
            await self._feed_audio(audio, intensities)
        if keep_ms == 0:
            while self._held:
                audio, intensities = self._held.pop(0)
                await self._feed_audio(audio, intensities)

    async def _feed_audio(self, audio: AudioSegment, speaker_intensities: Dict):
        if len(audio) == 0:
            return
        pcm = await voice_thread(self._resampler.process, audio)
        if self.first_audio_at is None:
            self.first_audio_at = time.perf_counter()
        self._mark_starts.append(self._samples_fed)
        self._mark_intensities.append(dict(speaker_intensities or {}))
        self._samples_fed += round(audio.frame_count() * ENCODER_SAMPLE_RATE / audio.frame_rate)
        self.fed_seconds += audio.duration_seconds
        await self._write(pcm)

    async def finish(self):
        if not self.started:
            return
        try:
            if self._held:
                if not self._intro_done:
                    await voice_thread(self._blip_in)
                await voice_thread(self._blip_out)
                await self._release(0)
            await self._write(self._resampler.finish())
            if self._proc is not None and self._proc.stdin is not None:
                self._proc.stdin.close()
            if self._reader_task is not None:
                await self._reader_task
            if self._proc is not None:
                await self._proc.wait()
        except (OSError, RuntimeError) as e:
            self.failed = True
            log_service.error(f"Live TTS encoder finish failed: {e}")
        finally:
            await self._stop_flusher()
            await self._emit(final=True)
            await self._emit_end(complete=not self.failed)

    async def _stop_flusher(self):
        if self._flusher_task is None or self._flusher_task.done():
            return
        async with self._emit_lock:
            self._flusher_task.cancel()
        await asyncio.wait({self._flusher_task}, timeout=1.0)

    async def cancel(self):
        if not self.started:
            return
        for task in (self._reader_task, self._flusher_task):
            if task is not None and not task.done():
                task.cancel()
        if self._proc is not None and self._proc.returncode is None:
            try:
                self._proc.kill()
                await asyncio.wait_for(self._proc.wait(), timeout=1.0)
            except (ProcessLookupError, OSError, asyncio.TimeoutError):
                pass
        self._out.clear()
        await self._emit_end(complete=False)

    async def _emit_end(self, complete: bool):
        await self.sio.emit('tts_stream_end', {
            'session_id': self.room,
            'tts_type': self.tts_type,
            'unique_id': self.stream_id,
            'complete': complete,
            'duration_s': round(self.fed_seconds, 3),
            'talk_end_s': self.talk_end_s
        }, room=self.room)
