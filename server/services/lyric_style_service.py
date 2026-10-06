"""Lyric styles: one DeepSeek pass per song designs how its sung words are set in the background scene.

The words keep their own timing (the lyric timing file); the style only groups them into cards (words that build up
on screen as they are sung, then give way to the next card) and gives each card and word a layout, font, size, weight,
case and tilt from the shared font list (`client/src/lib/lyricFonts.json`). Saved per track in `LYRIC_STYLES_DIR`
with a hash of the words it was made for, and served inside the lyric timing response as `style` while the words
still match.
"""
import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Optional

from config import settings
from config.settings import BASE_DIR
from services import llm_router, log_service
from services.track_asset_stages import track_lyrics_text

FONTS = json.loads((BASE_DIR / "client" / "src" / "lib" / "lyricFonts.json").read_text(encoding="utf-8"))["fonts"]
FONT_IDS = {font["id"]: font for font in FONTS}
LAYOUTS = ("stack", "flow", "cascade", "solo")
ALIGNS = ("left", "center", "right")
CASES = ("upper", "lower", "title")
WEIGHTS = {"thin": 200, "light": 300, "regular": 400, "bold": 700, "black": 900}
SIZES = (1, 6)
MAX_TILT = 8
CARD_WORDS = 10
HELD_S = 0.8
GAP_S = 2.0
SOUND_CHARS = 400
ATTEMPTS = 2
WRITTEN_LOOKAHEAD = 8
EDGE_PUNCTUATION = "'.,!?;:\"()[]{}“”‘’…-–—*_"

PROMPT = """Design the on-screen lyrics for this song's music video, the way a kinetic typography designer would.

The sung words appear over the video in sync with the voice. They are grouped into cards: a card is a short group of
words that stays on screen while it is sung, each word appearing the moment it is sung, until the next card replaces
it. You design the typography: how the words group into cards, how each card breaks into lines, and the font, size
and treatment of every word, so the type itself shows what the words mean and how they are sung.

SONG
{song}

FONTS (use only these ids)
{fonts}

LAYOUTS
- stack: the signature look of kinetic type. Lines stacked tight and every line stretched to the same width, so a
  short line becomes huge: "WE ARE / NEVER GOING / HOME" makes HOME enormous. For punchy lines of 2-6 words.
- flow: lines aligned left, centre or right, each word at its own size. For longer phrases and mixed sizes.
- cascade: each line steps further across the screen like a staircase. For falling, building, lists, momentum.
- solo: one to three words, very big, one line. For a held note, the hook, a shout, a single image.

SIZES: 1 (small: the, a, of, and, to) to 6 (huge). Sizes are relative inside a card; in stack the line width rules.
WEIGHTS: thin, light, regular, bold, black (a font only goes as far as its own range).

HOW TO DESIGN
- Identity: pick 2-4 home fonts that fit the genre, sound and mood; they carry most cards. Beyond them, use other
  fonts across the song for single words whose meaning calls for them (a whisper in a delicate italic serif, a
  scream in a heavy condensed sans, a memory on a typewriter, a name in handwriting). A font change should mean
  something, and it should happen: a song set in one font throughout is a missed chance.
- Cards follow the phrasing: most cards have 2-6 words, at most {max_words} for a fast run. Start a new card where
  the singer breathes or the thought turns. A held word or the hook can be a solo card.
- Lines: a card of 4 or more words has 2-4 lines. Break where a typographer would, so the key word gets its own line
  or ends one. A typographer never sets eight words on one line.
- Contrast: in each card one or two words carry it (size 5-6, bolder, or set apart) and the rest stay at 1-3.
  Enlarging every other word is noise, not emphasis.
- Rhythm: consecutive cards should not look alike. Within a verse, move between layouts and alignments (a left
  flow, then a stack, then a right cascade) and let the weight and case shift with the voice. Use all four layouts
  across the song.
- Motifs: a line that comes back (the chorus, a refrain, the title) keeps the same design every time it returns.
- Case: upper for shouts and the hook, lower for intimacy, title for names and beginnings.
- Tilt a card (-{tilt} to {tilt} degrees) now and then, for motion or unease. Most cards stay level.

EXAMPLES (a different song, to show the format and the thinking)
Lyrics: [42.0s] 100:I 101:keep 102:on 103:falling 104:down 105:into 106:the 107:blue(2.1s)
        [61.3s] (gap 9.1s) 108:we 109:are 110:never 111:going 112:home
        [88.0s] 113:hold 114:me 115:like 116:you 117:used 118:to
Cards:
 {{"start": 100, "layout": "cascade", "align": "left", "font": "oswald", "weight": "light", "case": "lower",
   "size": 2, "breaks": [103, 104], "words": {{"103": {{"size": 5, "weight": "bold"}}}}}}
 {{"start": 105, "layout": "flow", "align": "center", "font": "playfair", "weight": "regular", "case": "lower",
   "size": 2, "italic": true, "breaks": [107], "words": {{"107": {{"size": 6, "weight": "black", "italic": false}}}}}}
 {{"start": 108, "layout": "stack", "align": "center", "font": "anton", "case": "upper", "size": 3,
   "breaks": [110, 112]}}
 {{"start": 113, "layout": "flow", "align": "left", "font": "cormorant", "weight": "light", "case": "lower",
   "size": 3, "italic": true, "tilt": -3, "breaks": [115], "words": {{"113": {{"size": 5}},
   "116": {{"font": "caveat", "size": 4, "italic": false}}}}}}

LYRICS
Every word carries its number: 12:night is word 12. Numbers start at 0. Each line starts with its time; (1.2s)
after a word means it is held that long, and (gap 6.0s) marks a pause before a line.
{lyrics}

Reply with JSON only:
{{"look": "<two or three sentences: the song's visual language and how verses, chorus and bridge differ>",
 "home_fonts": ["<font id>", ...],
 "cards": [{{"start": <number of the card's first word>, "layout": "stack|flow|cascade|solo",
            "align": "left|center|right", "font": "<font id>", "weight": "<weight>", "case": "upper|lower|title",
            "size": <1-6>, "italic": false, "tilt": 0,
            "breaks": [<numbers of the words that begin a new line>],
            "words": {{"<word number>": {{"font": "<font id>", "size": 4, "weight": "black", "case": "upper",
                                         "italic": true}}}}}}]}}
The first card starts at word 0 and each card runs until the next card's start; the last card ends with word {last}.
"words" lists only the words that differ from their card, with only the fields that differ."""


