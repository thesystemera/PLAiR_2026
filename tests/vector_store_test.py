"""The shared VectorStore pattern, without a database, a GPU or an LLM: a fake store over an in-memory table.

Checks: boot builds slot 1; an added item is searchable at once and marks the store dirty; a removed one is hidden at
once; a rebuild builds the spare slot and swaps; items added during a rebuild survive it; only one rebuild runs at a
time; reading never waits for a rebuild.

Usage: python tests/vector_store_test.py
"""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

from services.vector_store import STORES, Slot, VectorStore  # noqa: E402


class FakeStore(VectorStore):
    name = "fake"

    def __init__(self, table, build_seconds=0.0):
        super().__init__()
        self.table = table
        self.build_seconds = build_seconds
        self.builds = []

    def load_rows(self, keys=None):
        rows = [(i + 1, key, meta) for i, (key, meta) in enumerate(self.table.items())]
        return rows if keys is None else [row for row in rows if row[1] in keys]

    def embed(self, meta, persist):
        return meta

    def build_slot(self, entries, target, basis):
        if target is not None:
            self.builds.append(target)
            time.sleep(self.build_seconds)
        return Slot(entries)


def main():
    failures = []

    def check(name, ok, detail=""):
        print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
        if not ok:
            failures.append(name)

    table = {"a": "A", "b": "B"}
    store = FakeStore(table)
    STORES.remove(store)
    store.load()
    check("boot builds slot 1", store.builds == [1] and store.keys() == {"a", "b"}, str(store.builds))
    check("clean after boot", not store.dirty)

    table["c"] = "C"
    store.add_rows(["c"])
    check("added item searchable at once", "c" in store.keys())
    check("adding marks dirty", store.dirty)
    check("no rebuild on add", store.builds == [1], str(store.builds))

    store.remove("a")
    check("removed item hidden at once", "a" not in store.keys())

    del table["a"]
    store.rebuild()
    check("rebuild builds the spare slot", store.builds == [1, 2], str(store.builds))
    check("after rebuild the live slot holds it all", {e.key for e in store.live().entries} == {"b", "c"})
    check("pending cleared after rebuild", store._pending_slot is None and not store._pending)
    check("clean after rebuild", not store.dirty)

    slow = FakeStore({"x": "X"}, build_seconds=0.5)
    STORES.remove(slow)
    slow.load()
    builder = threading.Thread(target=slow.rebuild)
    builder.start()
    time.sleep(0.1)
    started = time.perf_counter()
    keys = slow.keys()
    read_s = time.perf_counter() - started
    check("reading never waits for a rebuild", read_s < 0.05 and keys == {"x"}, f"{read_s:.3f}s")
    slow.table["y"] = "Y"
    slow.add_rows(["y"])
    second = threading.Thread(target=slow.rebuild)
    second.start()
    second.join()
    builder.join()
    check("only one rebuild at a time", slow.builds == [1, 2], str(slow.builds))
    check("item added during a rebuild survives it", "y" in slow.keys())
    check("and still marks the store dirty", slow.dirty)

    print(f"\n{'all passed' if not failures else f'{len(failures)} failed'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
