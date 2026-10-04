import os
import json
import time
import numpy as np
from annoy import AnnoyIndex
from threading import Lock
from typing import List, Dict, Any, Optional, Tuple
from models_global import get_device, get_sentence_encoder
from config.settings import settings
from database.pg_pool import get_pooled_connection
from services import log_service

EMBEDDING_DIM = settings.SEMANTIC_ENCODER_DIM


def embeddings_table(category: str) -> str:
    return f"{category}_{settings.SEMANTIC_ENCODER_SLUG}_embeddings"


def index_paths(index_dir, prefix: str) -> Tuple[str, str]:
    stem = f"{prefix}_{settings.SEMANTIC_ENCODER_SLUG}"
    return os.path.join(str(index_dir), f"{stem}_1.ann"), os.path.join(str(index_dir), f"{stem}_2.ann")


class BaseVectorDatabaseService:
    embedding_dim: int = EMBEDDING_DIM
    encoder_name: str = settings.SEMANTIC_ENCODER
    categories: Tuple[str, ...] = ()
    default_weights: Dict[str, float] = {}
    log_channel: str = ""
    service_label: str = ""
    display_name: str = ""
    index_dir_setting_name: str = ""
    index_file_prefix: str = ""
    source_table: str = ""
    source_id_column: str = ""
    source_label: str = ""
    source_db_label: str = ""
    item_noun: str = "items"
    single_item_noun: str = "item"

    def __init__(self, source_service=None):
        self._log(f"Initializing {type(self).__name__} (using global models)")

        self.source_service = source_service

        self.device = get_device()

        if self.device.type != 'cuda':
            log_service.warning(
                f"⚠️  {self.service_label} vector service is on {self.device.type.upper()}! "
                f"GPU acceleration is recommended for better performance."
            )
        else:
            self._log(f"✓ GPU device: {self.device}")
            self._log(f"✓ Embedding model: {self.encoder_name} ({self.embedding_dim}-dim)")

        self.embedding_tables = [embeddings_table(category) for category in self.categories]

        self.caches: Dict[str, Dict[str, np.ndarray]] = {}
        for category in self.categories:
            cache: Dict[str, np.ndarray] = {}
            self.caches[category] = cache
            setattr(self, f"{category}_embeddings", cache)

        self.annoy_index_1 = AnnoyIndex(self.embedding_dim, 'angular')
        self.annoy_index_2 = AnnoyIndex(self.embedding_dim, 'angular')

        self.current_index = 1
        self.index_lock = Lock()

        self._rowid_cache: Dict[int, str] = {}
        self._metadata_cache: Dict[int, Dict] = {}
        self.dirty = False

    def _log(self, message: str):
        getattr(log_service, self.log_channel)(message)

    def _get_connection(self):
        return get_pooled_connection(settings.EMBEDDINGS_DATABASE_URL)

    def _index_files(self) -> Tuple[str, str]:
        return index_paths(getattr(settings, self.index_dir_setting_name), self.index_file_prefix)

    def current_annoy_index(self) -> AnnoyIndex:
        return self.annoy_index_1 if self.current_index == 1 else self.annoy_index_2

    def lookup_cached_row(self, rowid: int) -> Tuple[Optional[str], Optional[Dict]]:
        if rowid in self._rowid_cache:
            return self._rowid_cache[rowid], self._metadata_cache.get(rowid, {}).copy()
        return None, None

    def load_initial_data(self):
        start_time = time.perf_counter()

        self._log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        self._log(f"Loading {self.display_name} Embedding Caches")
        self._log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        total_loaded = 0
        conn = self._get_connection()
        c = conn.cursor()
        for db_type in self.embedding_tables:
            c.execute(f"CREATE TABLE IF NOT EXISTS {db_type} (text TEXT PRIMARY KEY, embedding BYTEA NOT NULL)")
        conn.commit()

        for category, cache in self.caches.items():
            db_type = embeddings_table(category)
            if db_type not in self.embedding_tables:
                log_service.warning(f"⚠️  Missing embedding table {db_type}")
                continue

            try:
                c.execute(f"SELECT text, embedding FROM {db_type}")
                count = 0
                for text, embedding_bytes in c.fetchall():
                    cache[text] = np.frombuffer(bytes(embedding_bytes), dtype=np.float32)
                    count += 1
                    total_loaded += 1
                if count > 0:
                    self._log(f"  ✓ {db_type}: {count} embeddings")
            except Exception as e:
                log_service.error(f"Error loading {db_type}: {e}")

        conn.close()

        if total_loaded > 0:
            self._log(f"✓ Loaded {total_loaded} total cached embeddings into memory")
        else:
            self._log("  No cached embeddings found (will generate on first use)")

        self._load_indexes()
        if not self._metadata_cache and self.source_service is not None:
            try:
                self.load_metadata()
            except Exception as e:
                log_service.warning(f"{self.display_name}: could not load metadata: {e}")

        end_time = time.perf_counter()
        self._log(f"✓ load_initial_data completed in {end_time - start_time:.2f}s")
        self._log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")

    def _trigger_rebuild(self):
        raise NotImplementedError

    def _load_indexes(self):
        self._log("\nLoading Annoy indexes from disk...")
        self._load_annoy_indexes()

    def _load_annoy_indexes(self):
        ann_file_1, ann_file_2 = self._index_files()
        name_1, name_2 = os.path.basename(ann_file_1), os.path.basename(ann_file_2)

        missing_files = []
        if not os.path.exists(ann_file_1):
            missing_files.append(name_1)
        if not os.path.exists(ann_file_2):
            missing_files.append(name_2)

        if missing_files:
            log_service.warning(f"  ✗ Missing file(s): {', '.join(missing_files)}")
            log_service.warning("  🔨 Triggering rebuild...")
            self._trigger_rebuild()
            return

        try:
            self.annoy_index_1.load(ann_file_1)
            items_1 = self.annoy_index_1.get_n_items()
            self._log(f"  ✓ Loaded {name_1}: {items_1} {self.item_noun}")

            self.annoy_index_2.load(ann_file_2)
            items_2 = self.annoy_index_2.get_n_items()
            self._log(f"  ✓ Loaded {name_2}: {items_2} {self.item_noun}")

            if items_1 == 0 and items_2 == 0:
                log_service.warning(f"  ✗ Both indexes are empty (0 {self.item_noun})")
                log_service.warning("  🔨 Triggering rebuild...")
                self._trigger_rebuild()
                return

            expected_items = self._expected_index_items()
            if expected_items is not None and items_1 != expected_items:
                log_service.warning(f"  ✗ Index has {items_1} slots but {self.source_label} max rowid is {expected_items} (stale)")
                log_service.warning("  🔨 Triggering rebuild...")
                self.annoy_index_1.unload()
                self.annoy_index_2.unload()
                self.annoy_index_1 = AnnoyIndex(self.embedding_dim, 'angular')
                self.annoy_index_2 = AnnoyIndex(self.embedding_dim, 'angular')
                self._trigger_rebuild()
                return

            log_service.success("  ✓ All indexes loaded from disk successfully (TTS pattern - query by rowid)")

        except Exception as e:
            log_service.error(f"Failed to load indexes: {e}")
            log_service.warning("  🔨 Triggering rebuild...")
            self._trigger_rebuild()

    def _expected_index_items(self) -> Optional[int]:
        if not self.source_service:
            return None
        conn = self.source_service._get_connection()
        try:
            c = conn.cursor()
            c.execute(f"SELECT COALESCE(MAX(rowid), 0) FROM {self.source_table}")
            return c.fetchone()[0]
        finally:
            conn.close()

    def _encoder(self):
        return get_sentence_encoder(self.encoder_name)

    def _generate_embedding(self, text: str) -> np.ndarray:
        if not text or not text.strip():
            return np.zeros(self.embedding_dim, dtype=np.float32)
        return self._encoder().encode(text, normalize_embeddings=True).astype(np.float32)

    def _generate_embeddings_batch(self, texts: List[str], batch_size: int = 32) -> List[np.ndarray]:
        if not texts:
            return []
        self._log(f"    Generating {len(texts)} embeddings...")
        vectors = self._encoder().encode([text if text and text.strip() else " " for text in texts],
                                         batch_size=batch_size, normalize_embeddings=True)
        embeddings = [np.zeros(self.embedding_dim, dtype=np.float32) if not (text and text.strip())
                      else np.asarray(vector, dtype=np.float32) for text, vector in zip(texts, vectors)]
        self._log(f"    ✓ Generated {len(texts)} embeddings")
        return embeddings

    def rebuild(self) -> None:
        self._log_rebuild_header()
        self._rebuild_from_source(self.source_service)
        self.dirty = False

    def weighted(self, item: Dict[str, Any], weights: Optional[Dict[str, float]] = None) -> np.ndarray:
        return self._create_weighted_embedding(
            self.get_category_embeddings(self._extract_category_texts(item)), weights)

    def read_rows(self) -> Tuple[Dict[int, str], Dict[int, Dict]]:
        conn = self.source_service._get_connection()
        try:
            c = conn.cursor()
            c.execute(f"SELECT rowid, {self.source_id_column}, metadata_json FROM {self.source_table}")
            rows = c.fetchall()
        finally:
            conn.close()
        return ({rowid: item_id for rowid, item_id, _ in rows},
                {rowid: json.loads(metadata_json) for rowid, _, metadata_json in rows})

    def load_metadata(self) -> None:
        self._rowid_cache, self._metadata_cache = self.read_rows()

    def get_or_create_embedding(self, text: str, db_type: str, cache: Dict[str, np.ndarray]) -> np.ndarray:
        if not text or not text.strip():
            return np.zeros(self.embedding_dim, dtype=np.float32)

        text = text.strip()

        if text in cache:
            return cache[text]

        embedding = self._generate_embedding(text)

        if db_type in self.embedding_tables:
            conn = self._get_connection()
            c = conn.cursor()
            try:
                c.execute(
                    f"INSERT INTO {db_type} (text, embedding) VALUES (%s, %s) ON CONFLICT (text) DO NOTHING",
                    (text, embedding.tobytes())
                )
                conn.commit()
            except Exception:
                conn.rollback()
                c.execute(f"SELECT embedding FROM {db_type} WHERE text = %s", (text,))
                result = c.fetchone()
                if result:
                    embedding = np.frombuffer(bytes(result[0]), dtype=np.float32)
            finally:
                conn.close()

        cache[text] = embedding
        return embedding

    def ensure_embeddings(self, category: str, texts: List[str]) -> List[np.ndarray]:
        cache = self.caches[category]
        texts = [text.strip() for text in texts]
        missing = list(dict.fromkeys(text for text in texts if text and text not in cache))
        if missing:
            vectors = self._generate_embeddings_batch(missing)
            conn = self._get_connection()
            try:
                c = conn.cursor()
                for text, vector in zip(missing, vectors):
                    c.execute(f"INSERT INTO {embeddings_table(category)} (text, embedding) VALUES (%s, %s) "
                              f"ON CONFLICT (text) DO NOTHING", (text, vector.tobytes()))
                    cache[text] = vector
                conn.commit()
            finally:
                conn.close()
        return [cache[text] if text else np.zeros(self.embedding_dim, dtype=np.float32) for text in texts]

    def get_category_embeddings(self, category_texts: Dict[str, str]) -> Dict[str, np.ndarray]:
        return {
            category: self.get_or_create_embedding(category_texts[category], embeddings_table(category), cache)
            for category, cache in self.caches.items()
        }

    def _add_single_item(self, item_data: Dict[str, Any]) -> bool:
        try:
            category_texts = self._extract_category_texts(item_data)

            conn = self._get_connection()
            try:
                c = conn.cursor()

                for category, text in category_texts.items():
                    if text and text.strip() and category in self.caches:
                        cache = self.caches[category]
                        db_type = embeddings_table(category)
                        if db_type in self.embedding_tables and text.strip() not in cache:
                            embedding = self._generate_embedding(text.strip())
                            cache[text.strip()] = embedding

                            try:
                                c.execute(
                                    f"INSERT INTO {db_type} (text, embedding) VALUES (%s, %s) ON CONFLICT (text) DO NOTHING",
                                    (text.strip(), embedding.tobytes())
                                )
                            except Exception:
                                pass

                conn.commit()
            finally:
                conn.close()
            return True
        except Exception as e:
            log_service.error(f"Failed to add {self.single_item_noun} embeddings: {e}")
            return False

    def _log_rebuild_header(self):
        self._log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        self._log(f"🔨 REBUILDING {self.display_name.upper()} VECTOR INDEXES")
        self._log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    def _rebuild_from_source(self, source_svc, total_items: Optional[int] = None):
        rebuild_start = time.perf_counter()

        self._log(f"\n📝 Step 1: Querying {self.source_db_label} database (TTS pattern - rowid based)...")

        conn = source_svc._get_connection()
        c = conn.cursor()
        c.execute(f"SELECT rowid, {self.source_id_column}, metadata_json FROM {self.source_table} ORDER BY rowid")

        unique_texts_needed = {category: set() for category in self.categories}

        items_to_index = []
        new_rowid_cache: Dict[int, str] = {}
        new_metadata_cache: Dict[int, Dict] = {}

        try:
            rows = c.fetchall()
        finally:
            conn.close()

        for rowid, item_id, metadata_json in rows:
            item = json.loads(metadata_json)
            category_texts = self._extract_category_texts(item)
            items_to_index.append((rowid, category_texts))

            new_rowid_cache[rowid] = item_id
            new_metadata_cache[rowid] = item

            for category, text in category_texts.items():
                if text and text.strip():
                    unique_texts_needed[category].add(text.strip())

        self._rowid_cache = new_rowid_cache
        self._metadata_cache = new_metadata_cache
        self._log(f"  ✓ Cached {len(self._rowid_cache)} {self.single_item_noun} rowids in memory")

        if total_items is None:
            total_items = len(items_to_index)
            if total_items == 0:
                log_service.warning(f"No {self.display_name.lower()} available for indexing")
                return
            self._log(f"📊 Total {self.item_noun} to index: {total_items}")

        for category, texts in unique_texts_needed.items():
            if texts:
                self._log(f"  • {category}: {len(texts)} unique values")

        self._log("\n🚀 Step 2: Generating category embeddings...")

        total_new_embeddings = 0
        conn = self._get_connection()
        c = conn.cursor()

        for category, cache in self.caches.items():
            db_type = embeddings_table(category)
            if db_type not in self.embedding_tables:
                continue

            needed = unique_texts_needed[category]
            missing = [text for text in needed if text not in cache]

            if missing:
                self._log(f"\n  📂 {category.upper()}")
                self._log(f"    Total needed: {len(needed)}")
                self._log(f"    Already cached: {len(needed) - len(missing)}")
                self._log(f"    Need to generate: {len(missing)}")

                new_embeddings = self._generate_embeddings_batch(missing)

                self._log(f"    💾 Saving {len(missing)} embeddings to {db_type}...")

                saved_count = 0
                for text, embedding in zip(missing, new_embeddings):
                    try:
                        c.execute(
                            f"INSERT INTO {db_type} (text, embedding) VALUES (%s, %s) ON CONFLICT (text) DO NOTHING",
                            (text, embedding.tobytes())
                        )
                        cache[text] = embedding
                        saved_count += 1
                        total_new_embeddings += 1
                    except Exception:
                        pass

                conn.commit()
                self._log(f"    ✓ Saved {saved_count} new embeddings")
            else:
                if needed:
                    self._log(f"  ✓ {category.upper()}: All {len(needed)} values already cached")

        conn.close()

        if total_new_embeddings > 0:
            self._log(f"\n✓ Generated {total_new_embeddings} total new embeddings")
        else:
            self._log("\n✓ All embeddings already cached")

        self._log("\n📊 Step 3: Building Annoy index (TTS pattern - using rowid)...")
        self._log(f"  Creating index for {total_items} {self.item_noun}...")

        new_index = AnnoyIndex(self.embedding_dim, 'angular')
        items_processed = 0

        for rowid, category_texts in items_to_index:
            items_processed += 1
            if items_processed % 100 == 0 or items_processed == total_items:
                progress_pct = int((items_processed / total_items) * 100)
                self._log(f"    Indexed {items_processed}/{total_items} {self.item_noun} ({progress_pct}%)")

            combined_embedding = self._create_weighted_embedding(self.get_category_embeddings(category_texts))

            new_index.add_item(rowid - 1, combined_embedding)

        self._log("  🔨 Building Annoy index structure (this may take a moment)...")
        new_index.build(10)

        self._swap_in_index(new_index)

        rebuild_end = time.perf_counter()
        self._log(f"\n✓ Index rebuild completed in {rebuild_end - rebuild_start:.2f}s")
        self._log(f"✓ Indexed {total_items} {self.item_noun}")
        self._log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")

    def _swap_in_index(self, new_index: AnnoyIndex):
        ann_file_1, ann_file_2 = self._index_files()

        both_empty = (
            (not os.path.exists(ann_file_1) or self.annoy_index_1.get_n_items() == 0) and
            (not os.path.exists(ann_file_2) or self.annoy_index_2.get_n_items() == 0)
        )

        with self.index_lock:
            if both_empty:
                self._log("  💾 Full rebuild - saving to both index files...")

                new_index.save(ann_file_1)
                self._log(f"    ✓ Saved: {os.path.basename(ann_file_1)}")

                new_index.save(ann_file_2)
                self._log(f"    ✓ Saved: {os.path.basename(ann_file_2)}")

                self.annoy_index_1.load(ann_file_1)
                self.annoy_index_2.load(ann_file_2)

                self.current_index = 1
                self._log("  ✓ Both indexes built, using index 1")
            else:
                new_ann_file = ann_file_2 if self.current_index == 1 else ann_file_1
                current_index_to_update = self.annoy_index_2 if self.current_index == 1 else self.annoy_index_1

                current_index_to_update.unload()
                new_index.save(new_ann_file)
                self._log(f"  💾 Saved: {new_ann_file}")

                new_index.unload()
                current_index_to_update.load(new_ann_file)

                self.current_index = 3 - self.current_index
                self._log(f"  ✓ Switched to index {self.current_index}")

    def _extract_category_texts(self, item: Dict[str, Any]) -> Dict[str, str]:
        raise NotImplementedError

    def _create_weighted_embedding(
            self,
            category_embeddings: Dict[str, np.ndarray],
            weights: Optional[Dict[str, float]] = None
    ) -> np.ndarray:
        if weights is None:
            weights = self.default_weights

        combined = np.zeros(self.embedding_dim, dtype=np.float32)
        for category, weight in weights.items():
            if category in category_embeddings:
                combined += category_embeddings[category] * weight

        norm = np.linalg.norm(combined)
        if norm > 0:
            combined = combined / norm

        return combined
