import os
import random
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from annoy import AnnoyIndex

from config.settings import settings
from database.pg_pool import get_pooled_connection
from services import log_service
from services.vector_store import (EMBEDDING_DIM, Entry, Slot, VectorStore, prime_query_vectors, query_vector,
                                   unit)

SHOTGUN_PRUNE_AT = 20000
NEAR_BEST_TAKES = 0.01
ANNOY_TREES = 10
SAVE_VERIFY_TIMEOUT_S = 5


def _get_connection():
    return get_pooled_connection(settings.EMBEDDINGS_DATABASE_URL)


def _verify_file_saved(file_path: str) -> bool:
    start_time = time.time()
    while time.time() - start_time < SAVE_VERIFY_TIMEOUT_S:
        if os.path.exists(file_path):
            try:
                with open(file_path, 'rb') as f:
                    f.read(1)
                return True
            except IOError:
                time.sleep(0.1)
    return False


@dataclass
class ClipSlot(Slot):
    rows: Dict[int, Entry] = field(default_factory=dict)
    index: Optional[AnnoyIndex] = None


class ClipStore(VectorStore):
    """One clip table (spoken lines, paralanguage, SFX or breaths) on the shared VectorStore pattern: the live slot is
    an Annoy index file (A/B: <table>_1.ann / <table>_2.ann), clips saved since are searched alongside it, and the
    background maintainer folds them into the spare file and swaps."""

    log_channel = "tts_vector_db"

    def __init__(self, table: str):
        super().__init__()
        self.name = table
        self.table = table

    def path(self, slot: int) -> str:
        return os.path.join(str(settings.EMBEDDINGS_DIR), f"{self.table}_{slot}.ann")

    def load_rows(self, keys: Optional[List[str]] = None) -> List[Tuple[int, str, Any]]:
        conn = _get_connection()
        try:
            c = conn.cursor()
            query = f"SELECT id, filename, title, embedding, voice FROM {self.table}"
            if keys is None:
                c.execute(query)
            else:
                c.execute(f"{query} WHERE filename = ANY(%s)", (list(keys),))
            rows = c.fetchall()
        finally:
            conn.close()
        return [(row_id, filename, (title, voice, np.frombuffer(bytes(embedding), dtype=np.float32)))
                for row_id, filename, title, embedding, voice in rows]

    def embed(self, meta: Any, persist: bool) -> np.ndarray:
        return unit(meta[2])

    def build_slot(self, entries: List[Entry], target: Optional[int], basis: Optional[Slot]) -> ClipSlot:
        rows = {entry.row_id: entry for entry in entries}
        if target is None or not entries:
            return ClipSlot(entries, rows, None)
        index = AnnoyIndex(EMBEDDING_DIM, 'angular')
        for entry in entries:
            index.add_item(entry.row_id - 1, entry.payload)
        index.build(ANNOY_TREES)
        path = self.path(target)
        staging = f"{path}.new"
        index.save(staging)
        index.unload()
        if not _verify_file_saved(staging):
            raise IOError(f"Failed to verify saved file: {staging}")
        os.replace(staging, path)
        loaded = AnnoyIndex(EMBEDDING_DIM, 'angular')
        loaded.load(path)
        return ClipSlot(entries, rows, loaded)

    def release_slot(self, slot: Slot) -> None:
        if isinstance(slot, ClipSlot) and slot.index is not None:
            slot.index.unload()

    def restore(self) -> bool:
        path = self.path(1)
        if not os.path.exists(path):
            return False
        index = AnnoyIndex(EMBEDDING_DIM, 'angular')
        try:
            index.load(path)
        except Exception as e:
            log_service.error(f"Failed to load Annoy index for {self.table}: {e}")
            return False
        rows = self.load_rows()
        if index.get_n_items() != max((row_id for row_id, _, _ in rows), default=0):
            index.unload()
            log_service.warning(f"  ✗ {self.table} index does not match database ids (stale) - rebuilding")
            return False
        entries = [Entry(row_id, filename, meta, self.embed(meta, persist=False)) for row_id, filename, meta in rows]
        with self._lock:
            self._slots[1] = ClipSlot(entries, {entry.row_id: entry for entry in entries}, index)
            self._live = 1
        return True

    def candidates(self, vector: np.ndarray, how_many: int) -> List[Entry]:
        slots, hidden = self.views()
        found: Dict[str, Entry] = {}
        for slot in slots:
            if slot.index is not None:
                ids = slot.index.get_nns_by_vector(vector, how_many) if slot.index.get_n_items() else []
                nearby = [slot.rows.get(item_id + 1) for item_id in ids]
            else:
                nearby = slot.entries
            for entry in nearby:
                if entry is not None and entry.key not in hidden:
                    found[entry.key] = entry
        return list(found.values())