def _plain(text: str) -> str:
    return re.sub(r"[^\w]", "", text.lower())


def _written_tokens(text: str) -> list[str]:
    tokens = [token.strip(EDGE_PUNCTUATION) for token in re.sub(r"\[.*?]", " ", text or "").split()]
    return [token for token in tokens if _plain(token)]


def _as_written(words: list[str], tokens: list[str]) -> list[str]:
    """Each timed word as the lyric sheet writes it (case, apostrophes, inner hyphens), found by walking the sheet in
    order; a word the sheet doesn't have nearby keeps its timed form."""
    shown, position = [], 0
    for word in words:
        target = _plain(word)
        match = next((index for index in range(position, min(len(tokens), position + WRITTEN_LOOKAHEAD))
                      if _plain(tokens[index]) == target), None)
        if match is None:
            shown.append(word)
        else:
            shown.append(tokens[match])
            position = match + 1
    return shown


def timed_words(timing: dict, written: str = "") -> list[dict]:
    words = []
    for line_index, line in enumerate(timing.get("lyrics") or []):
        for position, word in enumerate(line.get("words") or []):
            words.append({"text": word.get("word") or "", "start": float(word.get("start") or 0),
                          "end": float(word.get("end") or 0), "line": line_index, "first": position == 0})
    sheet = written or " ".join(line.get("line") or "" for line in timing.get("lyrics") or [])
    for word, shown in zip(words, _as_written([word["text"] for word in words], _written_tokens(sheet))):
        word["shown"] = shown
    return words


def words_hash(words: list[dict]) -> str:
    return hashlib.sha1(" ".join(word["text"] for word in words).encode("utf-8")).hexdigest()[:16]


def style_path(track_id: str):
    return settings.LYRIC_STYLES_DIR / f"{track_id}.json"


def attach_style(track_id: str, timing: dict) -> None:
    style = load_style(track_id, timing)
    if style:
        timing["style"] = style


def load_style(track_id: str, timing: dict) -> Optional[dict]:
    path = style_path(track_id)
    if not path.is_file():
        return None
    style = json.loads(path.read_text(encoding="utf-8"))
    if style.get("version") != settings.LYRIC_STYLE_VERSION:
        return None
    if style.get("words_hash") != words_hash(timed_words(timing)):
        return None
    return style


