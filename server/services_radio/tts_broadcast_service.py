import asyncio
import io
import base64
import os
from pydub import AudioSegment
from typing import Dict, List, Optional
from services import log_service

class AudioBroadcastService:
    def __init__(self, sio):
        self.sio = sio

    async def broadcast_audio_stream(
            self,
            main_buffer: asyncio.Queue,
            background_buffer: asyncio.Queue,
            user_id: int,
            tts_type: str,
            stream_id: str,
            debug_export: bool = False
    ):
        session_id = str(user_id)
        log_service.tts_broadcast(f"Audio Streaming: Starting stream {stream_id} for session {session_id}")

        debug_audio = AudioSegment.empty() if debug_export else None
        if debug_export:
            os.makedirs('audio_debugging', exist_ok=True)
            log_service.tts_broadcast(
                f"Audio Streaming: Debug export enabled - will save to audio_debugging/stream_{stream_id}.wav")

        await self.sio.emit('tts_stream_start', {
            'session_id': session_id,
            'tts_type': tts_type,
            'unique_id': stream_id
        }, room=session_id)

        CHUNK_SIZE = 8192
        BACKGROUND_VOLUME_REDUCTION = -80
        MAX_PEAK_DB = -1.0
        background_queue = []
        chunks_processed = 0
        total_audio_processed = 0

        def limit_peaks(audio_segment: AudioSegment, max_db: float = -1.0) -> AudioSegment:
            if len(audio_segment) == 0:
                return audio_segment
            if audio_segment.dBFS > max_db:
                return audio_segment.apply_gain(max_db - audio_segment.dBFS)
            return audio_segment

        def get_next_chunk_size(current_pos: int, audio_length: int, intensities) -> int:
            if not isinstance(intensities, list):
                return min(CHUNK_SIZE, audio_length - current_pos)
            cumulative_time = 0
            for segment in intensities:
                segment_end = cumulative_time + segment['duration']
                if cumulative_time <= current_pos < segment_end:
                    next_boundary = segment_end
                    if next_boundary - current_pos < CHUNK_SIZE:
                        return next_boundary - current_pos
                    break
                cumulative_time += segment['duration']
            return min(CHUNK_SIZE, audio_length - current_pos)

        def get_current_intensities(position: int, intensities) -> Dict:
            if not isinstance(intensities, list):
                return intensities
            cumulative_time = 0
            for segment in intensities:
                segment_end = cumulative_time + segment['duration']
                if cumulative_time <= position < segment_end:
                    return segment['intensities']
                cumulative_time += segment['duration']
            return intensities[-1]['intensities']

        def combine_intensities(main_intensities: Optional[Dict], bg_intensities: List[Dict]) -> Dict:
            result = {}
            if main_intensities:
                result.update(main_intensities)
            for bg in bg_intensities:
                for speaker, bg_val in bg.items():
                    main_val = result.get(speaker)
                    if main_val is None and bg_val is None:
                        result[speaker] = None
                    elif main_val is None:
                        result[speaker] = bg_val
                    elif bg_val is None:
                        result[speaker] = main_val
                    else:
                        result[speaker] = min(main_val, bg_val)
            return result

        main_audio_finished = False

        try:
            while True:
                try:
                    while not background_buffer.empty():
                        new_background = await background_buffer.get()
                        if new_background is not None:
                            new_bg_audio = new_background['audio']
                            before_dbfs = new_bg_audio.dBFS
                            new_bg_audio = new_bg_audio.apply_gain(BACKGROUND_VOLUME_REDUCTION)
                            after_dbfs = new_bg_audio.dBFS
                            log_service.tts_broadcast(
                                f"Background audio: BEFORE={before_dbfs:.1f} dBFS, AFTER={after_dbfs:.1f} dBFS, REDUCTION={BACKGROUND_VOLUME_REDUCTION} dB"
                            )
                            background_queue.append({
                                'audio': new_bg_audio,
                                'speaker_intensities': new_background['speaker_intensities']
                            })

                    if not main_audio_finished:
                        try:
                            main_data = await asyncio.wait_for(main_buffer.get(), timeout=0.1)
                            if main_data is None:
                                main_audio_finished = True
                            else:
                                audio_segment = main_data['audio']
                                chunk_position = 0

                                while chunk_position < len(audio_segment):
                                    chunk_size = get_next_chunk_size(
                                        chunk_position,
                                        len(audio_segment),
                                        main_data['speaker_intensities']
                                    )

                                    current_chunk = audio_segment[chunk_position:chunk_position + chunk_size]
                                    mixed_chunk = current_chunk
                                    remaining_bg = []
                                    bg_intensities = []

                                    for bg in background_queue:
                                        bg_audio = bg['audio']
                                        bg_intensities.append(bg['speaker_intensities'])

                                        if len(bg_audio) <= len(current_chunk):
                                            mixed_chunk = mixed_chunk.overlay(bg_audio)
                                        else:
                                            current_bg = bg_audio[:len(current_chunk)]
                                            remaining_bg.append({
                                                'audio': bg_audio[len(current_chunk):],
                                                'speaker_intensities': bg['speaker_intensities']
                                            })
                                            mixed_chunk = mixed_chunk.overlay(current_bg)

                                    mixed_chunk = limit_peaks(mixed_chunk, MAX_PEAK_DB)  # type: ignore
                                    background_queue = remaining_bg

                                    if debug_export:
                                        debug_audio += mixed_chunk

                                    current_intensities = combine_intensities(
                                        get_current_intensities(chunk_position, main_data['speaker_intensities']),
                                        [bg['speaker_intensities'] for bg in background_queue]
                                    )

                                    chunk_buffer = io.BytesIO()
                                    await asyncio.to_thread(
                                        mixed_chunk.export,
                                        chunk_buffer,
                                        format="webm",
                                        codec="libopus",
                                        parameters=[
                                            "-ac", "2",
                                            "-ar", "48000",
                                            "-b:a", "128k",
                                            "-f", "webm",
                                            "-cluster_size_limit", "4M",
                                            "-cluster_time_limit", "10000"
                                        ]
                                    )
                                    chunk_data = chunk_buffer.getvalue()

                                    b64_chunk = base64.b64encode(chunk_data).decode('utf-8')
                                    emit_data = {
                                        'user_id': user_id,
                                        'tts_type': tts_type,
                                        'unique_id': stream_id,
                                        'chunk': b64_chunk,
                                        'speaker_intensities': current_intensities,
                                        'chunk_num': chunks_processed
                                    }

                                    await self.sio.emit('tts_stream_audio_chunk', emit_data, room=session_id)

                                    chunks_processed += 1
                                    total_audio_processed += chunk_size
                                    chunk_position += chunk_size

                        except asyncio.TimeoutError:
                            await asyncio.sleep(0)
                            continue

                    elif background_queue:
                        min_bg_length = min(len(bg_data['audio']) for bg_data in background_queue)
                        if min_bg_length == 0:
                            background_queue = [bg for bg in background_queue if len(bg['audio']) > 0]
                            if not background_queue:
                                break
                            continue

                        silent_main_audio = AudioSegment.silent(duration=min_bg_length)
                        chunk_position = 0

                        while chunk_position < len(silent_main_audio):
                            chunk_size = min(CHUNK_SIZE, len(silent_main_audio) - chunk_position)
                            current_chunk = silent_main_audio[chunk_position:chunk_position + chunk_size]
                            mixed_chunk = current_chunk
                            remaining_bg = []
                            bg_intensities = []

                            for bg in background_queue:
                                bg_audio = bg['audio']
                                bg_intensities.append(bg['speaker_intensities'])

                                if len(bg_audio) <= len(current_chunk):  # type: ignore
                                    mixed_chunk = mixed_chunk.overlay(bg_audio)  # type: ignore
                                else:
                                    current_bg = bg_audio[:len(current_chunk)]  # type: ignore
                                    remaining_bg.append({
                                        'audio': bg_audio[len(current_chunk):],  # type: ignore
                                        'speaker_intensities': bg['speaker_intensities']
                                    })
                                    mixed_chunk = mixed_chunk.overlay(current_bg)  # type: ignore

                            mixed_chunk = limit_peaks(mixed_chunk, MAX_PEAK_DB)  # type: ignore
                            background_queue = remaining_bg

                            if debug_export:
                                debug_audio += mixed_chunk

                            current_intensities = combine_intensities(
                                None,
                                [bg['speaker_intensities'] for bg in background_queue]
                            )

                            chunk_buffer = io.BytesIO()
                            await asyncio.to_thread(
                                mixed_chunk.export,
                                chunk_buffer,
                                format="webm",
                                codec="libopus",
                                parameters=[
                                    "-ac", "2",
                                    "-ar", "48000",
                                    "-b:a", "128k",
                                    "-f", "webm",
                                    "-cluster_size_limit", "4M",
                                    "-cluster_time_limit", "10000"
                                ]
                            )
                            chunk_data = chunk_buffer.getvalue()

                            b64_chunk = base64.b64encode(chunk_data).decode('utf-8')
                            emit_data = {
                                'user_id': user_id,
                                'tts_type': tts_type,
                                'unique_id': stream_id,
                                'chunk': b64_chunk,
                                'speaker_intensities': current_intensities,
                                'chunk_num': chunks_processed
                            }

                            await self.sio.emit('tts_stream_audio_chunk', emit_data, room=session_id)
                            chunks_processed += 1
                            chunk_position += chunk_size

                    else:
                        break

                except Exception as e:
                    log_service.error(f"Audio Streaming: Error in broadcast_audio_stream: {str(e)}")
                    await asyncio.sleep(0.1)
                    continue
        finally:
            log_service.tts_broadcast(
                f"Audio Streaming: Stream {stream_id} complete. Processed {chunks_processed} chunks, {total_audio_processed}ms of audio")

            if debug_export and debug_audio:
                debug_file_path = f'audio_debugging/stream_{stream_id}.wav'
                await asyncio.to_thread(debug_audio.export, debug_file_path, format='wav')
                log_service.tts_broadcast(f"Audio Streaming: Exported debug audio to {debug_file_path}")

            await self.sio.emit('tts_stream_end', {
                'session_id': session_id,
                'tts_type': tts_type,
                'unique_id': stream_id
            }, room=session_id)