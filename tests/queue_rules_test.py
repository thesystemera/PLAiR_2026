"""Queue rules, without a server, a catalog or an LLM: PlaybackState on a fake catalog and a fake fill.

Checks: picks go after earlier picks, "play next" goes right after the current song, the songs ahead never pass
the limit (station fill is trimmed first, then the picks furthest away), seeding keeps the songs just played and
remembers its seed,
a playlist switch keeps them too, and the DJs' playlist view and last track read the right songs.

Usage: python tests/queue_rules_test.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

from config import settings  # noqa: E402
from services.playback_state import PlaybackState  # noqa: E402


def track(track_id):
    return {"id": track_id, "generation_params": {"title": track_id, "artist_name": "Artist"},
            "derived_tags": {"primary_genre": "test"}, "track_info": {"duration": 180000}}


class Catalog:
    def __init__(self):
        self.tracks = {}

    def get_track(self, track_id):
        return self.tracks.setdefault(track_id, track(track_id)) if track_id else None

    def has_artwork(self, _):
        return False


class Population:
    def __init__(self, catalog):
        self.catalog, self.count = catalog, 0

    def _new(self, n, prefix):
        out = []
        for _ in range(n):
            self.count += 1
            out.append(self.catalog.get_track(f"{prefix}{self.count}"))
        return out

    async def fill_queue(self, *, queue, queue_size, **_):
        return self._new(max(0, queue_size - len(queue)), "fill")

    async def seed_fill(self, *, needed, **_):
        return self._new(needed, "seed")


def ids(state, start=None, end=None):
    return [t["id"] for t in state.queue[start:end]]


def upcoming(state):
    return ids(state, state.current_index + 1)


async def new_state():
    catalog = Catalog()
    state = PlaybackState("guest_test", catalog, None, Population(catalog))
    state._spawn_play_event = lambda **_: None
    await state.play()
    for _ in range(settings.QUEUE_PLAYED_SONGS + 2):
        await state.next()
    await state._auto_fill_queue()
    return state


async def main():
    failures = []

    def check(name, ok, detail=""):
        print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
        if not ok:
            failures.append(name)

    state = await new_state()
    check("queue holds played + current + ahead", len(state.queue) == state.QUEUE_SIZE, f"{len(state.queue)}")
    check("current sits after the songs kept", state.current_index == settings.QUEUE_PLAYED_SONGS)

    first = [f"pickA{i}" for i in range(5)]
    second = [f"pickB{i}" for i in range(5)]
    await state.add_to_queue(first)
    await state.add_to_queue(second)
    check("second request goes after the first", upcoming(state)[:10] == first + second, str(upcoming(state)[:10]))
    check("only station fill is trimmed", len(state.queue) == state.QUEUE_SIZE)

    many = [f"pickC{i}" for i in range(settings.QUEUE_AHEAD_SONGS + 5)]
    await state.add_to_queue(many)
    check("the songs ahead never pass the limit", len(upcoming(state)) == settings.QUEUE_AHEAD_SONGS,
          str(len(upcoming(state))))
    check("station fill goes first, then the picks furthest away",
          upcoming(state) == (first + second + many)[:settings.QUEUE_AHEAD_SONGS], str(upcoming(state)))

    state = await new_state()
    await state.add_to_queue(first)
    batch = [f"search{i}" for i in range(50)]
    await state.add_to_queue(batch, play_next=True)
    check("a big play-next batch keeps its first songs up to the limit",
          upcoming(state) == batch[:settings.QUEUE_AHEAD_SONGS], str(upcoming(state)))

    state = await new_state()
    await state.add_to_queue(first)
    await state.add_to_queue(["now1", "now2"], play_next=True)
    check("play next goes right after the current song", upcoming(state)[:7] == ["now1", "now2"] + first,
          str(upcoming(state)[:7]))
    later = upcoming(state)[-1]
    await state.add_to_queue([later], play_next=True)
    check("a queued song asked for again moves up, not duplicated",
          upcoming(state)[0] == later and ids(state).count(later) == 1)

    state = await new_state()
    played = ids(state, 0, state.current_index)
    current = state.current_track_id
    await state.add_to_queue(first)
    await state.seed_radio("mood", current)
    check("seeding keeps the songs just played", ids(state, 0, state.current_index) == played,
          f"{ids(state, 0, state.current_index)} vs {played}")
    check("seeding keeps the current song", state.current_track_id == current)
    check("seeding keeps the picks first", upcoming(state)[:5] == first, str(upcoming(state)[:5]))
    check("seeding remembers its seed", state.seed_track_id == current)

    state = await new_state()
    before = ids(state, 0, state.current_index + 1)
    await state.seed_radio("top_hits_all")
    check("playlist switch keeps the songs just played",
          ids(state, 0, state.current_index) == before[-settings.QUEUE_PLAYED_SONGS:],
          f"{ids(state, 0, state.current_index)} vs {before}")
    check("playlist switch forgets the seed", state.seed_track_id is None)
    check("playlist switch starts a new song", state.current_track_id not in before)

    from services_radio import context_service
    from services_radio.context_nodes_track import get_queue_playlist

    class Playback:
        def get_state(self, _session_id, simplified=True):
            return state.get_state(simplified=simplified)

    state = await new_state()
    context = await context_service.get_track_context("guest_test", Playback(), None)
    check("DJs' last track is the song just before",
          context["last_track"].get("name") == state.queue[state.current_index - 1]["id"], str(context["last_track"]))
    view = await get_queue_playlist(session_id="guest_test", playback_service=Playback())
    lines = view.splitlines()[1:]
    span = settings.DJ_PLAYLIST_VIEW_SONGS
    check("DJs' playlist shows played, now and ahead", len(lines) == 2 * span + 1 and "now" in lines[span], view)

    print(f"\n{'all passed' if not failures else f'{len(failures)} failed'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    asyncio.run(main())