def _song_brief(metadata: dict) -> str:
    params, derived = metadata.get("generation_params") or {}, metadata.get("derived_tags") or {}
    sound = " ".join((params.get("style_canonical") or params.get("style") or "").split())[:SOUND_CHARS]
    rows = [
        ("Title", params.get("title") or (metadata.get("track_info") or {}).get("title")),
        ("Artist", (log_service.track_artists(metadata) or [""])[0]),
        ("Genre", ", ".join(filter(None, [derived.get("primary_genre"), *(derived.get("secondary_genres") or [])]))),
        ("Mood", ", ".join(derived.get("mood_keywords") or [])),
        ("Vocals", ", ".join(derived.get("vocal_style_keywords") or [])),
        ("About", derived.get("lyrical_interpretation")),
        ("Sound", sound),
    ]
    return "\n".join(f"{label}: {value}" for label, value in rows if value)


def _font_list() -> str:
    lines = []
    for font in FONTS:
        weights = font["weights"]
        span = f"weight {weights[0]}" if weights[0] == weights[1] else f"weights {weights[0]}-{weights[1]}"
        lines.append(f"- {font['id']}: {font['about']} ({span}{', italic' if font['italic'] else ''})")
    return "\n".join(lines)


def _lyric_lines(words: list[dict]) -> str:
    lines, current, previous_end = [], None, None
    for index, word in enumerate(words):
        if word["line"] != current:
            current = word["line"]
            gap = word["start"] - previous_end if previous_end is not None else word["start"]
            lines.append(f"[{word['start']:.1f}s]" + (f" (gap {gap:.1f}s)" if gap >= GAP_S else ""))
        held = word["end"] - word["start"]
        lines[-1] += f" {index}:{word['shown']}" + (f"({held:.1f}s)" if held >= HELD_S and not word["first"] else "")
        previous_end = word["end"]
    return "\n".join(lines)


def _weight(value: Any, font: dict, fallback: int, notes: list, where: str) -> int:
    if value is None:
        weight = fallback
    elif isinstance(value, (int, float)):
        weight = int(value)
    elif str(value).lower() in WEIGHTS:
        weight = WEIGHTS[str(value).lower()]
    else:
        notes.append(f"{where}: weight must be one of {', '.join(WEIGHTS)}")
        weight = fallback
    low, high = font["weights"]
    return max(low, min(high, weight))


def _treatment(raw: dict, base: dict, notes: list, where: str) -> dict:
    font_id = raw.get("font", base.get("font"))
    if font_id not in FONT_IDS:
        notes.append(f"{where}: unknown font {font_id!r}")
        font_id = base.get("font") if base.get("font") in FONT_IDS else FONTS[0]["id"]
    font = FONT_IDS[font_id]
    case = raw.get("case", base.get("case", "lower"))
    if case not in CASES:
        notes.append(f"{where}: case must be one of {', '.join(CASES)}")
        case = base.get("case", "lower")
    try:
        size = int(raw.get("size", base.get("size", 3)))
    except (TypeError, ValueError):
        notes.append(f"{where}: size must be a number from {SIZES[0]} to {SIZES[1]}")
        size = base.get("size", 3)
    if not SIZES[0] <= size <= SIZES[1]:
        notes.append(f"{where}: size {size} is outside {SIZES[0]}-{SIZES[1]}")
        size = max(SIZES[0], min(SIZES[1], size))
    weight = _weight(raw.get("weight"), font, base.get("weight", 400), notes, where)
    italic = bool(raw.get("italic", base.get("italic", False))) and font["italic"]
    return {"font": font_id, "size": size, "weight": weight, "case": case, "italic": italic}


