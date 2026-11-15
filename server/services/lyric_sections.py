import re
from difflib import SequenceMatcher
from typing import Any, Dict, List

SECTION_PATTERN = re.compile(r'\[(Intro|Verse|Chorus|Bridge|Outro|Breakdown|Instrumental|Pre-Chorus)[^]]*?\]', re.IGNORECASE)
MIN_SECTION_S = 0.5


def _tokens(text: str) -> List[str]:
    text = re.sub(r'[^\w\s]', '', text.lower())
    return re.sub(r'\s+', ' ', text).strip().split()


def _blocks(prompt: str):
    blocks: List[Dict[str, Any]] = []
    tokens: List[str] = []
    for raw in prompt.split('\n'):
        marker = SECTION_PATTERN.search(raw)
        if marker:
            blocks.append({'type': marker.group(1).lower(), 'marker': marker.group(0), 'first': len(tokens), 'count': 0})
            continue
        line = _tokens(re.sub(r'\(.*?\)', '', re.sub(r'\[.*?]', '', raw)))
        if not line:
            continue
        if not blocks:
            blocks.append({'type': 'verse', 'marker': '', 'first': len(tokens), 'count': 0})
        tokens.extend(line)
        blocks[-1]['count'] += len(line)
    return blocks, tokens


def timed_lyric_sections(prompt: str, words: List[Dict[str, Any]], duration: float) -> List[Dict[str, Any]]:
    """Places the lyrics' own section markers ([Verse 1], [Chorus], [Breakdown]...) in time by matching the
    lyric text against the timed words. A section without sung words starts where the last sung word before it ends."""
    blocks, tokens = _blocks(prompt or '')
    if not blocks:
        return []
    sung = [(token, float(word.get('start', 0)), float(word.get('end', 0))) for word in words or [] for token in _tokens(word.get('word', ''))]
    matched: Dict[int, int] = {}
    matcher = SequenceMatcher(None, tokens, [token for token, _, _ in sung], autojunk=False)
    for a, b, size in matcher.get_matching_blocks():
        for k in range(size):
            matched[a + k] = b + k
    out: List[Dict[str, Any]] = []
    previous_end = 0.0
    for index, block in enumerate(blocks):
        hits = [matched[i] for i in range(block['first'], block['first'] + block['count']) if i in matched]
        start = 0.0 if index == 0 else (sung[hits[0]][1] if hits else previous_end)
        if out and start < out[-1]['start']:
            start = out[-1]['start']
        out.append({'type': block['type'], 'marker': block['marker'], 'start': round(start, 2), 'sung': bool(hits)})
        if hits:
            previous_end = sung[hits[-1]][2]
    for index, section in enumerate(out):
        section['end'] = out[index + 1]['start'] if index + 1 < len(out) else round(float(duration or section['start']), 2)
    return [section for section in out if section['end'] - section['start'] > MIN_SECTION_S]
