import re
import random
from typing import List, Dict
from services import log_service

class TTSStreamPlanner:
    def split_text_into_sentences(self, text: str) -> List[Dict]:
        log_service.tts_stream_planner(f"Sentence Splitter: Original text: {text}")

        text = re.sub(r'\[(BROADCAST|TXT)]', '', text).strip()

        if text.startswith('[IMPULSE]'):
            current_speaker = random.choice(['terry', 'shaquille'])
            log_service.tts_stream_planner(f"IMPULSE detected - randomly chose: {current_speaker}")
        else:
            first_speaker_match = re.search(r'\[(SHAQUILLE|TERRY)]', text)
            if not first_speaker_match:
                error_msg = f"NO SPEAKER TAG FOUND! Text must start with [SHAQUILLE] or [TERRY]. Text: {text[:100]}"
                log_service.error(error_msg)
                raise ValueError(error_msg)

            current_speaker = first_speaker_match.group(1).lower()
            log_service.tts_stream_planner(f"Initial speaker extracted from text: {current_speaker}")

        parts = re.split(
            r'(\*[^*]+\*|%[^%]+%|\[IMPULSE][^\[]+\[/IMPULSE]|\[SHAQUILLE]|\[TERRY]|\$[^$]+\$|@\d+@|&\d+(?:\.\d+)?&|(?<![A-Z]\.)(?<=[.!?])\s+)',
            text
        )

        ordered_content = []
        current_sentence = ""
        current_overlap = 0
        current_audio_process = 0.0

        def process_content(part):
            overlap = 0
            audio_process = None
            content = part
            if part.startswith('@') and part.endswith('@'):
                try:
                    overlap = int(part[1:-1])
                    content = ""
                except ValueError:
                    pass
            elif part.startswith('&') and part.endswith('&'):
                try:
                    audio_process = float(part[1:-1])
                    content = ""
                except ValueError:
                    pass
            return content.strip(), overlap, audio_process

        for part in parts:
            content, overlap, audio_process = process_content(part)

            if audio_process is not None:
                current_audio_process = audio_process
                continue

            if overlap > 0:
                current_overlap = overlap
                continue

            if part in ['[TERRY]', '[SHAQUILLE]']:
                if current_sentence:
                    ordered_content.append({
                        'type': 'sentence',
                        'content': current_sentence.strip(),
                        'speaker': current_speaker,
                        'overlap': current_overlap,
                        'char_count': len(current_sentence.strip()),
                        'audio_process': current_audio_process
                    })
                    current_sentence = ""
                    current_overlap = 0
                    current_audio_process = 0.0
                current_speaker = part[1:-1].lower()
            elif part.startswith('*') and part.endswith('*'):
                if current_sentence:
                    ordered_content.append({
                        'type': 'sentence',
                        'content': current_sentence.strip(),
                        'speaker': current_speaker,
                        'overlap': current_overlap,
                        'char_count': len(current_sentence.strip()),
                        'audio_process': current_audio_process
                    })
                    current_sentence = ""
                    current_overlap = 0
                meta_tag = part.strip('*')
                ordered_content.append({
                    'type': 'meta',
                    'content': meta_tag,
                    'speaker': current_speaker,
                    'overlap': current_overlap,
                    'char_count': len(meta_tag),
                    'audio_process': current_audio_process
                })
                current_overlap = 0
                current_audio_process = 0.0
            elif part.startswith('%') and part.endswith('%'):
                if current_sentence:
                    ordered_content.append({
                        'type': 'sentence',
                        'content': current_sentence.strip(),
                        'speaker': current_speaker,
                        'overlap': current_overlap,
                        'char_count': len(current_sentence.strip()),
                        'audio_process': current_audio_process
                    })
                    current_sentence = ""
                    current_overlap = 0
                audio_tag = part.strip('%')
                ordered_content.append({
                    'type': 'audio',
                    'content': audio_tag,
                    'speaker': 'computer',
                    'overlap': current_overlap,
                    'char_count': len(audio_tag),
                    'audio_process': current_audio_process
                })
                current_overlap = 0
                current_audio_process = 0.0
            elif part.startswith('[IMPULSE]') and part.endswith('[/IMPULSE]'):
                if current_sentence:
                    ordered_content.append({
                        'type': 'sentence',
                        'content': current_sentence.strip(),
                        'speaker': current_speaker,
                        'overlap': current_overlap,
                        'char_count': len(current_sentence.strip()),
                        'audio_process': current_audio_process
                    })
                    current_sentence = ""
                    current_overlap = 0
                impulse_content = part[9:-10]
                ordered_content.append({
                    'type': 'impulse',
                    'content': impulse_content,
                    'speaker': current_speaker,
                    'overlap': current_overlap,
                    'char_count': len(impulse_content),
                    'audio_process': current_audio_process
                })
                current_overlap = 0
                current_audio_process = 0.0
            elif part.startswith('$') and part.endswith('$'):
                if current_sentence:
                    ordered_content.append({
                        'type': 'sentence',
                        'content': current_sentence.strip(),
                        'speaker': current_speaker,
                        'overlap': current_overlap,
                        'char_count': len(current_sentence.strip()),
                        'audio_process': current_audio_process
                    })
                    current_sentence = ""
                    current_overlap = 0
                user_content_file = part.strip('$')
                ordered_content.append({
                    'type': 'user_content',
                    'content': user_content_file,
                    'speaker': current_speaker,
                    'overlap': current_overlap,
                    'char_count': len(user_content_file),
                    'audio_process': current_audio_process
                })
                current_overlap = 0
                current_audio_process = 0.0
            elif re.match(r'\s+', part) and current_sentence:
                ordered_content.append({
                    'type': 'sentence',
                    'content': current_sentence.strip(),
                    'speaker': current_speaker,
                    'overlap': current_overlap,
                    'char_count': len(current_sentence.strip()),
                    'audio_process': current_audio_process
                })
                current_sentence = ""
                current_overlap = 0
                current_audio_process = 0.0
            else:
                current_sentence += content if not current_sentence else " " + content

        if current_sentence:
            ordered_content.append({
                'type': 'sentence',
                'content': current_sentence.strip(),
                'speaker': current_speaker,
                'overlap': current_overlap,
                'char_count': len(current_sentence.strip()),
                'audio_process': current_audio_process
            })

        final_content = []
        for i, item in enumerate(ordered_content):
            final_content.append(item)
            if (i < len(ordered_content) - 1 and
                    item['type'] == 'sentence' and
                    ordered_content[i + 1]['type'] == 'sentence' and
                    item['speaker'] == ordered_content[i + 1]['speaker']):
                preceding_process = item.get('audio_process', 0.0)
                next_process = ordered_content[i + 1].get('audio_process', 0.0)
                breath_process = (preceding_process + next_process) / 2.0

                final_content.append({
                    'type': 'breath',
                    'content': 'breath',
                    'speaker': item['speaker'],
                    'overlap': 0,
                    'char_count': 2,
                    'audio_process': breath_process
                })

        log_service.tts_stream_planner(f"Sentence Splitter: Final segments: {final_content}")

        return final_content

    def create_stream_plan(self, ordered_content: List[Dict]) -> List[Dict]:
        stream_plan = []
        total_chars = 0

        for i, segment in enumerate(ordered_content):
            segment['char_start'] = total_chars
            segment['char_end'] = total_chars + segment['char_count']
            total_chars = segment['char_end']
            segment['part_of_blend'] = False

        for i, segment in enumerate(ordered_content):
            overlap = segment['overlap']
            if overlap > 0:
                j = i - 1
                while j >= 0:
                    prev_segment = ordered_content[j]
                    if prev_segment['char_end'] > segment['char_start'] - overlap:
                        if prev_segment['speaker'] == segment['speaker']:
                            segment['overlap'] = max(0, segment['char_start'] - prev_segment['char_end'])
                        else:
                            segment['part_of_blend'] = True
                            prev_segment['part_of_blend'] = True
                        overlap = segment['overlap']
                    else:
                        break
                    j -= 1

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