def validate(reply: dict, word_count: int) -> tuple[list[dict], list[str], list[str]]:
    """Cards that tile every word in order are required (errors, worth a retry); a slip inside a card falls
    back to the card's own treatment and is only noted."""
    errors: list[str] = []
    notes: list[str] = []
    raw_cards = reply.get("cards") if isinstance(reply, dict) else None
    if not isinstance(raw_cards, list) or not raw_cards:
        return [], ["the reply has no cards"], notes
    starts = []
    for number, card in enumerate(raw_cards):
        try:
            starts.append(int(card.get("start")))
        except (TypeError, ValueError, AttributeError):
            return [], [f"card {number}: start must be a word number"], notes
    if starts[0] != 0:
        errors.append("the first card must start at word 0")
    if any(b <= a for a, b in zip(starts, starts[1:])):
        errors.append("card starts must increase")
    if starts[-1] >= word_count:
        errors.append(f"the last card starts at {starts[-1]}, past the last word {word_count - 1}")
    if errors:
        return [], errors, notes
    cards = []
    for number, (card, start) in enumerate(zip(raw_cards, starts)):
        end = (starts[number + 1] if number + 1 < len(starts) else word_count) - 1
        where = f"card {number} (words {start}-{end})"
        layout = card.get("layout", "flow")
        if layout not in LAYOUTS:
            notes.append(f"{where}: layout must be one of {', '.join(LAYOUTS)}")
            layout = "flow"
        align = card.get("align", "center")
        if align not in ALIGNS:
            notes.append(f"{where}: align must be one of {', '.join(ALIGNS)}")
            align = "center"
        base = _treatment(card, {"font": card.get("font"), "weight": 400}, notes, where)
        try:
            tilt = float(card.get("tilt") or 0)
        except (TypeError, ValueError):
            tilt = 0.0
        breaks = []
        for value in card.get("breaks") or []:
            try:
                index = int(value)
            except (TypeError, ValueError):
                notes.append(f"{where}: break {value!r} is not a word number")
                continue
            if not start < index <= end:
                notes.append(f"{where}: break {index} is not inside the card (a break is a word after the first)")
                continue
            breaks.append(index)
        overrides = {}
        for key, raw in (card.get("words") or {}).items():
            try:
                index = int(key)
            except (TypeError, ValueError):
                notes.append(f"{where}: word key {key!r} is not a word number")
                continue
            if not start <= index <= end or not isinstance(raw, dict):
                notes.append(f"{where}: word {index} is not in this card")
                continue
            treatment = _treatment(raw, base, notes, f"{where} word {index}")
            changed = {k: v for k, v in treatment.items() if v != base[k]}
            if changed:
                overrides[str(index)] = changed
        cards.append({"start": start, "end": end, "layout": layout, "align": align, **base,
                      "tilt": max(-MAX_TILT, min(MAX_TILT, round(tilt, 1))), "breaks": sorted(set(breaks)),
                      "words": overrides})
    return cards, errors, notes


async def build_style(track_id: str, metadata: dict, timing: dict, *, model: Optional[str] = None) -> dict:
    words = timed_words(timing, track_lyrics_text(metadata))
    if not words:
        raise ValueError(f"{track_id} has no timed words")
    model = model or next((name for provider, name in llm_router.resolve_llm(llm_router.LLM_INTERPRET)
                           if provider == "deepseek"), None)
    if not model:
        raise RuntimeError("No DeepSeek model in LLM_INTERPRET")
    prompt = PROMPT.format(song=_song_brief(metadata), fonts=_font_list(), lyrics=_lyric_lines(words),
                           max_words=CARD_WORDS, tilt=MAX_TILT, last=len(words) - 1)
    messages = [{"role": "system", "content": "You are a kinetic typography designer for music videos."},
                {"role": "user", "content": prompt}]
    usage, problems, errors, notes, cards, reply = [], [], [], [], [], {}
    for attempt in range(ATTEMPTS):
        result = await llm_router.deepseek_chat(
            spec=llm_router.LLM_INTERPRET, model=model, temperature=0.7, json_mode=True,
            max_tokens=settings.LYRIC_STYLE_MAX_TOKENS, timeout=settings.LYRIC_STYLE_TIMEOUT_S, messages=messages)
        usage.append(result.get("usage"))
        if result.get("finish_reason") == "length":
            raise RuntimeError(f"{track_id}: the style ran past {settings.LYRIC_STYLE_MAX_TOKENS} tokens")
        reply = llm_router.parse_llm_json(result["text"], {})
        cards, errors, notes = validate(reply, len(words))
        if cards and not errors:
            break
        problems.append(errors)
        log_service.warning(f"[LyricStyle] {track_id} attempt {attempt + 1}: {len(errors)} problem(s): "
                            f"{'; '.join(errors[:5])}")
        messages += [{"role": "assistant", "content": result["text"]},
                     {"role": "user", "content": "Your reply had these problems:\n- " + "\n- ".join(errors[:30]) +
                                                 "\nReply with the full corrected JSON."}]
    if not cards or errors:
        raise RuntimeError(f"{track_id}: no valid style after {ATTEMPTS} attempts: {'; '.join(errors[:5])}")
    home = [font for font in reply.get("home_fonts") or [] if font in FONT_IDS]
    style = {"version": settings.LYRIC_STYLE_VERSION, "track_id": track_id, "words_hash": words_hash(words),
             "word_count": len(words), "text": [word["shown"] for word in words], "model": model,
             "created_at": datetime.now(timezone.utc).isoformat(),
             "look": str(reply.get("look") or ""), "home_fonts": home, "cards": cards}
    style_path(track_id).write_text(json.dumps(style, ensure_ascii=False, indent=1), encoding="utf-8")
    log_service.info(f"[LyricStyle] {track_id}: {len(cards)} cards for {len(words)} words, "
                     f"home fonts {', '.join(home) or '-'}, {len(notes)} note(s) ({model})")
    return {"style": style, "usage": usage, "problems": problems, "notes": notes}
