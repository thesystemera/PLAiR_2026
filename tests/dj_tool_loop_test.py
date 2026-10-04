import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
from google.genai import types

from services import llm_router
from services.ai_service import AIService
from services_radio.dj_tool_registry import DJ_FUNCTION_DECLARATIONS, READ_TOOLS

LINE = ("[BROADCAST] [LEO] &0.2& ~facepalms~ &0.1& Ah, FUCK! (24 chars)\n[JESS] @19@ &0.3& ~snorts~ My bad, dude. "
        "(39 chars)\n[LEO] @18@ &0.2& Nine Inch Nails! Not known snails!")
ECHO = LINE.replace(" (24 chars)", "").replace(" (39 chars)", "")
REPLY = ("[BROADCAST] [LEO] &0.2& ~excited~ Alright, forget the snails, we're diving headfirst into some actual Nine "
         "Inch Nails for you!\n[JESS] @79@ &0.3& There we go! Much better.\n[INTERNAL DIALOGUE] Played it.\n[TASK] complete")
CALL = {"category": "primary_artist", "query": "Nine Inch Nails", "mode": "play"}
SKIP = {"action": "next"}


def response(*parts):
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role="model", parts=list(parts)), finish_reason="STOP")])


def text(value):
    return types.Part.from_text(text=value)


def call(name, args):
    return types.Part(function_call=types.FunctionCall(name=name, args=args))


async def run(label, script, repeatable):
    rounds = iter(script)
    executed = []

    async def fake_chain(**_):
        return next(rounds), {}, 0, "stub"

    async def dispatch(name, args):
        executed.append(name)
        return {"status": "ok", "now_playing": "Nerve Staple by Nine Inch Nails"}

    aired = []

    async def on_preamble(line, calls):
        if line.strip():
            aired.append(line)

    original = llm_router.gemini_generate_chain
    llm_router.gemini_generate_chain = fake_chain
    try:
        result = await AIService().run_gemini_tool_turn(
            system_instruction="test", user_message="[LISTENER TXT] nine inch nails",
            function_declarations=DJ_FUNCTION_DECLARATIONS, dispatch=dispatch, max_rounds=4,
            on_preamble=on_preamble, followup_tools=READ_TOOLS, repeatable_tools=repeatable)
    finally:
        llm_router.gemini_generate_chain = original
    final = result["text"]
    print(f"{label}: tools run {executed} | lines aired before the reply {len(aired)} | rounds {result['rounds']} | "
          f"reply is the real one: {final.startswith('[BROADCAST] [LEO] &0.2& ~excited~')} | "
          f"echo left in reply: {'facepalms' in final}")


async def main():
    echo_loop = [response(text(LINE), call("search_and_play", CALL))] + [
        response(text(REPLY), text(ECHO), call("search_and_play", dict(reversed(list(CALL.items())))))
        for _ in range(4)]
    await run("echoed search", echo_loop, {"playback_control"})
    skip_twice = [response(text(LINE), call("playback_control", SKIP)),
                  response(text("[BROADCAST] [LEO] And another one gone."), call("playback_control", SKIP)),
                  response(text(REPLY))]
    await run("skip two songs (legit repeat)", skip_twice, {"playback_control"})
    skip_echo = [response(text(LINE), call("playback_control", SKIP)),
                 response(text(REPLY), text(ECHO), call("playback_control", SKIP))]
    await run("echoed skip", skip_echo, {"playback_control"})

asyncio.run(main())
