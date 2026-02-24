import os
import time
import psycopg2
import numpy as np
import torch
from annoy import AnnoyIndex
from threading import Lock
from typing import List, Dict, Any, Optional
from models_global import get_tokenizer, get_vector_model, get_device
from config.settings import settings
from services import log_service

class CatalogVectorDatabaseService:

    def __init__(self, catalog_service=None):
        log_service.vector_music("Initializing CatalogVectorDatabaseService (using global models)")

        self.catalog_service = catalog_service

        self.tokenizer = get_tokenizer()
        self.vector_model = get_vector_model()
        self.device = get_device()

        if self.device.type != 'cuda':
            log_service.warning(
                f"⚠️  Music vector service is on {self.device.type.upper()}! "
                f"GPU acceleration is recommended for better performance."
            )
        else:
            gpu_name = torch.cuda.get_device_name(0)
            log_service.vector_music(f"✓ GPU Device: {gpu_name}")
            log_service.vector_music("✓ T5 Embedding Model: google/flan-t5-large (1024-dim)")

        self.embedding_tables = settings.CATALOG_EMBEDDING_TABLES

        self.song_title_embeddings = {}
        self.primary_genre_embeddings = {}
        self.secondary_genres_embeddings = {}
        self.mood_embeddings = {}
        self.primary_artist_embeddings = {}
        self.similar_artists_embeddings = {}
        self.style_embeddings = {}
        self.theme_embeddings = {}
        self.vocal_embeddings = {}
        self.lyrics_embeddings = {}

        self.annoy_index_tracks_1 = AnnoyIndex(1024, 'angular')
        self.annoy_index_tracks_2 = AnnoyIndex(1024, 'angular')

        self.current_index = 1
        self.index_lock = Lock()

        self._track_rowid_cache: Dict[int, str] = {}
        self._track_metadata_cache: Dict[int, Dict] = {}

    def _get_connection(self):
        return psycopg2.connect(settings.EMBEDDINGS_DATABASE_URL)

    def load_initial_data(self):
        start_time = time.perf_counter()

        log_service.vector_music("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        log_service.vector_music("Loading Catalog Embedding Caches")
        log_service.vector_music("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        cache_map = {
            "song_title_embeddings": self.song_title_embeddings,
            "primary_genre_embeddings": self.primary_genre_embeddings,
            "secondary_genres_embeddings": self.secondary_genres_embeddings,
            "mood_embeddings": self.mood_embeddings,
            "primary_artist_embeddings": self.primary_artist_embeddings,
            "similar_artists_embeddings": self.similar_artists_embeddings,
            "style_embeddings": self.style_embeddings,
            "theme_embeddings": self.theme_embeddings,
            "vocal_embeddings": self.vocal_embeddings,
            "lyrics_embeddings": self.lyrics_embeddings
        }

        total_loaded = 0
        conn = self._get_connection()
        c = conn.cursor()

        for db_type, cache in cache_map.items():
            if db_type not in self.embedding_tables:
                log_service.warning(f"⚠️  Missing {db_type} in settings.CATALOG_EMBEDDING_TABLES")
                continue

            try:
                c.execute(f"SELECT text, embedding FROM {db_type}")
                count = 0
                for text, embedding_bytes in c.fetchall():
                    cache[text] = np.frombuffer(bytes(embedding_bytes), dtype=np.float32)
                    count += 1
                    total_loaded += 1
                if count > 0:
                    log_service.vector_music(f"  ✓ {db_type}: {count} embeddings")
            except Exception as e:
                log_service.error(f"Error loading {db_type}: {e}")

        conn.close()

        if total_loaded > 0:
            log_service.vector_music(f"✓ Loaded {total_loaded} total cached embeddings into memory")
        else:
            log_service.vector_music("  No cached embeddings found (will generate on first use)")

        log_service.vector_music("\nLoading Annoy indexes from disk...")
        self._load_annoy_indexes()

        end_time = time.perf_counter()
        log_service.vector_music(f"✓ load_initial_data completed in {end_time - start_time:.2f}s")
        log_service.vector_music("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")

    def _load_annoy_indexes(self):
        ann_file_1 = os.path.join(str(settings.CATALOG_EMBEDDINGS_DIR), "catalog_1.ann")
        ann_file_2 = os.path.join(str(settings.CATALOG_EMBEDDINGS_DIR), "catalog_2.ann")

        missing_files = []
        if not os.path.exists(ann_file_1):
            missing_files.append("catalog_1.ann")
        if not os.path.exists(ann_file_2):
            missing_files.append("catalog_2.ann")

        if missing_files:
            log_service.warning(f"  ✗ Missing file(s): {', '.join(missing_files)}")
            log_service.warning("  🔨 Triggering rebuild...")
            self.rebuild_indexes()
            log_service.success("  ✓ Rebuild complete")
            return

        try:
            self.annoy_index_tracks_1.load(ann_file_1)
            items_1 = self.annoy_index_tracks_1.get_n_items()
            log_service.vector_music(f"  ✓ Loaded catalog_1.ann: {items_1} tracks")

            self.annoy_index_tracks_2.load(ann_file_2)
            items_2 = self.annoy_index_tracks_2.get_n_items()
            log_service.vector_music(f"  ✓ Loaded catalog_2.ann: {items_2} tracks")

            if items_1 == 0 and items_2 == 0:
                log_service.warning("  ✗ Both indexes are empty (0 tracks)")
                log_service.warning("  🔨 Triggering rebuild...")
                self.rebuild_indexes()
                return

            log_service.success("  ✓ All indexes loaded from disk successfully (TTS pattern - query by rowid)")

        except Exception as e:
            log_service.error(f"Failed to load indexes: {e}")
            log_service.warning("  🔨 Triggering rebuild...")
            self.rebuild_indexes()

    def _generate_embedding(self, text: str) -> np.ndarray:
        if not text or not text.strip():
            return np.zeros(1024, dtype=np.float32)

        inputs = self.tokenizer.encode_plus(
            text,
            return_tensors='pt',
            max_length=512,
            truncation=True,
            padding='max_length'
        )
        inputs = {k: v.to(self.device) for k, v in inputs.items()}

        with torch.no_grad():
            outputs = self.vector_model(**inputs)

        hidden_state = outputs.last_hidden_state
        attention_mask = inputs['attention_mask']

        mask = attention_mask.unsqueeze(-1).expand(hidden_state.shape)
        masked_hidden_state = hidden_state * mask

        sum_hidden_state = masked_hidden_state.sum(dim=1)
        sum_mask = mask.sum(dim=1)
        sum_mask = torch.clamp(sum_mask, min=1e-9)

        mean_embedding = sum_hidden_state / sum_mask

        embedding = mean_embedding[0].cpu().numpy()

        norm = np.linalg.norm(embedding)
        if norm > 0:
            embedding = embedding / norm

        return embedding.astype(np.float32)

    def _generate_embeddings_batch(self, texts: List[str], batch_size: int = 32) -> List[np.ndarray]:
        if not texts:
            return []

        total = len(texts)
        log_service.vector_music(f"    Generating {total} embeddings in batches of {batch_size}...")

        all_embeddings = []
        for i in range(0, total, batch_size):
            batch = texts[i:i + batch_size]
            batch_num = (i // batch_size) + 1
            total_batches = (total + batch_size - 1) // batch_size

            if batch_num % 5 == 1 or batch_num == total_batches:
                progress_pct = int((i / total) * 100)
                log_service.vector_music(
                    f"      Batch {batch_num}/{total_batches}: "
                    f"{i}/{total} embeddings ({progress_pct}%)"
                )

            processed_texts = [text if text and text.strip() else " " for text in batch]

            inputs = self.tokenizer.batch_encode_plus(
                processed_texts,
                return_tensors='pt',
                max_length=512,
                truncation=True,
                padding='max_length'
            )
            inputs = {k: v.to(self.device) for k, v in inputs.items()}

            with torch.no_grad():
                outputs = self.vector_model(**inputs)

            hidden_state = outputs.last_hidden_state
            attention_mask = inputs['attention_mask']

            mask = attention_mask.unsqueeze(-1).expand(hidden_state.shape)
            masked_hidden_state = hidden_state * mask

            sum_hidden_state = masked_hidden_state.sum(dim=1)
            sum_mask = mask.sum(dim=1)
            sum_mask = torch.clamp(sum_mask, min=1e-9)

            mean_embeddings = sum_hidden_state / sum_mask

            embeddings = mean_embeddings.cpu().numpy()

            for j, embedding in enumerate(embeddings):
                if not batch[j] or not batch[j].strip():
                    all_embeddings.append(np.zeros(1024, dtype=np.float32))
                else:
                    norm = np.linalg.norm(embedding)
                    if norm > 0:
                        embedding = embedding / norm
                    all_embeddings.append(embedding.astype(np.float32))

        log_service.vector_music(f"    ✓ Generated {total} embeddings")
        return all_embeddings

    def get_or_create_embedding(self, text: str, db_type: str, cache: Dict[str, np.ndarray]) -> np.ndarray:
        if not text or not text.strip():
            return np.zeros(1024, dtype=np.float32)

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

    def add_single_track(self, track_data: dict) -> bool:
        try:
            category_texts = self._extract_category_texts(track_data)

            cache_map = {
                "song_title": (self.song_title_embeddings, "song_title_embeddings"),
                "primary_genre": (self.primary_genre_embeddings, "primary_genre_embeddings"),
                "secondary_genres": (self.secondary_genres_embeddings, "secondary_genres_embeddings"),
                "mood": (self.mood_embeddings, "mood_embeddings"),
                "primary_artist": (self.primary_artist_embeddings, "primary_artist_embeddings"),
                "similar_artists": (self.similar_artists_embeddings, "similar_artists_embeddings"),
                "style": (self.style_embeddings, "style_embeddings"),
                "theme": (self.theme_embeddings, "theme_embeddings"),
                "vocal": (self.vocal_embeddings, "vocal_embeddings"),
                "lyrics": (self.lyrics_embeddings, "lyrics_embeddings")
            }

            conn = self._get_connection()
            c = conn.cursor()

            for category, text in category_texts.items():
                if text and text.strip() and category in cache_map:
                    cache, db_type = cache_map[category]
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
            conn.close()
            return True
        except Exception as e:
            log_service.error(f"Failed to add track embeddings: {e}")
            return False

    def rebuild_indexes(self, catalog_service=None):
        rebuild_start = time.perf_counter()

        log_service.vector_music("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        log_service.vector_music("🔨 REBUILDING CATALOG VECTOR INDEXES")
        log_service.vector_music("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        catalog_svc = catalog_service or self.catalog_service
        if not catalog_svc:
            log_service.warning("catalog_service is None - cannot rebuild indexes")
            return
        if not catalog_svc.tracks:
            log_service.warning("catalog_service.tracks is empty - cannot rebuild indexes. Will retry when catalog is loaded.")
            return

        total_tracks = len(catalog_svc.tracks)
        log_service.vector_music(f"📊 Total tracks to index: {total_tracks}")

        log_service.vector_music("\n📝 Step 1: Querying catalog database (TTS pattern - rowid based)...")

        import json
        conn = catalog_svc._get_connection()
        c = conn.cursor()
        c.execute("SELECT rowid, track_id, metadata_json FROM tracks ORDER BY rowid")

        unique_texts_needed = {
            "song_title": set(),
            "primary_genre": set(),
            "secondary_genres": set(),
            "mood": set(),
            "primary_artist": set(),
            "similar_artists": set(),
            "style": set(),
            "theme": set(),
            "vocal": set(),
            "lyrics": set()
        }

        tracks_to_index = []
        self._track_rowid_cache.clear()
        self._track_metadata_cache.clear()
        
        for rowid, track_id, metadata_json in c.fetchall():
            track = json.loads(metadata_json)
            category_texts = self._extract_category_texts(track)
            tracks_to_index.append((rowid, track_id, track, category_texts))
            
            self._track_rowid_cache[rowid] = track_id
            self._track_metadata_cache[rowid] = track

            for category, text in category_texts.items():
                if text and text.strip():
                    unique_texts_needed[category].add(text.strip())

        conn.close()
        log_service.vector_music(f"  ✓ Cached {len(self._track_rowid_cache)} track rowids in memory")

        for category, texts in unique_texts_needed.items():
            if texts:
                log_service.vector_music(f"  • {category}: {len(texts)} unique values")

        log_service.vector_music("\n🚀 Step 2: Generating category embeddings...")

        cache_map = {
            "song_title": (self.song_title_embeddings, "song_title_embeddings"),
            "primary_genre": (self.primary_genre_embeddings, "primary_genre_embeddings"),
            "secondary_genres": (self.secondary_genres_embeddings, "secondary_genres_embeddings"),
            "mood": (self.mood_embeddings, "mood_embeddings"),
            "primary_artist": (self.primary_artist_embeddings, "primary_artist_embeddings"),
            "similar_artists": (self.similar_artists_embeddings, "similar_artists_embeddings"),
            "style": (self.style_embeddings, "style_embeddings"),
            "theme": (self.theme_embeddings, "theme_embeddings"),
            "vocal": (self.vocal_embeddings, "vocal_embeddings"),
            "lyrics": (self.lyrics_embeddings, "lyrics_embeddings")
        }

        total_new_embeddings = 0
        conn = self._get_connection()
        c = conn.cursor()

        for category, (cache, db_type) in cache_map.items():
            if db_type not in self.embedding_tables:
                continue

            # pylint: disable=unsupported-assignment-operation
            cache: Dict[str, np.ndarray]

            needed = unique_texts_needed[category]
            missing = [text for text in needed if text not in cache]

            if missing:
                log_service.vector_music(f"\n  📂 {category.upper()}")
                log_service.vector_music(f"    Total needed: {len(needed)}")
                log_service.vector_music(f"    Already cached: {len(needed) - len(missing)}")
                log_service.vector_music(f"    Need to generate: {len(missing)}")

                new_embeddings = self._generate_embeddings_batch(missing)

                log_service.vector_music(f"    💾 Saving {len(missing)} embeddings to {db_type}...")

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
                log_service.vector_music(f"    ✓ Saved {saved_count} new embeddings")
            else:
                if needed:
                    log_service.vector_music(f"  ✓ {category.upper()}: All {len(needed)} values already cached")

        conn.close()

        if total_new_embeddings > 0:
            log_service.vector_music(f"\n✓ Generated {total_new_embeddings} total new embeddings")
        else:
            log_service.vector_music("\n✓ All embeddings already cached")

        log_service.vector_music("\n📊 Step 3: Building Annoy index (TTS pattern - using rowid)...")
        log_service.vector_music(f"  Creating index for {total_tracks} tracks...")

        new_index = AnnoyIndex(1024, 'angular')
        items_processed = 0

        for rowid, track_id, track, category_texts in tracks_to_index:
            items_processed += 1
            if items_processed % 100 == 0 or items_processed == total_tracks:
                progress_pct = int((items_processed / total_tracks) * 100)
                log_service.vector_music(f"    Indexed {items_processed}/{total_tracks} tracks ({progress_pct}%)")

            category_embeddings = {
                "song_title": self.get_or_create_embedding(category_texts["song_title"],
                                                           "song_title_embeddings",
                                                           self.song_title_embeddings),
                "primary_genre": self.get_or_create_embedding(category_texts["primary_genre"],
                                                              "primary_genre_embeddings",
                                                              self.primary_genre_embeddings),
                "secondary_genres": self.get_or_create_embedding(category_texts["secondary_genres"],
                                                                 "secondary_genres_embeddings",
                                                                 self.secondary_genres_embeddings),
                "mood": self.get_or_create_embedding(category_texts["mood"], "mood_embeddings", self.mood_embeddings),
                "primary_artist": self.get_or_create_embedding(category_texts["primary_artist"],
                                                               "primary_artist_embeddings",
                                                               self.primary_artist_embeddings),
                "similar_artists": self.get_or_create_embedding(category_texts["similar_artists"],
                                                                "similar_artists_embeddings",
                                                                self.similar_artists_embeddings),
                "style": self.get_or_create_embedding(category_texts["style"], "style_embeddings",
                                                      self.style_embeddings),
                "theme": self.get_or_create_embedding(category_texts["theme"], "theme_embeddings",
                                                      self.theme_embeddings),
                "vocal": self.get_or_create_embedding(category_texts["vocal"], "vocal_embeddings",
                                                      self.vocal_embeddings),
                "lyrics": self.get_or_create_embedding(category_texts["lyrics"], "lyrics_embeddings",
                                                       self.lyrics_embeddings)
            }

            combined_embedding = self._create_weighted_embedding(category_embeddings)

            new_index.add_item(rowid - 1, combined_embedding)

        log_service.vector_music("  🔨 Building Annoy index structure (this may take a moment)...")
        new_index.build(10)

        ann_file_1 = os.path.join(str(settings.CATALOG_EMBEDDINGS_DIR), "catalog_1.ann")
        ann_file_2 = os.path.join(str(settings.CATALOG_EMBEDDINGS_DIR), "catalog_2.ann")

        both_empty = (
            (not os.path.exists(ann_file_1) or self.annoy_index_tracks_1.get_n_items() == 0) and
            (not os.path.exists(ann_file_2) or self.annoy_index_tracks_2.get_n_items() == 0)
        )

        with self.index_lock:
            if both_empty:
                log_service.vector_music("  💾 Full rebuild - saving to both index files...")

                new_index.save(ann_file_1)
                log_service.vector_music("    ✓ Saved: catalog_1.ann")

                new_index.save(ann_file_2)
                log_service.vector_music("    ✓ Saved: catalog_2.ann")

                self.annoy_index_tracks_1.load(ann_file_1)
                self.annoy_index_tracks_2.load(ann_file_2)

                self.current_index = 1
                log_service.vector_music("  ✓ Both indexes built, using index 1")
            else:
                new_ann_file = ann_file_2 if self.current_index == 1 else ann_file_1
                current_index_to_update = self.annoy_index_tracks_2 if self.current_index == 1 else self.annoy_index_tracks_1

                current_index_to_update.unload()
                new_index.save(new_ann_file)
                log_service.vector_music(f"  💾 Saved: {new_ann_file}")

                new_index.unload()
                current_index_to_update.load(new_ann_file)

                self.current_index = 3 - self.current_index
                log_service.vector_music(f"  ✓ Switched to index {self.current_index}")

        rebuild_end = time.perf_counter()
        log_service.vector_music(f"\n✓ Index rebuild completed in {rebuild_end - rebuild_start:.2f}s")
        log_service.vector_music(f"✓ Indexed {total_tracks} tracks")
        log_service.vector_music("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")

    def _extract_category_texts(self, track: Dict[str, Any]) -> Dict[str, str]:
        params = track.get("generation_params", {}) or {}
        derived_tags = track.get("derived_tags", {}) or {}
        track_info = track.get("track_info", {}) or {}

        song_title = params.get("title") or track_info.get("title") or ""
        primary_genre_text = derived_tags.get("primary_genre") or ""
        secondary_genres = derived_tags.get("secondary_genres") or []
        secondary_genres_text = ', '.join(secondary_genres) if isinstance(secondary_genres, list) else ""
        mood_keywords = derived_tags.get("mood_keywords") or []
        mood_text = ', '.join(mood_keywords) if isinstance(mood_keywords, list) else ""
        primary_artist_text = derived_tags.get("inspired_artist") or ""
        similar_artists = derived_tags.get("similar_artists") or []
        similar_artists_text = ', '.join(similar_artists) if isinstance(similar_artists, list) else ""
        style_text = params.get("style_canonical") or params.get("style") or ""
        theme_text = (derived_tags.get("lyrical_interpretation") or "")[:300]
        lyrics_text = (params.get("prompt") or "")[:200]
        vocal_keywords = derived_tags.get("vocal_style_keywords") or []
        vocal_text = ', '.join(vocal_keywords) if isinstance(vocal_keywords, list) else ""

        return {
            "song_title": song_title,
            "primary_genre": primary_genre_text,
            "secondary_genres": secondary_genres_text,
            "mood": mood_text,
            "primary_artist": primary_artist_text,
            "similar_artists": similar_artists_text,
            "style": style_text,
            "theme": theme_text,
            "lyrics": lyrics_text,
            "vocal": vocal_text
        }

    def _create_weighted_embedding(
            self,
            category_embeddings: Dict[str, np.ndarray],
            weights: Optional[Dict[str, float]] = None
    ) -> np.ndarray:
        if weights is None:
            weights = {
                "song_title": 0.0,
                "primary_genre": 0.20,
                "secondary_genres": 0.10,
                "mood": 0.20,
                "primary_artist": 0.15,
                "similar_artists": 0.10,
                "style": 0.12,
                "vocal": 0.07,
                "theme": 0.04,
                "lyrics": 0.02
            }

        combined = np.zeros(1024, dtype=np.float32)
        for category, weight in weights.items():
            if category in category_embeddings:
                combined += category_embeddings[category] * weight

        norm = np.linalg.norm(combined)
        if norm > 0:
            combined = combined / norm

        return combined