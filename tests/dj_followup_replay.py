"""Replay the follow-up round of recorded DJ turns against Gemini with different studio messages.

After a tool call the hosts get the tool results plus a [STUDIO] message, with thinking off. This replays that
exact moment from data/logs/dj_turns.jsonl* with the plain "reply on air" message and, where it differs, the one
in use for that turn (the hand-off message after a scheduled segment). It counts how the reply opens: on a
channel tag (spoke), straight to the notes (notes only), or with stray text before any tag (PREFACE); ECHO
repeats the line that already aired and RECALL calls the same tool again.

Usage: python tests/dj_followup_replay.py <runs per arm> <turn_id> [<turn_id> ...]
Needs no backend and changes nothing, but it uses the station's Gemini quota: keep runs small while the
station is on air (40 runs x 6 turns x 4 arms caused a 429 on a live DJ turn on 2026-10-01).
"""
import asyncio
import glob
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

from google import genai  # noqa: E402
from google.genai import types  # noqa: E402

from config.settings import settings  # noqa: E402
from services_radio import context_nodes  # noqa: E402,F401
from services_radio.context_node_registry import node_registry  # noqa: E402
from services_radio.dj_prompt_helper_service import assemble_prompt  # noqa: E402
from services.ai_service import HANDED_OFF_NOTE, LINE_AIRED_NOTE  # noqa: E402
from services_radio.dj_tools_registry import DJ_FUNCTION_DECLARATIONS, SEGMENT_TOOLS  # noqa: E402

SYSTEM_NODES = ['core_dj_identity', 'format_channels', 'format_tone',
                'format_performance_tags_guide', 'format_performance_tag_examples', 'format_roles_detailed',
                'format_station_characteristics', 'format_dialogue_examples', 'guidelines_general',
                'guidelines_critical', 'guidelines_internal_dialogue', 'instruction_dj_tools', 'tool_guidance',
                'station_recent_airings', 'studio_clock', 'city_pulse']

ARMS = {
    "reply on air": (LINE_AIRED_NOTE, 0),
    "in use": (None, 0),
}


def note_in_use(turn) -> str:
    first = turn["rounds"][0]
    scheduled = all(call["name"] in SEGMENT_TOOLS for call in first["calls"]) and all(
        json.loads(result["result"]).get("status") == "scheduled" for result in first["results"])
    return HANDED_OFF_NOTE if scheduled else LINE_AIRED_NOTE
CHANNEL = re.compile(r"\[(BROADCAST|TXT)\]")


def load_turns(turn_ids):
    found = {}
    for path in sorted(glob.glob(str(ROOT / "data" / "logs" / "dj_turns.jsonl*"))):
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if record.get("turn_id") in turn_ids:
                    found[record["turn_id"]] = record
    return [found[turn_id] for turn_id in turn_ids if turn_id in found]


def classify(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return "empty"
    first = CHANNEL.search(stripped)
    notes = min((i for i in (stripped.find("[INTERNAL"), stripped.find("[TASK]")) if i >= 0), default=-1)
    if stripped.startswith("[BROADCAST]") or stripped.startswith("[TXT]"):
        return "spoke"
    if stripped.startswith("[INTERNAL") or stripped.startswith("[TASK]"):
        return "notes only" if first is None else "notes then spoke"
    return "PREFACE" + (" (notes marker inside)" if 0 <= notes < (first.start() if first else len(stripped)) else "")


async def main():
    runs = int(sys.argv[1])
    turns = load_turns(sys.argv[2:])
    system_keys = [key for key in SYSTEM_NODES if node_registry.is_system(key)]
    context = await node_registry.fetch_nodes(system_keys)
    system_prompt = assemble_prompt(context, system_keys, untrusted_keys=set(), note=None)
    print(f"system prompt: {len(system_prompt)} chars from {len(system_keys)} system nodes; "
          f"{len(DJ_FUNCTION_DECLARATIONS)} tools", flush=True)
    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    def config_for(budget):
        return types.GenerateContentConfig(
            temperature=settings.GEMINI_DJ_TEMPERATURE, max_output_tokens=settings.GEMINI_DJ_MAX_TOKENS,
            system_instruction=system_prompt,
            thinking_config=types.ThinkingConfig(thinking_budget=budget),
            tools=[types.Tool(function_declarations=DJ_FUNCTION_DECLARATIONS)],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))

    def key(text):
        plain = re.sub(r"\[[A-Z ]+\]|~[^~]*~|%[^%]*%|[@&][\d.]+[@&]|\(\d+ chars\)", " ", text)
        return " ".join(plain.split())[:50].lower()

    semaphore = asyncio.Semaphore(2)

    async def one(turn, marker, budget):
        first = turn["rounds"][0]
        model_parts = [types.Part.from_text(text=first["text"])] if first["text"].strip() else []
        model_parts += [types.Part.from_function_call(name=call["name"], args=call["args"]) for call in first["calls"]]
        reply_parts = [types.Part.from_function_response(name=result["name"], response=json.loads(result["result"]))
                       for result in first["results"]]
        if marker and first["text"].strip():
            reply_parts.append(types.Part.from_text(text=marker))
        contents = [types.Content(role="user", parts=[types.Part.from_text(text=turn["user_message"])]),
                    types.Content(role="model", parts=model_parts),
                    types.Content(role="user", parts=reply_parts)]
        async with semaphore:
            try:
                response = await client.aio.models.generate_content(model="gemini-2.5-flash", contents=contents,
                                                                    config=config_for(budget))
            except Exception as e:
                return f"error {type(e).__name__}", ""
        candidate = response.candidates[0] if response.candidates else None
        parts = list(candidate.content.parts) if candidate and candidate.content and candidate.content.parts else []
        text = "".join(p.text for p in parts if p.text and not getattr(p, "thought", False))
        called = [p.function_call.name for p in parts if p.function_call]
        label = classify(text)
        if text.strip() and key(text) == key(first["text"]):
            label += " ECHO"
        if any(name in [c["name"] for c in first["calls"]] for name in called):
            label += " RECALL"
        return label, text

    totals = {}
    for turn in turns:
        print(f"\n== {turn['turn_id']} | {turn['listener'][:70]} | tools {[c['name'] for c in turn['rounds'][0]['calls']]}")
        for name, (marker, budget) in ARMS.items():
            if marker is None:
                marker = note_in_use(turn)
                if marker == LINE_AIRED_NOTE:
                    continue
            results = await asyncio.gather(*(one(turn, marker, budget) for _ in range(runs)))
            counts = {}
            for label, _ in results:
                counts[label] = counts.get(label, 0) + 1
            print(f"  {name:24s} {dict(sorted(counts.items()))}", flush=True)
            for label, text in results:
                totals.setdefault(name, {})
                short = "PREFACE" if label.startswith("PREFACE") else label
                totals[name][short] = totals[name].get(short, 0) + 1
                if label.startswith("PREFACE"):
                    print("      >", " | ".join(text.strip()[:110].splitlines()))
    print()
    print("== TOTALS")
    for name, counts in totals.items():
        print(f"  {name:24s} {dict(sorted(counts.items()))}")

asyncio.run(main())
