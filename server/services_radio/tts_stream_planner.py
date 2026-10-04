import re
from typing import List, Dict
from config.settings import settings
from services import log_service

CHANNEL_TAG = re.compile(r'(\[BROADCAST]|\[TXT])')
STATION_NAME = re.compile(r'\bpla(?:y ?a)?ir(\.fm|\s+fm)?\b', re.IGNORECASE)
TIME_SHIFT = re.compile(r'@([WwCc]?)(\d+(?:\.\d+)?)@')


def spoken_text(text: str) -> str:
    """What airs: only [BROADCAST] sections are spoken; [TXT] is a text message. Untagged text is spoken."""
    if not CHANNEL_TAG.search(text or ""):
        return (text or "").strip()
    spoken, channel = [], None
    for part in CHANNEL_TAG.split(text):
        if part in ('[BROADCAST]', '[TXT]'):
            channel = part
        elif part.strip() and channel == '[BROADCAST]':
            spoken.append(part.strip())
    return " ".join(spoken)


def say_station_name(match: re.Match) -> str:
    """The station name as the voice engine should read it: 'PLAiR.fm' -> 'Playar F M'."""
    return settings.STATION_NAME_SPOKEN + (" F M" if match.group(1) else "")


class TTSStreamPlanner:
    def split_text_into_sentences(self, text: str) -> List[Dict]:
        log_service.detail(f"Sentence Splitter: Original text: {text}", "tts_stream_planner")

        text = STATION_NAME.sub(say_station_name, spoken_text(text))

        first_speaker_match = re.search(r'\[(LEO|JESS)]', text)
        if not first_speaker_match:
            error_msg = f"NO SPEAKER TAG FOUND! Text must start with [LEO] or [JESS]. Text: {text[:100]}"
            log_service.error(error_msg)
            raise ValueError(error_msg)

        current_speaker = first_speaker_match.group(1).lower()
        log_service.detail(f"Initial speaker extracted from text: {current_speaker}", "tts_stream_planner")

        parts = re.split(
            r'(~[^~]+~|%[^%]+%|\[LEO]|\[JESS]|\$[^$]+\$|@[WwCc]?\d+(?:\.\d+)?@|&\d+(?:\.\d+)?&|(?<![A-Z]\.)(?<=[.!?])\s+)',
            text
        )

        ordered_content = []
        current_sentence = ""
        current_overlap = 0
        current_overlap_unit = 'w'
        pending_proximity = None
        proximity = {}

        def process_content(part):
            overlap = 0
            audio_process = None
            content = part
            unit = 'w'
            if part.startswith('@') and part.endswith('@'):
                shift = TIME_SHIFT.fullmatch(part)
                if shift:
                    unit = 'c' if shift.group(1).lower() == 'c' else 'w'
                    overlap = float(shift.group(2))
                    content = ""
            elif part.startswith('&') and part.endswith('&'):
                try:
                    audio_process = float(part[1:-1])
                    content = ""
                except ValueError:
                    pass
            return content.strip(), overlap, audio_process, unit

        def emit(kind, content, speaker):
            nonlocal current_overlap, current_overlap_unit, pending_proximity
            if pending_proximity is not None:
                mix = pending_proximity
            else:
                mix = proximity.get(current_speaker if speaker == 'computer' else speaker, 0.0)
            if speaker != 'computer':
                proximity[speaker] = mix
            ordered_content.append({
                'type': kind,
                'content': content,
                'speaker': speaker,
                'block_speaker': current_speaker,
                'overlap': current_overlap,
                'overlap_unit': current_overlap_unit,
                'char_count': len(content),
                'audio_process': mix
            })
            current_overlap = 0
            current_overlap_unit = 'w'
            pending_proximity = None

        def flush_sentence():
            nonlocal current_sentence
            if current_sentence:
                emit('sentence', current_sentence.strip(), current_speaker)
                current_sentence = ""

        for part in parts:
            content, overlap, audio_process, unit = process_content(part)

            if audio_process is not None:
                pending_proximity = audio_process
                continue

            if overlap > 0:
                current_overlap = overlap
                current_overlap_unit = unit
                continue

            if part in ['[JESS]', '[LEO]']:
                flush_sentence()
                current_speaker = part[1:-1].lower()
            elif part.startswith('~') and part.endswith('~'):
                flush_sentence()
                emit('paralanguage', part.strip('~'), current_speaker)
            elif part.startswith('%') and part.endswith('%'):
                flush_sentence()
                emit('audio', part.strip('%'), 'computer')
            elif part.startswith('$') and part.endswith('$'):
                flush_sentence()
                emit('user_content', part.strip('$'), current_speaker)
            elif re.match(r'\s+', part) and current_sentence:
                flush_sentence()
            else:
                current_sentence += content if not current_sentence else " " + content

        flush_sentence()

        ordered_content = [item for item in ordered_content
                           if item['type'] != 'sentence' or re.search(r'[A-Za-z0-9]', item['content'])]

        final_content = []
        last_own = {}
        for item in ordered_content:
            speaker = item['speaker']
            previous = last_own.get(speaker)
            if item['type'] == 'sentence' and previous is not None and previous['type'] == 'sentence':
                final_content.append({
                    'type': 'breath',
                    'content': 'breath',
                    'context': previous['content'],
                    'speaker': speaker,
                    'overlap': 0,
                    'char_count': 2,
                    'audio_process': (previous['audio_process'] + item['audio_process']) / 2.0
                })
            final_content.append(item)
            if speaker != 'computer':
                last_own[speaker] = item

        log_service.detail(f"Sentence Splitter: Final segments: {final_content}", "tts_stream_planner")

        return final_content

    @staticmethod
    def _overlap_target(ordered_content: List[Dict], index: int):
        host = ordered_content[index].get('block_speaker', ordered_content[index]['speaker'])
        turn = []
        for j in range(index - 1, -1, -1):
            other = ordered_content[j]
            if other['speaker'] == host:
                if turn:
                    break
                continue
            if other['type'] == 'sentence' and other['speaker'] != 'computer':
                turn.append(other)
        if not turn:
            return None
        segment = ordered_content[index]
        if segment.get('overlap_unit') == 'c':
            back = int(round(segment['overlap']))
            for line in turn:
                if back <= len(line['content']):
                    return line['char_start'] + len(line['content']) - back
                back -= len(line['content']) + 1
            return turn[-1]['char_start']
        back = int(round(segment['overlap']))
        for line in turn:
            words = line['content'].split()
            if back <= len(words):
                kept = " ".join(words[:len(words) - back])
                return line['char_start'] + len(kept) + (1 if kept and back else 0)
            back -= len(words)
        return turn[-1]['char_start']

    def create_stream_plan(self, ordered_content: List[Dict]) -> List[Dict]:
        stream_plan = []
        total_chars = 0

        for i, segment in enumerate(ordered_content):
            segment['char_start'] = total_chars
            segment['char_end'] = total_chars + segment['char_count']
            total_chars = segment['char_end']
            segment['part_of_blend'] = False

        for i, segment in enumerate(ordered_content):
            if segment['overlap'] <= 0:
                continue
            target = self._overlap_target(ordered_content, i)
            if target is None:
                segment['overlap'] = 0
                continue
            segment['overlap_target'] = target
            window_start = target
            touched = []
            j = i - 1
            while j >= 0 and ordered_content[j]['char_end'] > window_start:
                touched.append(ordered_content[j])
                j -= 1
            if any(other['speaker'] != segment['speaker'] for other in touched):
                segment['part_of_blend'] = True
                for other in touched:
                    other['part_of_blend'] = True
            else:
                segment['overlap'] = 0

        i = 0
        assigned_to_blend = set()
        while i < len(ordered_content):
            segment = ordered_content[i]
            if segment['part_of_blend'] and i not in assigned_to_blend:
                blend_segments = []
                blend_start = segment['char_start'] - segment['overlap']
                blend_end = segment['char_end']
                while i < len(ordered_content) and ordered_content[i]['part_of_blend']:
                    if i not in assigned_to_blend:
                        next_segment = ordered_content[i]
                        blend_segments.append(next_segment)
                        blend_end = max(blend_end, next_segment['char_end'])
                        assigned_to_blend.add(i)
                    i += 1
                stream_plan.append({
                    'type': 'blend',
                    'segments': blend_segments,
                    'start_char': blend_start,
                    'end_char': blend_end
                })
            else:
                stream_plan.append({
                    'type': 'single',
                    'segment': segment,
                    'start_char': segment['char_start'],
                    'end_char': segment['char_end']
                })
                i += 1

        stream_plan.sort(key=lambda x: x['start_char'])

        return stream_plan