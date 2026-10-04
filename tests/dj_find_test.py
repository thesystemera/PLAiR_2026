"""Run whole DJ turns for music requests and check what the hosts end up playing.

Real DJ system prompt, real Gemini tool loop and the real catalog search (find_tracks / search_and_play), with
playback faked: nothing is queued or played on the station and no backend is needed. The user message is the
one recorded for a real turn (from data/logs/dj_turns.jsonl*) with the listener's words swapped in.

Each scenario prints the tool calls the hosts made and the track that would have played, and checks its artist.
Uses the station's Gemini quota: a turn is 2-4 calls. Keep runs small (CLAUDE.md, "Go easy on paid LLM calls").

Usage: python tests/dj_find_test.py <runs per scenario> <recorded turn_id> [scenario number ...]
Set EMBEDDINGS_DIR to a copy of data/embeddings while the production backend is running.
"""
import asyncio
import glob
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "tests"))

import track_search_probe as probe  # noqa: E402
from config.settings import settings  # noqa: E402
from service_registry import services  # noqa: E402
from services import listener_plays, log_service  # noqa: E402
from services.llm_router import LLM_DJ  # noqa: E402
from services_radio import context_nodes  # noqa: E402,F401
from services_radio.context_node_registry import node_registry  # noqa: E402
from services_radio.dj_command_executor import CommandExecutorService  # noqa: E402
from services_radio.dj_prompt_helper_service import assemble_prompt  # noqa: E402
from services_radio.dj_tools_registry import READ_TOOLS, SEGMENT_TOOLS, declarations_for  # noqa: E402
from services_radio.dj_tools import DJToolRuntime, DJTurnContext  # noqa: E402

SYSTEM_NODES = ['core_dj_identity', 'format_channels', 'format_tone',
                'format_meta_tags_guide', 'format_meta_tag_examples', 'format_roles_detailed',
                'format_station_characteristics', 'format_dialogue_examples', 'guidelines_general',
                'guidelines_critical', 'guidelines_internal_dialogue', 'instruction_dj_tools', 'tool_guidance',
                'station_recent_airings', 'studio_clock', 'city_pulse']

SUPER_LIKES = "one of their super likes"
SIGNED_IN_USER = 1

SCENARIOS = [
    ("Can we listen, I'm trying to find a band, this is like a 90s band, male, female singer, I think it started "
     "with S, um, yeah.", "Sonic Youth"),
    ("What's that band with the French woman singing over old Moog synths, kind of krautrock pop? Play them.",
     "Stereolab"),
    ("There's this Boston band, quiet verses and then the chorus explodes, the singer screams. Can't remember the "
     "name. Put them on.", "Pixies"),
    ("Play something by the guy who did Windowlicker.", "Aphex Twin"),
    ("Play me something dreamy and slow.", None),
    ("What about something for my favourites? Like, uh, my super likes or like, what should I do like this or "
     "something?", SUPER_LIKES),
]


class FakePlayback:
    def __init__(self):
        self.played = []

    async def add_to_queue(self, session_id, track_ids, user_id=None):
        return None

    async def play(self, session_id, track_id=None, user_id=None):
        self.played.append(track_id)

    def get_state(self, session_id):
        return {}


class FakeSio:
    async def emit(self, *args, **kwargs):
        return None


class FakeDB:
    def add(self, *args):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def __getattr__(self, name):
        async def nothing(*args, **kwargs):
            return None
        return nothing


def recorded_turn(turn_id):
    for path in sorted(glob.glob(str(ROOT / "data" / "logs" / "dj_turns.jsonl*"))):
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if record.get("turn_id") == turn_id:
                    return record
    raise SystemExit(f"turn {turn_id} not found")


async def main():
    runs = int(sys.argv[1])
    template = recorded_turn(sys.argv[2])["user_message"]
    head = template[:template.rindex("[LISTENER TXT]")]
    picked = [SCENARIOS[int(n) - 1] for n in sys.argv[3:]] or SCENARIOS
    search = await probe.setup()
    system_keys = [key for key in SYSTEM_NODES if node_registry.is_system(key)]
    context = await node_registry.fetch_nodes(system_keys)
    system_prompt = assemble_prompt(context, system_keys, untrusted_keys=set(), note=None)
    declarations = declarations_for({"search_and_play", "find_tracks"})
    hits = 0
    for listener_text, expected in picked:
        print(f"\n== {listener_text[:90]}  (want {expected or 'a fitting vibe'})", flush=True)
        signed_in = expected == SUPER_LIKES
        wanted = set(await listener_plays.ratings(SIGNED_IN_USER, "super_likes")) if signed_in else set()
        for _ in range(runs):
            playback = FakePlayback()
            executor = CommandExecutorService(None, None, None, None, None, None, None, None, None, FakeSio(), FakeDB,
                                         search, playback, services.catalog_service)
            session = ({"session_id": str(SIGNED_IN_USER), "user_id": SIGNED_IN_USER} if signed_in
                       else {"session_id": f"guest_{uuid.uuid4()}", "user_id": None})
            ctx = DJTurnContext(session_dict=session, transcription=listener_text, origin="text")
            runtime = DJToolRuntime(executor, ctx)
            result = await services.ai_service.run_gemini_tool_turn(
                system_instruction=system_prompt, user_message=f"{head}[LISTENER TXT] {listener_text}",
                function_declarations=declarations, dispatch=runtime.dispatch, spec=LLM_DJ,
                max_rounds=settings.DJ_TOOL_MAX_ROUNDS, thinking_budget=settings.DJ_TOOL_THINKING_BUDGET,
                followup_thinking_budget=settings.DJ_TOOL_FOLLOWUP_THINKING_BUDGET,
                followup_tools=READ_TOOLS, handoff_tools=SEGMENT_TOOLS, repeatable_tools={"playback_control"})
            played = playback.played[-1] if playback.played else None
            track = services.catalog_service.get_track(played) if played else None
            artists = " / ".join(log_service.track_artists(track)) if track else ""
            if signed_in:
                ok = played in wanted
            else:
                ok = expected is None and bool(played) or bool(expected and expected.lower() in artists.lower())
            hits += ok
            calls = "; ".join(f"{c['name']}({', '.join(f'{k}={v}' for k, v in c['args'].items() if k != '_done_with')})"
                              for c in result.get("tool_calls") or [])
            title = (track or {}).get("generation_params", {}).get("title", "")
            print(f"  {'OK  ' if ok else 'MISS'} played: {title} by {artists or '-'} | {result.get('rounds')} rounds"
                  f" | {calls}", flush=True)
            if not ok:
                for call in result.get("tool_calls") or []:
                    items = (call.get("result") or {}).get("items") or []
                    if items:
                        print(f"       {call['name']} showed: {', '.join(item['artist'] for item in items)}")
                said = " / ".join(line.strip() for line in (result.get("text") or "").splitlines() if line.strip())
                print(f"       reply: {said[:400]}")
    print(f"\n{hits} of {runs * len(picked)} turns played what was wanted")


if __name__ == "__main__":
    asyncio.run(main())