class VectorDBService:
    def __init__(self):
        log_service.tts_vector_db("Initializing VectorDBService (PostgreSQL)")
        self.stores: Dict[str, ClipStore] = {table: ClipStore(table) for table in settings.TTS_EMBEDDING_TABLES}
        self.shotgun_cache = {}
        self.shotgun_lock = Lock()
        self._initialize_databases()

    def _initialize_databases(self):
        conn = _get_connection()
        c = conn.cursor()

        for table_name in settings.TTS_EMBEDDING_TABLES:
            c.execute(f'''
                CREATE TABLE IF NOT EXISTS {table_name} (
                    id SERIAL PRIMARY KEY,
                    filename TEXT UNIQUE NOT NULL,
                    title TEXT,
                    embedding BYTEA,
                    voice TEXT
                )
            ''')
            c.execute(f'''
                CREATE INDEX IF NOT EXISTS idx_{table_name}_voice
                ON {table_name}(voice)
            ''')
        c.execute("ALTER TABLE paralanguage_embeddings ADD COLUMN IF NOT EXISTS emoji TEXT")

        conn.commit()
        conn.close()
        log_service.tts_vector_db("TTS embedding tables initialized (PostgreSQL)")

    def _clip_base_directory(self, table_name: str):
        return {
            "tts_embeddings": settings.TTS_AUDIO_DIR,
            "paralanguage_embeddings": settings.PARALANGUAGE_AUDIO_DIR,
            "audio_embeddings": settings.AUDIO_EFFECT_DIR,
            "breath_embeddings": settings.BREATH_AUDIO_DIR,
        }.get(table_name)

    def purge_missing_files(self):
        for table_name in settings.TTS_EMBEDDING_TABLES:
            base_dir = self._clip_base_directory(table_name)
            if base_dir is None or not os.path.isdir(base_dir):
                continue
            conn = _get_connection()
            try:
                c = conn.cursor()
                c.execute(f"SELECT filename, voice FROM {table_name}")
                rows = c.fetchall()
                missing = [filename for filename, voice in rows
                           if not os.path.exists(os.path.join(str(base_dir), voice or "", filename))]
                if not missing:
                    continue
                if len(missing) > len(rows) // 2:
                    log_service.warning(
                        f"Vector Cache: {len(missing)}/{len(rows)} {table_name} files missing on disk - skipping purge")
                    continue
                c.execute(f"DELETE FROM {table_name} WHERE filename = ANY(%s)", (missing,))
                conn.commit()
                log_service.tts_vector_db(f"Vector Cache: Purged {len(missing)} stale {table_name} rows (files missing)")
            except Exception as e:
                conn.rollback()
                log_service.error(f"Vector Cache: purge failed for {table_name}: {e}")
            finally:
                conn.close()

    def paralanguage_emojis(self) -> dict:
        conn = _get_connection()
        try:
            c = conn.cursor()
            c.execute("SELECT DISTINCT ON (lower(title)) lower(title), emoji FROM paralanguage_embeddings WHERE emoji IS NOT NULL")
            return dict(c.fetchall())
        finally:
            conn.close()

    def set_paralanguage_emoji(self, title: str, emoji: str):
        conn = _get_connection()
        try:
            c = conn.cursor()
            c.execute("UPDATE paralanguage_embeddings SET emoji = %s WHERE lower(title) = lower(%s)", (emoji, title))
            conn.commit()
        except Exception as e:
            conn.rollback()
            log_service.error(f"Failed to store paralanguage emoji for '{title}': {e}")
        finally:
            conn.close()

    def nearest_titles(self, db_type: str, text: str, limit: int = 5) -> List[str]:
        store = self.stores.get(db_type)
        if store is None:
            return []
        query = unit(self._generate_embedding(text))
        scored = {}
        for entry in store.candidates(query, limit * 4):
            title = entry.meta[0]
            scored[title] = max(scored.get(title, -1.0), float(np.dot(query, entry.payload)))
        return [title for title, _ in sorted(scored.items(), key=lambda kv: kv[1], reverse=True)[:limit]]

    def delete_embedding(self, filename: str, db_type: str):
        conn = _get_connection()
        try:
            c = conn.cursor()
            c.execute(f"DELETE FROM {db_type} WHERE filename = %s", (filename,))
            conn.commit()
        except Exception as e:
            conn.rollback()
            log_service.error(f"Failed to delete embedding {filename} from {db_type}: {e}")
        finally:
            conn.close()

        if db_type in self.stores:
            self.stores[db_type].remove(filename)
        log_service.tts_vector_db(f"Vector Cache: Removed stale {db_type} embedding: {filename}")

    def purge_paralanguage_on_mode_change(self):
        mode = "engine-tags" if settings.PARALANGUAGE_ENGINE_TAGS else "phonetic"
        marker = settings.PARALANGUAGE_AUDIO_DIR / ".paralanguage_mode"
        try:
            if marker.read_text(encoding="utf-8").strip() == mode:
                return
        except OSError:
            pass
        removed = 0
        for clip in settings.PARALANGUAGE_AUDIO_DIR.rglob("*.flac"):
            clip.unlink(missing_ok=True)
            removed += 1
        conn = _get_connection()
        try:
            c = conn.cursor()
            c.execute("SELECT to_regclass('paralanguage_embeddings')")
            if c.fetchone()[0] is not None:
                c.execute("TRUNCATE paralanguage_embeddings RESTART IDENTITY")
            conn.commit()
        finally:
            conn.close()
        for slot in (1, 2):
            ann_file = self.stores["paralanguage_embeddings"].path(slot)
            if os.path.exists(ann_file):
                os.remove(ann_file)
        settings.PARALANGUAGE_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        marker.write_text(mode, encoding="utf-8")
        log_service.system(f"[TTS CACHE] paralanguage mode is now '{mode}': purged {removed} paralanguage clips to re-render")

    def load_initial_data(self):
        start_time = time.perf_counter()
        self.purge_paralanguage_on_mode_change()
        self.drop_stale_encoder_rows()
        self.purge_missing_files()

        db_data_results = {}
        for table_name, store in self.stores.items():
            store.load()
            entries = store.entries()
            voice_counts: Dict[str, int] = {}
            for entry in entries:
                voice_counts[entry.meta[1]] = voice_counts.get(entry.meta[1], 0) + 1
            db_data_results[table_name] = (entries, voice_counts)
            if not entries:
                log_service.tts_vector_db(f"Vector Database: {table_name} is empty (fills as clips are generated)")
            else:
                voices = ", ".join(f"{voice} {count}" for voice, count in sorted(voice_counts.items()))
                log_service.tts_vector_db(f"Vector Database: {table_name}: {len(entries)} clips ({voices})")

        end_time = time.perf_counter()
        log_service.tts_vector_db(f"Vector Database: clip cache loaded in {end_time - start_time:.2f}s")
        return db_data_results

    def titles(self, db_type: str) -> List[str]:
        store = self.stores.get(db_type)
        return list({entry.meta[0] for entry in store.entries()}) if store is not None else []

    @staticmethod
    def prime_embeddings(texts: List[str]):
        prime_query_vectors(texts)

    @staticmethod
    def _generate_embedding(text: str) -> np.ndarray:
        return query_vector(text)

    def drop_stale_encoder_rows(self):
        conn = _get_connection()
        try:
            c = conn.cursor()
            for table_name, store in self.stores.items():
                c.execute("SELECT to_regclass(%s)", (table_name,))
                if c.fetchone()[0] is None:
                    continue
                c.execute(f"SELECT COUNT(*) FROM {table_name} WHERE octet_length(embedding) <> %s",
                          (EMBEDDING_DIM * 4,))
                stale = c.fetchone()[0]
                if not stale:
                    continue
                c.execute(f"TRUNCATE {table_name} RESTART IDENTITY")
                for slot in (1, 2):
                    ann_file = store.path(slot)
                    if os.path.exists(ann_file):
                        os.remove(ann_file)
                log_service.system(f"[TTS CACHE] {table_name}: {stale} rows were made with another encoder - "
                                   f"cleared, re-embedding from the clip files")
            conn.commit()
        finally:
            conn.close()

    def save_embedding(self, audio_path: str, text: str, voice_name: str, db_type: str):
        embedding = self._generate_embedding(text)
        filename = os.path.basename(audio_path)

        conn = _get_connection()
        c = conn.cursor()

        try:
            c.execute(
                f"""
                INSERT INTO {db_type} (filename, title, embedding, voice)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (filename) DO UPDATE SET
                    title = EXCLUDED.title,
                    embedding = EXCLUDED.embedding,
                    voice = EXCLUDED.voice
                RETURNING id
                """,
                (filename, text, embedding.tobytes(), voice_name)
            )
            fetch_result = c.fetchone()
            new_id = fetch_result[0] if fetch_result else None
            conn.commit()
        except Exception as e:
            log_service.error(f"Failed to save embedding to {db_type}: {e}")
            conn.rollback()
            new_id = None
        finally:
            conn.close()

        if new_id and db_type in self.stores:
            self.stores[db_type].add_entries([(new_id, filename, (text, voice_name, embedding), unit(embedding))])
            log_service.detail(f"Vector Cache: Saved new {db_type} embedding: {filename}", "tts_vector_db")

    def _cooled_down(self, listener: Optional[str], filename: str, now: float, respect_cooldown: bool) -> bool:
        if not respect_cooldown:
            return True
        with self.shotgun_lock:
            used_at = self.shotgun_cache.get((listener, filename))
        return used_at is None or now - used_at >= settings.VECTOR_DB_SHOTGUN_COOLDOWN

    def is_fresh(self, listener: Optional[str], key: str, now: float) -> bool:
        return self._cooled_down(listener, key, now, True)

    def note_used(self, listener: Optional[str], filename: str, now: Optional[float] = None):
        now = now or time.time()
        with self.shotgun_lock:
            self.shotgun_cache[(listener, filename)] = now
            if len(self.shotgun_cache) > SHOTGUN_PRUNE_AT:
                cutoff = now - settings.VECTOR_DB_SHOTGUN_COOLDOWN
                self.shotgun_cache = {key: used_at for key, used_at in self.shotgun_cache.items() if used_at >= cutoff}

    def query_embeddings(
            self,
            response_str: str,
            voice_name: str,
            db_type: str,
            top_n: int = 5,
            listener: Optional[str] = None,
            respect_cooldown: bool = True,
            min_similarity: float = float('-inf')
    ) -> List[Tuple[str, str, float]]:
        query_start_time = time.perf_counter()
        store = self.stores.get(db_type)
        if store is None:
            log_service.error(f"Unknown database type: {db_type}")
            return []
        query_embedding = unit(self._generate_embedding(response_str))
        current_time = time.time()

        live = store.live()
        if live is None or not live.entries:
            log_service.throttled(f"tts_index_empty:{db_type}",
                                  f"Vector Cache: {db_type} index is empty - every lookup misses until the next rebuild",
                                  "tts_vector_db")

        all_matches = []
        skipped_count = 0
        for entry in store.candidates(query_embedding, top_n * 10):
            title, db_voice, _embedding = entry.meta
            if db_voice != voice_name:
                continue
            if self._cooled_down(listener, entry.key, current_time, respect_cooldown):
                all_matches.append((entry.key, title, float(np.dot(query_embedding, entry.payload))))
            else:
                skipped_count += 1

        all_matches = sorted((m for m in all_matches if m[2] >= min_similarity), key=lambda x: x[2], reverse=True)

        results = []
        while all_matches and len(results) < top_n:
            current_similarity = all_matches[0][2]
            identical_matches = [m for m in all_matches if current_similarity - m[2] < NEAR_BEST_TAKES]
            results.append(random.choice(identical_matches) if len(identical_matches) > 1 else identical_matches[0])
            all_matches = [m for m in all_matches if current_similarity - m[2] >= NEAR_BEST_TAKES]

        best = f"best {results[0][2]:.3f}" if results else "no match"
        log_service.detail(
            f"Vector Cache: {db_type} lookup for {voice_name} '{response_str[:40]}' -> {best}, "
            f"{skipped_count} on cooldown ({time.perf_counter() - query_start_time:.2f}s)", "tts_vector_db")
        return results
