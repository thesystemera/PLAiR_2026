import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings
from services import lyric_style_service
from services.llm_telemetry import PRICING

COST_PER_SONG_USD = 0.01
SECONDS_PER_SONG = 75


def cost_usd(model: str, usage: list) -> float:
    price_in, price_out, price_cached = PRICING.get(model, (0.0, 0.0, 0.0))
    total = 0.0
    for item in filter(None, usage):
        fresh = item["prompt_tokens"] - item["cached_tokens"]
        total += (fresh * price_in + item["cached_tokens"] * price_cached + item["output_tokens"] * price_out) / 1e6
    return total


def show(style: dict) -> None:
    text = style["text"]
    for card in style["cards"]:
        words = []
        for index in range(card["start"], card["end"] + 1):
            prefix = " / " if index in card["breaks"] else " "
            override = card["words"].get(str(index))
            words.append(prefix + text[index] + (f"<{','.join(f'{k}={v}' for k, v in override.items())}>"
                                                 if override else ""))
        print(f"  {card['start']:>4} {card['layout']:7} {card['font']:16} w{card['weight']} s{card['size']} "
              f"{card['case']:5}{' tilt ' + str(card['tilt']) if card['tilt'] else ''}:{''.join(words)}")


def report(track_id: str, result: dict, cards: bool) -> None:
    style = result["style"]
    fonts = Counter(card["font"] for card in style["cards"])
    fonts.update(word["font"] for card in style["cards"] for word in card["words"].values() if "font" in word)
    layouts = Counter(card["layout"] for card in style["cards"])
    output = sum(item["output_tokens"] for item in filter(None, result["usage"]))
    print(f"{track_id}: {len(style['cards'])} cards for {style['word_count']} words, {len(result['usage'])} "
          f"call(s), {output} output tokens, ${cost_usd(style['model'], result['usage']):.4f}")
    for number, errors in enumerate(result["problems"], 1):
        print(f"  attempt {number} had {len(errors)} problem(s): {'; '.join(errors[:6])}")
    if result["notes"]:
        print(f"  {len(result['notes'])} note(s): {'; '.join(result['notes'][:6])}")
    if cards:
        print(f"  look: {style['look']}")
        print(f"  home fonts {style['home_fonts']}; fonts {dict(fonts)}; layouts {dict(layouts)}")
        show(style)


async def main():
    parser = argparse.ArgumentParser(description="Design lyric styles (cards, fonts, sizes) with one DeepSeek call "
                                                 "per song (LLM_INTERPRET, never Gemini). New lyric timings get one "
                                                 "automatically; this styles chosen songs or the ones without one")
    parser.add_argument("--track", action="append", help="design this track again (repeat for more tracks)")
    parser.add_argument("--missing", action="store_true",
                        help="every song with timed lyrics and no current style, super-liked first, then liked")
    parser.add_argument("--limit", type=int, default=0, help="with --missing: at most N songs this run")
    parser.add_argument("--apply", action="store_true", help="with --missing: design them (default: count and cost only)")
    parser.add_argument("--show", action="store_true", help="with --track: print every card")
    args = parser.parse_args()
    if args.track:
        for track_id in args.track:
            metadata = json.loads((settings.METADATA_DIR / f"{track_id}.json").read_text(encoding="utf-8"))
            timing = json.loads((settings.LYRIC_TIMESTAMPS_DIR / f"{track_id}.json").read_text(encoding="utf-8"))
            report(track_id, await lyric_style_service.build_style(track_id, metadata, timing), args.show)
        return
    if not args.missing:
        parser.error("give --track ID or --missing")
    track_ids = lyric_style_service.missing_styles()
    if args.limit:
        track_ids = track_ids[:args.limit]
    slots = settings.LYRIC_STYLE_CONCURRENCY
    print(f"{len(track_ids)} song(s) with timed lyrics and no style: about ${len(track_ids) * COST_PER_SONG_USD:.2f} "
          f"and {len(track_ids) * SECONDS_PER_SONG / slots / 3600:.1f} h at {slots} at a time")
    if not args.apply:
        print("Dry run: add --apply to design them.")
        return
    done = [0]

    def on_done(track_id: str, result):
        done[0] += 1
        print(f"[{done[0]}/{len(track_ids)}] " + (f"{track_id}: {len(result['style']['cards'])} cards, "
                                                   f"${cost_usd(result['style']['model'], result['usage']):.4f}"
                                                   if result else f"{track_id}: failed (see radio.log)"), flush=True)

    counts = await lyric_style_service.backfill(track_ids, on_done)
    print(f"Done: {counts}")


if __name__ == "__main__":
    asyncio.run(main())
