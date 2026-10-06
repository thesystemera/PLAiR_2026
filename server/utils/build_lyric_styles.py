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


async def main():
    parser = argparse.ArgumentParser(description="Design lyric styles (cards, fonts, sizes) for tracks with one "
                                                 "DeepSeek call each (LLM_INTERPRET, never Gemini)")
    parser.add_argument("--track", action="append", required=True, help="track id (repeat for more tracks)")
    parser.add_argument("--show", action="store_true", help="print every card")
    args = parser.parse_args()
    for track_id in args.track:
        metadata = json.loads((settings.METADATA_DIR / f"{track_id}.json").read_text(encoding="utf-8"))
        timing = json.loads((settings.LYRIC_TIMESTAMPS_DIR / f"{track_id}.json").read_text(encoding="utf-8"))
        result = await lyric_style_service.build_style(track_id, metadata, timing)
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
        print(f"  look: {style['look']}")
        print(f"  home fonts {style['home_fonts']}; fonts {dict(fonts)}; layouts {dict(layouts)}")
        if args.show:
            show(style)


if __name__ == "__main__":
    asyncio.run(main())
