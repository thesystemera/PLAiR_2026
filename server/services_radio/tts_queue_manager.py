import asyncio
import itertools
import re
import time
import uuid
import os
from pydub import AudioSegment
from pathlib import Path
from typing import List, Dict, Optional, Tuple

from config.settings import settings
from services import log_service
from services import usage_tracking
from services.task_utils import spawn
from services_radio.tts_broadcast_service import TimelineMixer, limit_peaks, CLIP_CHUNK_MS
from services_radio.tts_generation_service import EMBEDDINGS_BY_CONTENT_TYPE
from services_radio.tts_live_stream import LiveStreamEncoder
from services_radio.tts_processing_service import decode_mp3
from services_radio.tts_voice_threads import voice_thread

SHOUTOUT_AUDIO_URL = re.compile(r'/api/user_content/shoutouts/audio/(\d+)/([A-Za-z0-9_-]+)\.mp3$')
SHOUTOUT_AUDIO_ID = re.compile(r'^(\d+)_([A-Za-z0-9_-]+)$')
SESSION_WORKER_IDLE_TIMEOUT_S = 60.0
RENDER_CANCEL_TIMEOUT_S = 2.0
NO_AUDIO = 0
SPOKEN_CHARS_PER_S = 16.0
FILLER_SECONDS = {'meta': 1.0, 'impulse': 1.0, 'breath': 0.4, 'audio': 0.0, 'user_content': 8.0}
GENERATED_EMBEDDINGS = ('tts_embeddings', 'meta_embeddings', 'impulse_embeddings')
VOICE_SPEAKERS = frozenset(settings.VOICE_PREFERENCES)
TTS_TYPE_LABELS = {
    'interactive': 'chat reply',
    'announcer': 'track announcement',
    'radio_segment': 'talk break',
    'sting': 'station sting',
    'shoutouts': 'shoutout segment',
}

class PrerenderedClip:
    def __init__(self, audio: AudioSegment, marks: Optional[List[Tuple[int, Dict]]] = None, label: str = ""):
        self.audio = audio
        self.marks = sorted(marks or [(0, {})], key=lambda mark: mark[0])
        self.label = label

    def intensities_at(self, position_ms: int) -> Dict:
        current = self.marks[0][1]
        for start, intensities in self.marks:
            if start > position_ms:
                break
            current = intensities
        return current

    def pieces(self) -> List[Tuple[AudioSegment, Dict]]:
        bounds = sorted({0, len(self.audio)} | {start for start, _ in self.marks if 0 < start < len(self.audio)})
        pieces = []
        for start, end in zip(bounds, bounds[1:]):
            position = start
            while position < end:
                stop = min(end, position + CLIP_CHUNK_MS)
                pieces.append((self.audio[position:stop], self.intensities_at(position)))
                position = stop
        return pieces


class _SegmentRender:
    def __init__(self, index: int, segment: Dict):
        self.index = index
        self.segment = segment
        self.resolved = asyncio.Event()
        self.rate: Optional[int] = None
        self.task: Optional[asyncio.Task] = None

    def resolve(self, rate: Optional[int]):
        if not self.resolved.is_set():
            self.rate = rate
            self.resolved.set()

def _spoken_seconds(segment: Dict) -> float:
    if segment.get('type') == 'sentence':
        return len(segment.get('content') or '') / SPOKEN_CHARS_PER_S
    return FILLER_SECONDS.get(segment.get('type'), 1.0)


class _Turn:
    def __init__(self, seq: int, owner: str, renders: List[_SegmentRender]):
        self.seq = seq
        self.group = (owner, seq)
        self.renders = renders
        self.audible = False
        self.deadlines = list(itertools.accumulate(
            (_spoken_seconds(render.segment) for render in renders[:-1]), initial=time.monotonic()))

    def rank(self, index: int) -> Tuple:
        return 0, self.deadlines[index], self.seq, index

    async def wait_resolved_before(self, index: int):
        for render in self.renders[:index]:
            await render.resolved.wait()

