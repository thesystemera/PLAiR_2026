import asyncio
import uuid
import os
from pydub import AudioSegment
from typing import List, Dict, Optional, Tuple

from config.settings import settings, BASE_DIR
from services import log_service

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

        self.queue = asyncio.Queue()
        self.current_stream_id = None
        self.current_user_id = None
        self.current_tts_type = None
        self._processor_task = None

        log_service.tts_queue_manager("TTS Request: Initializing TTSQueueManager")

    async def start(self):
        log_service.tts_queue_manager("TTS Request: Starting TTS queue processor as async task")
        self._processor_task = asyncio.create_task(self._process_queue())

    async def add_tts_request(
            self,
            text: str,
            user_id: int,
            tts_type: str,
            is_broadcast: bool,
            is_temp_user: bool = True,
            session_id: Optional[str] = None
    ):
        if is_broadcast and not is_temp_user and user_id:
            from database import AsyncSessionLocal, User
            async with AsyncSessionLocal() as db:
                user = await db.get(User, user_id)
                if user and getattr(user, 'tts_muted', False):
                    log_service.tts_queue_manager(
                        f"TTS Request: User {user_id} has DJ voice muted - skipping BROADCAST TTS")
                    return
                await self.queue.put((text, user_id, tts_type, is_broadcast, is_temp_user, session_id))
                log_service.tts_queue_manager(
                    f"TTS Request: Added TTS request for session {session_id} (user {user_id})")
        else:
            await self.queue.put((text, user_id, tts_type, is_broadcast, is_temp_user, session_id))
            log_service.tts_queue_manager(f"TTS Request: Added TTS request for session {session_id} (user {user_id})")

    async def _process_queue(self):
        while True:
            try:
                text, user_id, tts_type, _is_broadcast, _is_temp_user, session_id = await self.queue.get()
                try:
                    stream_id = self.current_stream_id or str(uuid.uuid4())
                    log_service.tts_queue_manager(f"TTS Request: Processing request for session {session_id}")
                    await self._generate_tts_stream(text, user_id, tts_type, stream_id, session_id)
                except Exception as e:
                    log_service.error(f"TTS Request: Error processing TTS request: {e}")
                finally:
                    self.queue.task_done()
            except Exception as e:
                log_service.error(f"TTS Request: Queue error: {e}")
                await asyncio.sleep(0.1)

    async def _generate_tts_stream(
            self,
            text: str,
            user_id: int,
            tts_type: str,
            stream_id: str,
            session_id: Optional[str] = None
    ):
        ordered_content = self.tts_stream_planner.split_text_into_sentences(text)
        all_audio_paths = []
        main_buffer = asyncio.Queue()
        background_buffer = asyncio.Queue()

        broadcast_task = asyncio.create_task(
            self.audio_broadcast_service.broadcast_audio_stream(
                main_buffer,
                background_buffer,
                session_id or str(user_id),
                tts_type,
                stream_id,
                debug_export=False
            )
        )

        async def process_segment(
                segment: Dict,
                ordered_content: Optional[List[Dict]] = None,
                segment_index: Optional[int] = None
        ) -> Optional[Dict]:
            content_type = segment['type']
            content_voice = segment['speaker']
            audio_process_mix = segment.get('audio_process', 0.0)

            previous_segment_end_mix = None
            next_segment_start_mix = None
            if ordered_content and segment_index is not None:
                if segment_index > 0 and ordered_content[segment_index - 1]['speaker'] == content_voice:
                    previous_segment_end_mix = ordered_content[segment_index - 1].get('audio_process', 0.0)
                if segment_index < len(ordered_content) - 1 and ordered_content[segment_index + 1][
                    'speaker'] == content_voice:
                    next_segment_start_mix = ordered_content[segment_index + 1].get('audio_process', 0.0)

            audio_segment = None
            file_path = None

            try:
                if content_type in ['meta', 'impulse', 'sentence', 'audio']:
                    audio_segment, file_path = await self.tts_generation_service.fetch_or_generate_audio(
                        tag=segment['content'].strip(),
                        embeddings_type={
                            'meta': 'meta_embeddings',
                            'impulse': 'impulse_embeddings',
                            'sentence': 'tts_embeddings',
                            'audio': 'audio_embeddings'
                        }[content_type],
                        content_voice=content_voice if content_type != 'audio' else 'computer',
                        audio_process_mix=audio_process_mix,
                        previous_segment_end_mix=previous_segment_end_mix,
                        next_segment_start_mix=next_segment_start_mix,
                        can_generate=content_voice in settings.GENERATION_PERMISSIONS.get(content_type, set())
                    )

                elif content_type == 'breath':
                    breath_sound = await self.tts_generation_service.find_random_breath_sound(content_voice)
                    if breath_sound:
                        audio_segment = await asyncio.to_thread(AudioSegment.from_file, breath_sound, format="mp3")
                        file_path = breath_sound

                elif content_type == 'user_content':
                    raw_content = segment['content']

                    if "/api/user_content/" in raw_content:
                        try:
                            clean_url = raw_content.split('?')[0]
                            parts = clean_url.strip("/").split('/')

                            if len(parts) >= 2:
                                uid_str = parts[-2]
                                filename = parts[-1]

                                try:
                                    shoutouts_dir = settings.get_user_shoutouts_dir(int(uid_str))
                                    local_path = shoutouts_dir / filename
                                    if local_path.exists():
                                        file_path = str(local_path)
                                except ValueError:
                                    log_service.error(f"TTS: Invalid user ID in URL: {uid_str}")
                        except Exception as e:
                            log_service.error(f"TTS: Error parsing URL '{raw_content}': {e}")

                    elif "_" in raw_content and "/" not in raw_content:
                        try:
                            uid_str, timestamp = raw_content.split('_', 1)
                            shoutouts_dir = settings.get_user_shoutouts_dir(int(uid_str))
                            local_path = shoutouts_dir / f"{timestamp}.mp3"
                            if local_path.exists():
                                file_path = str(local_path)
                            else:
                                local_path = shoutouts_dir / timestamp
                                if local_path.exists():
                                    file_path = str(local_path)
                        except ValueError:
                            pass

                    if not file_path:
                        potential_path = os.path.join(str(BASE_DIR), raw_content.lstrip("/"))
                        if os.path.exists(potential_path):
                            file_path = potential_path

                    if file_path:
                        audio_segment = await asyncio.to_thread(AudioSegment.from_file, file_path, format="mp3")
                        log_service.tts_queue_manager(
                            f"TTS: Successfully loaded user content: {os.path.basename(file_path)}")
                    else:
                        log_service.error(f"TTS: User content file not found for input: {raw_content}")

                if audio_segment and file_path:
                    all_audio_paths.append(file_path)

                    if audio_process_mix > 0:
                        audio_segment = await asyncio.to_thread(
                            self.audio_processing_service.process_audio,
                            audio_segment,
                            audio_process_mix,
                            previous_segment_end_mix,
                            next_segment_start_mix,
                            speaker=content_voice
                        )

                    speaker_intensities = {
                        'terry': audio_process_mix if content_voice == 'terry' else None,
                        'shaquille': audio_process_mix if content_voice == 'shaquille' else None,
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

            return None

        async def blend_segments(segments_to_blend: List[Dict]) -> Tuple[Optional[AudioSegment], Optional[List[Dict]]]:
            if not segments_to_blend:
                return None, None

            segments_to_blend.sort(key=lambda x: x.get('char_start', 0))
            blended_audio = AudioSegment.empty()
            total_duration = 0
            timeline = []

            for i, segment in enumerate(segments_to_blend):
                if segment['audio'] is not None:
                    try:
                        segment_audio = segment['audio']
                        segment_duration = len(segment_audio)
                        speaker = segment.get('speaker', '')
                        audio_process = segment.get('audio_process', 0.0)

                        if i == 0:
                            blended_audio += segment_audio
                            timeline.append({
                                'start': 0,
                                'duration': segment_duration,
                                'speaker': speaker,
                                'intensity': audio_process
                            })
                            total_duration += segment_duration
                        else:
                            overlap_chars = segment.get('overlap', 0)
                            previous_segments_chars = sum(s.get('char_count', 0) for s in segments_to_blend[:i])
                            overlap_ratio = overlap_chars / previous_segments_chars if previous_segments_chars > 0 else 0

                            prev_speaker = segments_to_blend[i - 1].get('speaker', '')
                            if speaker == prev_speaker:
                                current_start = segment.get('char_start', 0)
                                prev_start = segments_to_blend[i - 1].get('char_start', 0)
                                prev_end = prev_start + segments_to_blend[i - 1].get('char_count', 0)

                                if current_start < prev_end:
                                    blend_position = total_duration
                                    overlap_duration = 0
                                else:
                                    blend_position = total_duration
                                    overlap_duration = 0
                            else:
                                overlap_ratio = min(overlap_ratio, 0.5)
                                overlap_duration = int(total_duration * overlap_ratio)
                                overlap_duration = min(overlap_duration, total_duration)
                                blend_position = total_duration - overlap_duration

                            try:
                                timeline.append({
                                    'start': blend_position,
                                    'duration': segment_duration,
                                    'speaker': speaker,
                                    'intensity': audio_process
                                })

                                temp_blend = blended_audio.overlay(segment_audio, position=blend_position)
                                if temp_blend.dBFS >= -1.0:
                                    gain_reduction = -1.0 - temp_blend.dBFS
                                    segment_audio = segment_audio.apply_gain(gain_reduction)

                                blended_audio = blended_audio.overlay(segment_audio, position=blend_position)
                                blended_audio += segment_audio[overlap_duration:]
                                total_duration = len(blended_audio)

                            except Exception as e:
                                log_service.error(f"Audio Blending: Failed to blend segment {i}: {e}")

                    except Exception as e:
                        log_service.error(f"Audio Blending: Failed to process segment {i}: {e}")
                        continue

            if len(blended_audio) == 0:
                log_service.tts_queue_manager("Audio Blending: Blended audio is empty.")
                return None, None

            change_points = set()
            for entry in timeline:
                change_points.add(entry['start'])
                change_points.add(entry['start'] + entry['duration'])
            sorted_times = sorted(change_points)

            temporal_intensities = []
            for i in range(len(sorted_times) - 1):
                start_time = sorted_times[i]
                end_time = sorted_times[i + 1]
                duration = end_time - start_time

                active_speakers = {
                    'terry': None,
                    'shaquille': None,
                    'computer': None
                }

                for entry in timeline:
                    entry_start = entry['start']
                    entry_end = entry['start'] + entry['duration']
                    if not (entry_end <= start_time or entry_start >= end_time):
                        active_speakers[entry['speaker']] = entry['intensity']

                temporal_intensities.append({
                    'start': start_time,
                    'duration': duration,
                    'intensities': active_speakers.copy()
                })

            return blended_audio, temporal_intensities

        async def process_and_stream_segments(ordered_content: List[Dict]):
            stream_plan = self.tts_stream_planner.create_stream_plan(ordered_content)

            for plan_item in stream_plan:
                if plan_item['type'] == 'single':
                    segment = plan_item['segment']
                    segment_index = ordered_content.index(segment)

                    processed_segment = await process_segment(segment, ordered_content, segment_index)

                    if processed_segment:
                        audio_data = processed_segment['audio']
                        speaker = processed_segment['speaker']
                        content_type = processed_segment['type']
                        speaker_intensities = processed_segment['speaker_intensities']

                        if content_type == 'audio':
                            await background_buffer.put({
                                'audio': audio_data,
                                'speaker_intensities': speaker_intensities,
                                'speaker': speaker
                            })
                        else:
                            await main_buffer.put({
                                'audio': audio_data,
                                'speaker_intensities': speaker_intensities,
                                'speaker': speaker
                            })

                elif plan_item['type'] == 'blend':
                    segments_to_blend = plan_item['segments']
                    segment_tasks = [
                        process_segment(segment, ordered_content, ordered_content.index(segment))
                        for segment in segments_to_blend
                    ]
                    processed_results = await asyncio.gather(*segment_tasks)
                    processed_segments = [p for p in processed_results if p is not None]

                    if processed_segments:
                        blended_audio, speaker_intensities = await blend_segments(processed_segments)

                        if blended_audio:
                            await main_buffer.put({
                                'audio': blended_audio,
                                'speaker_intensities': speaker_intensities,
                                'speaker': 'blend'
                            })

            await main_buffer.put(None)

        await process_and_stream_segments(ordered_content)
        await broadcast_task
        return all_audio_paths