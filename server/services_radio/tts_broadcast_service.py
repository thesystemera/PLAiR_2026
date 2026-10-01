from pydub import AudioSegment
from typing import Dict, List, Optional, Tuple

CHUNK_SIZE = 8192
CLIP_CHUNK_MS = 1000

def chunk_span(current_pos: int, audio_length: int, intensities) -> Tuple[int, bool]:
    if not isinstance(intensities, list):
        return min(CHUNK_SIZE, audio_length - current_pos), CHUNK_SIZE >= audio_length - current_pos
    cumulative_time = 0
    for segment in intensities:
        segment_end = cumulative_time + segment['duration']
        if cumulative_time <= current_pos < segment_end:
            next_boundary = segment_end
            if next_boundary - current_pos < CHUNK_SIZE:
                return next_boundary - current_pos, False
            break
        cumulative_time += segment['duration']
    return min(CHUNK_SIZE, audio_length - current_pos), CHUNK_SIZE >= audio_length - current_pos

def intensities_at(position: int, intensities) -> Dict:
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

class TimelineMixer:
    def __init__(self):
        self.pending_background: List[Dict] = []
        self.background_queue: List[Dict] = []
        self.chunks_mixed = 0
        self.audio_mixed_ms = 0

    def add_background(self, audio: AudioSegment, speaker_intensities: Dict):
        self.pending_background.append({
            'audio': audio,
            'speaker_intensities': speaker_intensities
        })

    def begin_main(self):
        self.background_queue.extend(self.pending_background)
        self.pending_background = []

    def _mix_chunk(self, current_chunk: AudioSegment) -> AudioSegment:
        mixed_chunk = current_chunk
        remaining_bg = []
        for bg in self.background_queue:
            bg_audio = bg['audio']
            if len(bg_audio) <= len(current_chunk):
                mixed_chunk = mixed_chunk.overlay(bg_audio)
            else:
                current_bg = bg_audio[:len(current_chunk)]
                remaining_bg.append({
                    'audio': bg_audio[len(current_chunk):],
                    'speaker_intensities': bg['speaker_intensities']
                })
                mixed_chunk = mixed_chunk.overlay(current_bg)
        self.background_queue = remaining_bg
        self.chunks_mixed += 1
        self.audio_mixed_ms += len(current_chunk)
        return mixed_chunk

    def mix_main(self, audio_segment: AudioSegment, speaker_intensities, start: int = 0,
                 safe_end: Optional[int] = None) -> Tuple[List[Tuple[AudioSegment, Dict]], int]:
        chunks = []
        chunk_position = start
        audio_length = len(audio_segment)
        while chunk_position < audio_length:
            chunk_size, by_length = chunk_span(chunk_position, audio_length, speaker_intensities)
            if safe_end is not None and (by_length or chunk_position + chunk_size > safe_end):
                break
            mixed_chunk = self._mix_chunk(audio_segment[chunk_position:chunk_position + chunk_size])
            chunks.append((mixed_chunk, combine_intensities(
                intensities_at(chunk_position, speaker_intensities),
                [bg['speaker_intensities'] for bg in self.background_queue]
            )))
            chunk_position += chunk_size
        return chunks, chunk_position

    def mix_tail(self) -> List[Tuple[AudioSegment, Dict]]:
        self.begin_main()
        chunks = []
        while self.background_queue:
            min_bg_length = min(len(bg_data['audio']) for bg_data in self.background_queue)
            if min_bg_length == 0:
                self.background_queue = [bg for bg in self.background_queue if len(bg['audio']) > 0]
                continue

            silent_main_audio = AudioSegment.silent(duration=min_bg_length)
            chunk_position = 0
            while chunk_position < len(silent_main_audio):
                chunk_size = min(CHUNK_SIZE, len(silent_main_audio) - chunk_position)
                mixed_chunk = self._mix_chunk(silent_main_audio[chunk_position:chunk_position + chunk_size])
                chunks.append((mixed_chunk, combine_intensities(
                    None,
                    [bg['speaker_intensities'] for bg in self.background_queue]
                )))
                chunk_position += chunk_size
        return chunks

class AudioBroadcastService:
    def __init__(self, sio):
        self.sio = sio

    async def broadcast_cancel(self, room: str):
        await self.sio.emit('tts_stream_cancel', {
            'session_id': room
        }, room=room)