class IncrementalBlend:
    def __init__(self):
        self.segments: List[Dict] = []
        self.audio = AudioSegment.empty()
        self.total_duration = 0
        self.timeline: List[Dict] = []

    def add(self, segment: Dict):
        segments_to_blend = self.segments
        segments_to_blend.append(segment)
        i = len(segments_to_blend) - 1

        if segment['audio'] is None:
            return
        try:
            segment_audio = segment['audio']
            segment_duration = len(segment_audio)
            speaker = segment.get('speaker', '')
            audio_process = segment.get('audio_process', 0.0)

            if i == 0:
                self.audio += segment_audio
                self.timeline.append({
                    'start': 0,
                    'duration': segment_duration,
                    'speaker': speaker,
                    'intensity': audio_process
                })
                self.total_duration += segment_duration
                return

            overlap_chars = segment.get('overlap', 0)
            previous_segments_chars = sum(s.get('char_count', 0) for s in segments_to_blend[:i])
            overlap_ratio = overlap_chars / previous_segments_chars if previous_segments_chars > 0 else 0

            prev_speaker = segments_to_blend[i - 1].get('speaker', '')
            if speaker == prev_speaker:
                blend_position = self.total_duration
                overlap_duration = 0
            else:
                overlap_ratio = min(overlap_ratio, 0.5)
                overlap_duration = int(self.total_duration * overlap_ratio)
                overlap_duration = min(overlap_duration, self.total_duration)
                blend_position = self.total_duration - overlap_duration
                own_voice_end = self._voice_end(speaker)
                if blend_position < own_voice_end:
                    blend_position = own_voice_end
                    overlap_duration = self.total_duration - blend_position

            try:
                self.timeline.append({
                    'start': blend_position,
                    'duration': segment_duration,
                    'speaker': speaker,
                    'intensity': audio_process
                })

                temp_blend = self.audio.overlay(segment_audio, position=blend_position)
                if temp_blend.dBFS >= -1.0:
                    gain_reduction = -1.0 - temp_blend.dBFS
                    segment_audio = segment_audio.apply_gain(gain_reduction)

                self.audio = self.audio.overlay(segment_audio, position=blend_position)
                self.audio += segment_audio[overlap_duration:]
                self.total_duration = len(self.audio)

            except Exception as e:
                log_service.error(f"Audio Blending: Failed to blend segment {i}: {e}")

        except Exception as e:
            log_service.error(f"Audio Blending: Failed to process segment {i}: {e}")

    def _voice_end(self, speaker: str) -> int:
        if speaker not in VOICE_SPEAKERS:
            return 0
        return max(
            (entry['start'] + entry['duration'] for entry in self.timeline if entry['speaker'] == speaker),
            default=0
        )

    def safe_end(self, future_segments: List[Dict]) -> int:
        total = self.total_duration
        previous_chars = sum(s.get('char_count', 0) for s in self.segments)
        bound = total
        for segment in future_segments:
            overlap_chars = segment.get('overlap', 0)
            if overlap_chars <= 0:
                continue
            ratio = min(overlap_chars / previous_chars, 0.5) if previous_chars > 0 else 0.5
            bound = min(bound, total - int(total * ratio))
        return bound

    def temporal_intensities(self) -> List[Dict]:
        change_points = set()
        for entry in self.timeline:
            change_points.add(entry['start'])
            change_points.add(entry['start'] + entry['duration'])
        sorted_times = sorted(change_points)

        temporal_intensities = []
        for i in range(len(sorted_times) - 1):
            start_time = sorted_times[i]
            end_time = sorted_times[i + 1]
            active_speakers = {
                'tara': None,
                'leo': None,
                'computer': None
            }
            for entry in self.timeline:
                entry_start = entry['start']
                entry_end = entry['start'] + entry['duration']
                if not (entry_end <= start_time or entry_start >= end_time):
                    active_speakers[entry['speaker']] = entry['intensity']
            temporal_intensities.append({
                'start': start_time,
                'duration': end_time - start_time,
                'intensities': active_speakers.copy()
            })
        return temporal_intensities

class TTSQueueManager:
    def __init__(
            self,
            vector_db_service,
            audio_processing_service,
            tts_generation_service,
            audio_broadcast_service,
            tts_stream_planner
    ):
        self.vector_db_service = vector_db_service
        self.audio_processing_service = audio_processing_service
        self.tts_generation_service = tts_generation_service
        self.audio_broadcast_service = audio_broadcast_service
        self.tts_stream_planner = tts_stream_planner

        self.current_stream_id = None
        self.current_user_id = None
        self.current_tts_type = None
        self._session_queues: Dict[str, asyncio.Queue] = {}
        self._session_workers: Dict[str, asyncio.Task] = {}
        self._session_active: Dict[str, asyncio.Task] = {}
        self._turn_seq = itertools.count(1)

        log_service.detail("TTS Request: Initializing TTSQueueManager", "tts_queue_manager")

    async def start(self):
        log_service.detail("TTS Request: Per-session TTS queue processors ready", "tts_queue_manager")

    @staticmethod
    def _session_key(session_id: Optional[str], user_id: int) -> str:
        return session_id or f"user:{user_id}"

    def session_busy(self, session_id: Optional[str], user_id: int = 0) -> bool:
        key = self._session_key(session_id, user_id)
        queue = self._session_queues.get(key)
        task = self._session_active.get(key)
        return (queue is not None and not queue.empty()) or (task is not None and not task.done())

    def _enqueue(self, item: Tuple):
        key = self._session_key(item[5], item[1])
        queue = self._session_queues.get(key)
        if queue is None:
            queue = asyncio.Queue()
            self._session_queues[key] = queue
        queue.put_nowait(item)

        worker = self._session_workers.get(key)
        if worker is None or worker.done():
            self._session_workers[key] = asyncio.create_task(
                self._process_session_queue(key, queue), name=f"tts_session_worker:{key}"
            )

    async def _tts_allowed(self, user_id: int, is_broadcast: bool, is_temp_user: bool) -> bool:
        if is_broadcast and not is_temp_user and user_id:
            from services.user_data_cache_service import user_data_cache
            user = await user_data_cache.get_user(user_id)
            if user and getattr(user, 'tts_muted', False):
                log_service.tts_queue_manager(
                    f"DJ voice: skipped for {log_service.who(user_id=user_id)} (DJ voice muted)")
                return False
        return True

    async def add_tts_request(
            self,
            text: str,
            user_id: int,
            tts_type: str,
            is_broadcast: bool,
            is_temp_user: bool = True,
            session_id: Optional[str] = None,
            stream_id: Optional[str] = None,
            lead_in: Optional[PrerenderedClip] = None
    ) -> bool:
        if not await self._tts_allowed(user_id, is_broadcast, is_temp_user):
            return False
        self._enqueue((text, user_id, tts_type, is_broadcast, is_temp_user, session_id, stream_id, lead_in))
        log_service.detail(f"TTS Request: queued {tts_type} for {log_service.who(session_id, user_id=user_id)}",
                           "tts_queue_manager")
        return True

    async def add_clip_request(
            self,
            clip: PrerenderedClip,
            user_id: int,
            tts_type: str,
            is_temp_user: bool = True,
            session_id: Optional[str] = None,
            stream_id: Optional[str] = None
    ) -> bool:
        if len(clip.audio) == 0 or not await self._tts_allowed(user_id, True, is_temp_user):
            return False
        self._enqueue((clip, user_id, tts_type, True, is_temp_user, session_id, stream_id, None))
        log_service.detail(
            f"TTS Request: queued {tts_type} clip '{clip.label}' ({len(clip.audio) / 1000:.1f}s) for "
            f"{log_service.who(session_id, user_id=user_id)}", "tts_queue_manager")
        return True

    async def _process_session_queue(self, key: str, queue: asyncio.Queue):
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=SESSION_WORKER_IDLE_TIMEOUT_S)
                except asyncio.TimeoutError:
                    if queue.empty():
                        return
                    continue

                text, user_id, tts_type, _is_broadcast, _is_temp_user, session_id, requested_stream_id, lead_in = item
                stream_id = requested_stream_id or self.current_stream_id or str(uuid.uuid4())
                if isinstance(text, PrerenderedClip):
                    work = self._stream_clip(text, user_id, tts_type, stream_id, session_id)
                else:
                    work = self._generate_tts_stream(text, user_id, tts_type, stream_id, session_id, lead_in)
                task = asyncio.create_task(work, name=f"tts_stream:{key}")
                self._session_active[key] = task
                try:
                    await task
                except asyncio.CancelledError:
                    current = asyncio.current_task()
                    if current is not None and current.cancelling():
                        raise
                    log_service.tts_queue_manager(
                        f"DJ voice for {log_service.who(session_id, user_id=user_id)}: stream {stream_id[:8]} cancelled")
                except Exception as e:
                    log_service.error(
                        f"DJ voice for {log_service.who(session_id, user_id=user_id)}: {tts_type} stream failed: {e}")
                finally:
                    if self._session_active.get(key) is task:
                        del self._session_active[key]
                    queue.task_done()
        finally:
            if self._session_queues.get(key) is queue and queue.empty():
                del self._session_queues[key]
            if self._session_workers.get(key) is asyncio.current_task():
                del self._session_workers[key]

    async def cancel_session(self, session_id: Optional[str], user_id: Optional[int] = None):
        key = self._session_key(session_id, user_id or 0)
        room = session_id or str(user_id or 0)

        dropped = 0
        queue = self._session_queues.get(key)
        if queue is not None:
            while True:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                queue.task_done()
                dropped += 1

        job_ids = self.tts_generation_service.owner_jobs(key)
        task = self._session_active.get(key)
        cancelled_active = task is not None and not task.done()
        if cancelled_active:
            task.cancel()
            await asyncio.wait({task}, timeout=2.0)

        if job_ids:
            await self.tts_generation_service.abort_jobs(key, job_ids)

        await self.audio_broadcast_service.broadcast_cancel(room)
        if cancelled_active or dropped or job_ids:
            log_service.tts_queue_manager(
                f"DJ voice for {log_service.who(session_id, user_id=user_id)}: interrupted by a new listener turn "
                f"({'stopped the current reply, ' if cancelled_active else ''}dropped {dropped} queued, "
                f"aborted {len(job_ids)} engine job(s))")

    async def _generate_tts_stream(
            self,
            text: str,
            user_id: int,
            tts_type: str,
            stream_id: str,
            session_id: Optional[str] = None,
            lead_in: Optional[PrerenderedClip] = None
    ):
        usage_tracking.bind_session(session_id, None if session_id else user_id)
        ordered_content = self.tts_stream_planner.split_text_into_sentences(text)
        stream_plan = self.tts_stream_planner.create_stream_plan(ordered_content)
        owner = self._session_key(session_id, user_id)
        room = session_id or str(user_id)
        listener = log_service.who(session_id, user_id=user_id)
        kind = TTS_TYPE_LABELS.get(tts_type, tts_type)
        spoken = [segment for segment in ordered_content if segment.get('type') == 'sentence']
        if spoken:
            voices = "+".join(sorted({segment.get('speaker') or '?' for segment in spoken}))
            shape = f"{len(spoken)} lines ({voices}), {len(ordered_content)} segments"
        else:
            shape = "+".join(sorted({segment.get('type') or '?' for segment in ordered_content})) or "empty"
        opening = " ".join(segment.get('content', '') for segment in spoken[:2]).strip()
        quote = f" | \"{opening[:90]}{'...' if len(opening) > 90 else ''}\"" if opening else ""
        log_service.tts_queue_manager(f"DJ voice for {listener}: {kind} started | {shape}{quote}")

        renders = [_SegmentRender(index, segment) for index, segment in enumerate(ordered_content)]
        renders_by_segment = {id(render.segment): render for render in renders}
        turn = _Turn(next(self._turn_seq), owner, renders)
        encoder = LiveStreamEncoder(self.audio_broadcast_service.sio, room, user_id, tts_type, stream_id)
        mixer = TimelineMixer()
        started_at = time.perf_counter()
        hits_before = self.tts_generation_service.metrics['hits']
        misses_before = self.tts_generation_service.metrics['misses']
        completed = False

        self.tts_generation_service.block_low_priority()
        self.tts_generation_service.open_turn(turn.group)
        try:
            for render in renders:
                render.task = asyncio.create_task(
                    self._render_segment(turn, render, ordered_content, owner),
                    name=f"tts_render:{owner}:{render.index}"
                )
            await encoder.start()
            if lead_in is not None:
                for audio, intensities in lead_in.pieces():
                    await encoder.feed(limit_peaks(audio), intensities)

            for plan_item in stream_plan:
                if plan_item['type'] == 'blend':
                    await self._stream_blend(plan_item['segments'], renders_by_segment, mixer, encoder, turn)
                    continue

                processed = await renders_by_segment[id(plan_item['segment'])].task
                if processed is None:
                    continue
                if processed['type'] == 'audio':
                    await voice_thread(
                        mixer.add_background, processed['audio'], processed['speaker_intensities']
                    )
                else:
                    chunks = await voice_thread(
                        self._mix_main_item, mixer, processed['audio'], processed['speaker_intensities']
                    )
                    await self._feed(encoder, chunks, turn)

            await self._feed(encoder, await voice_thread(mixer.mix_tail), turn)
            completed = True
        finally:
            self.tts_generation_service.allow_low_priority()
            pending = [render.task for render in renders if render.task is not None and not render.task.done()]
            try:
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.wait(pending, timeout=RENDER_CANCEL_TIMEOUT_S)
            finally:
                self.tts_generation_service.close_turn(turn.group)
                if completed:
                    await encoder.finish()
                else:
                    await encoder.cancel()

            first_audio = (encoder.first_emit_at - started_at) if encoder.first_emit_at else None
            outcome = 'done' if completed else 'failed' if encoder.failed else 'cancelled'
            log_service.tts_queue_manager(
                f"DJ voice for {listener}: {kind} {outcome} | {encoder.fed_seconds:.1f}s audio, "
                f"first audio {'n/a' if first_audio is None else f'{first_audio:.1f}s'}, "
                f"total {time.perf_counter() - started_at:.1f}s | clips: "
                f"{self.tts_generation_service.metrics['hits'] - hits_before} from cache, "
                f"{self.tts_generation_service.metrics['misses'] - misses_before} rendered | "
                f"stream {stream_id[:8]}, {mixer.chunks_mixed} chunks"
            )

    async def _stream_clip(self, clip: PrerenderedClip, user_id: int, tts_type: str, stream_id: str,
                           session_id: Optional[str] = None):
        usage_tracking.bind_session(session_id, None if session_id else user_id)
        room = session_id or str(user_id)
        encoder = LiveStreamEncoder(self.audio_broadcast_service.sio, room, user_id, tts_type, stream_id)
        completed = False
        try:
            await encoder.start()
            for audio, intensities in clip.pieces():
                await encoder.feed(limit_peaks(audio), intensities)
            completed = True
        finally:
            if completed:
                await encoder.finish()
            else:
                await encoder.cancel()
            log_service.tts_queue_manager(
                f"DJ voice for {log_service.who(session_id, user_id=user_id)}: "
                f"{TTS_TYPE_LABELS.get(tts_type, tts_type)} '{clip.label}' "
                f"{'played' if completed else 'cancelled'} ({encoder.fed_seconds:.1f}s)")

    async def _feed(self, encoder: LiveStreamEncoder, chunks: List[Tuple[AudioSegment, Dict]], turn: _Turn):
        for audio, intensities in chunks:
            await encoder.feed(audio, intensities)
        if chunks and not turn.audible:
            turn.audible = True
            self.tts_generation_service.widen_turn(turn.group)

    @staticmethod
    def _mix_main_item(mixer: TimelineMixer, audio: AudioSegment,
                       speaker_intensities) -> List[Tuple[AudioSegment, Dict]]:
        mixer.begin_main()
        chunks, _position = mixer.mix_main(audio, speaker_intensities)
        return chunks

    @staticmethod
    def _mix_blend_range(mixer: TimelineMixer, blend: IncrementalBlend, position: int,
                         safe_end: Optional[int]) -> Tuple[List[Tuple[AudioSegment, Dict]], int]:
        if position == 0:
            mixer.begin_main()
        return mixer.mix_main(blend.audio, blend.temporal_intensities(), position, safe_end)

    async def _stream_blend(self, segments: List[Dict], renders_by_segment: Dict[int, _SegmentRender],
                            mixer: TimelineMixer, encoder: LiveStreamEncoder, turn: _Turn):
        ordered = sorted(segments, key=lambda segment: segment.get('char_start', 0))
        renders = [renders_by_segment[id(segment)] for segment in ordered]
        blend = IncrementalBlend()
        position = 0

        for index, render in enumerate(renders):
            processed = await render.task
            if processed is not None:
                await voice_thread(blend.add, processed)

            upcoming = renders[index + 1:]
            if not upcoming or blend.total_duration == 0 or blend.audio.channels != 2:
                continue
            for pending in upcoming:
                await pending.resolved.wait()
            if any(pending.rate is None or pending.rate > blend.audio.frame_rate for pending in upcoming):
                continue
            safe_end = blend.safe_end([pending.segment for pending in upcoming])
            if safe_end <= position:
                continue
            chunks, position = await voice_thread(self._mix_blend_range, mixer, blend, position, safe_end)
            await self._feed(encoder, chunks, turn)

        if len(blend.audio) > 0:
            chunks, position = await voice_thread(self._mix_blend_range, mixer, blend, position, None)
            await self._feed(encoder, chunks, turn)

    async def _render_segment(self, turn: _Turn, render: _SegmentRender, ordered_content: List[Dict],
                              owner: str) -> Optional[Dict]:
        segment = render.segment
        segment_index = render.index
        content_type = segment['type']
        content_voice = segment['speaker']
        audio_process_mix = segment.get('audio_process', 0.0)
        rank = turn.rank(segment_index)
        generation = self.tts_generation_service

        previous_segment_end_mix = None
        next_segment_start_mix = None
        if segment_index > 0 and ordered_content[segment_index - 1]['speaker'] == content_voice:
            previous_segment_end_mix = ordered_content[segment_index - 1].get('audio_process', 0.0)
        if segment_index < len(ordered_content) - 1 and ordered_content[segment_index + 1]['speaker'] == content_voice:
            next_segment_start_mix = ordered_content[segment_index + 1].get('audio_process', 0.0)

        audio_segment = None
        file_path = None

        try:
            if content_type in EMBEDDINGS_BY_CONTENT_TYPE:
                embeddings_type = EMBEDDINGS_BY_CONTENT_TYPE[content_type]
                clip_voice = content_voice if content_type != 'audio' else 'computer'
                can_generate = content_voice in settings.GENERATION_PERMISSIONS.get(content_type, set())
                tag = segment['content'].strip()

                cached = await generation.lookup_clip(
                    tag, embeddings_type, clip_voice, generation.similarity_threshold(embeddings_type), rank,
                    listener=owner
                )
                cached_file_path = cached[0] if cached else None
                if cached and can_generate:
                    generation.note_cache_match(embeddings_type, clip_voice, tag, cached[1])
                if cached_file_path:
                    render.resolve(await generation.clip_rate(cached_file_path))
                elif can_generate and embeddings_type in GENERATED_EMBEDDINGS:
                    render.resolve(settings.TTS_SAMPLE_RATE)
                else:
                    render.resolve(NO_AUDIO)

                audio_segment, file_path = await generation.render_clip(
                    tag, embeddings_type, clip_voice, cached_file_path,
                    audio_process_mix, previous_segment_end_mix, next_segment_start_mix,
                    can_generate=can_generate, owner=owner, rank=rank, group=turn.group,
                    before_generation=lambda: turn.wait_resolved_before(segment_index)
                )

            elif content_type == 'breath':
                file_path = await generation.resolve_breath_clip(
                    (segment.get('context') or segment['content']).strip(), content_voice, rank, listener=owner
                )
                render.resolve(await generation.clip_rate(file_path) if file_path else NO_AUDIO)
                if file_path:
                    audio_segment = await generation.load_clip(
                        file_path, content_voice, audio_process_mix, previous_segment_end_mix,
                        next_segment_start_mix, process=audio_process_mix > 0, rank=rank
                    )

            elif content_type == 'user_content':
                file_path = self._resolve_user_content(segment['content'])
                render.resolve(await generation.clip_rate(file_path) if file_path else NO_AUDIO)
                if file_path:
                    audio_segment = await voice_thread(decode_mp3, file_path)
                    self._note_community_play(file_path, owner)
                    log_service.detail(
                        f"TTS: Successfully loaded user content: {os.path.basename(file_path)}", "tts_queue_manager")
                else:
                    log_service.error(f"TTS: User content file not found for input: {segment['content']}")

            if audio_segment and file_path:
                if audio_process_mix > 0 and content_type == 'user_content':
                    audio_segment = await generation.process_clip(
                        audio_segment, content_voice, audio_process_mix,
                        previous_segment_end_mix, next_segment_start_mix, rank
                    )

                speaker_intensities = {
                    'tara': audio_process_mix if content_voice == 'tara' else None,
                    'leo': audio_process_mix if content_voice == 'leo' else None,
                    'computer': audio_process_mix if content_voice == 'computer' else None
                }

                return {
                    'audio': audio_segment,
                    'speaker_intensities': speaker_intensities,
                    'speaker': content_voice,
                    'type': content_type,
                    'overlap': segment.get('overlap', 0),
                    'char_count': segment.get('char_count', 0),
                    'char_start': segment.get('char_start', 0),
                    'char_end': segment.get('char_end', 0),
                    'audio_process': audio_process_mix
                }

        except Exception as e:
            log_service.error(f"Unexpected error: {e}")
        finally:
            render.resolve(NO_AUDIO)

        return None

    @staticmethod
    def _note_community_play(file_path: str, owner: str):
        from services.community_engagement import community_engagement
        path = Path(file_path)
        item_id = f"{path.parent.parent.name}_{path.stem.removesuffix('_sting')}"
        user_id = int(owner) if owner.isdigit() else None
        spawn(community_engagement.record_on_air_play(item_id, owner, user_id), name=f"community_play:{item_id}")

    @staticmethod
    def _resolve_user_content(raw_content: str) -> Optional[str]:
        content = raw_content.strip().split('?')[0]
        match = SHOUTOUT_AUDIO_URL.search(content) or SHOUTOUT_AUDIO_ID.match(content)
        if not match:
            log_service.warning(f"TTS: Rejected user content reference '{raw_content[:80]}'")
            return None

        uid_str, stem = match.group(1), match.group(2)
        shoutouts_dir = (settings.USERS_DIR / uid_str / "shoutouts").resolve()
        candidate = (shoutouts_dir / f"{stem}.mp3").resolve()
        if not candidate.is_relative_to(shoutouts_dir) or not candidate.is_file():
            return None
        return str(candidate)
