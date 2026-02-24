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

class UserContentVectorDatabaseService:

    def __init__(self, user_content_service=None):
        log_service.user_content("Initializing UserContentVectorDatabaseService (using global models)")

        self.user_content_service = user_content_service

        self.tokenizer = get_tokenizer()
        self.vector_model = get_vector_model()
        self.device = get_device()

        if self.device.type != 'cuda':
            log_service.warning(
                f"⚠️  User content vector service is on {self.device.type.upper()}! "
                f"GPU acceleration is recommended for better performance."
            )
        else:
            gpu_name = torch.cuda.get_device_name(0)
            log_service.user_content(f"✓ GPU Device: {gpu_name}")
            log_service.user_content("✓ T5 Embedding Model: google/flan-t5-large (1024-dim)")

        self.embedding_tables = settings.USER_CONTENT_EMBEDDING_TABLES

        self.transcription_embeddings = {}
        self.category_embeddings = {}
        self.urgency_embeddings = {}
        self.importance_embeddings = {}
        self.tags_embeddings = {}
        self.username_embeddings = {}
        self.location_embeddings = {}
        self.target_audience_embeddings = {}
        self.sentiment_embeddings = {}
        self.content_theme_embeddings = {}

        self.annoy_index_content_1 = AnnoyIndex(1024, 'angular')
        self.annoy_index_content_2 = AnnoyIndex(1024, 'angular')

        self.current_index = 1
        self.index_lock = Lock()

        self._content_rowid_cache: Dict[int, str] = {}
        self._content_metadata_cache: Dict[int, Dict] = {}

    def _get_connection(self):
        return psycopg2.connect(settings.EMBEDDINGS_DATABASE_URL)

    def load_initial_data(self):
        start_time = time.perf_counter()

        log_service.user_content("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        log_service.user_content("Loading User Content Embedding Caches")
        log_service.user_content("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        cache_map = {
            "transcription_embeddings": self.transcription_embeddings,
            "category_embeddings": self.category_embeddings,
            "urgency_embeddings": self.urgency_embeddings,
            "importance_embeddings": self.importance_embeddings,
            "tags_embeddings": self.tags_embeddings,
            "username_embeddings": self.username_embeddings,
            "location_embeddings": self.location_embeddings,
            "target_audience_embeddings": self.target_audience_embeddings,
            "sentiment_embeddings": self.sentiment_embeddings,
            "content_theme_embeddings": self.content_theme_embeddings
        }

        total_loaded = 0
        conn = self._get_connection()
        c = conn.cursor()

        for db_type, cache in cache_map.items():
            if db_type not in self.embedding_tables:
                log_service.warning(f"⚠️  Missing {db_type} in settings.USER_CONTENT_EMBEDDING_TABLES")
                continue

            try:
                c.execute(f"SELECT text, embedding FROM {db_type}")
                count = 0
                for text, embedding_bytes in c.fetchall():
                    cache[text] = np.frombuffer(bytes(embedding_bytes), dtype=np.float32)
                    count += 1
                    total_loaded += 1
                if count > 0:
                    log_service.user_content(f"  ✓ {db_type}: {count} embeddings")
            except Exception as e:
                log_service.error(f"Error loading {db_type}: {e}")

        conn.close()

        if total_loaded > 0:
            log_service.user_content(f"✓ Loaded {total_loaded} total cached embeddings into memory")
        else:
            log_service.user_content("  No cached embeddings found (will generate on first use)")

        log_service.user_content("\nLoading Annoy indexes from disk...")
        self._load_annoy_indexes()

        end_time = time.perf_counter()
        log_service.user_content(f"✓ load_initial_data completed in {end_time - start_time:.2f}s")
        log_service.user_content("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")

    def _load_annoy_indexes(self):
        ann_file_1 = os.path.join(str(settings.USER_CONTENT_EMBEDDINGS_DIR), "user_content_1.ann")
        ann_file_2 = os.path.join(str(settings.USER_CONTENT_EMBEDDINGS_DIR), "user_content_2.ann")

        missing_files = []
        if not os.path.exists(ann_file_1):
            missing_files.append("user_content_1.ann")
        if not os.path.exists(ann_file_2):
            missing_files.append("user_content_2.ann")

        if missing_files:
            log_service.warning(f"  ✗ Missing file(s): {', '.join(missing_files)}")
            log_service.warning("  🔨 Triggering rebuild...")
            self._rebuild_if_data_available()
            return

        try:
            self.annoy_index_content_1.load(ann_file_1)
            items_1 = self.annoy_index_content_1.get_n_items()
            log_service.user_content(f"  ✓ Loaded user_content_1.ann: {items_1} items")

            self.annoy_index_content_2.load(ann_file_2)
            items_2 = self.annoy_index_content_2.get_n_items()
            log_service.user_content(f"  ✓ Loaded user_content_2.ann: {items_2} items")

            if items_1 == 0 and items_2 == 0:
                log_service.warning("  ✗ Both indexes are empty (0 items)")
                log_service.warning("  🔨 Triggering rebuild...")
                self._rebuild_if_data_available()
                return

            log_service.success("  ✓ All indexes loaded from disk successfully (TTS pattern - query by rowid)")

        except Exception as e:
            log_service.error(f"Failed to load indexes: {e}")
            log_service.warning("  🔨 Triggering rebuild...")
            self._rebuild_if_data_available()

    def _rebuild_if_data_available(self):
        if self.user_content_service and hasattr(self.user_content_service, 'shoutouts') and self.user_content_service.shoutouts:
            shoutouts_list = list(self.user_content_service.shoutouts.values())
            self.rebuild_indexes(shoutouts_list)
            log_service.success("  ✓ Rebuild complete")
        else:
            log_service.user_content("  ⚠️  No user content available yet - indexes will be built when content is added")

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
        log_service.user_content(f"    Generating {total} embeddings in batches of {batch_size}...")

        all_embeddings = []
        for i in range(0, total, batch_size):
            batch = texts[i:i + batch_size]
            batch_num = (i // batch_size) + 1
            total_batches = (total + batch_size - 1) // batch_size

            if batch_num % 5 == 1 or batch_num == total_batches:
                progress_pct = int((i / total) * 100)
                log_service.user_content(
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

        log_service.user_content(f"    ✓ Generated {total} embeddings")
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

    def add_single_shoutout(self, shoutout_data: Dict[str, Any]) -> bool:
        try:
            category_texts = self._extract_category_texts(shoutout_data)

            cache_map = {
                "transcription": (self.transcription_embeddings, "transcription_embeddings"),
                "category": (self.category_embeddings, "category_embeddings"),
                "urgency": (self.urgency_embeddings, "urgency_embeddings"),
                "importance": (self.importance_embeddings, "importance_embeddings"),
                "tags": (self.tags_embeddings, "tags_embeddings"),
                "username": (self.username_embeddings, "username_embeddings"),
                "location": (self.location_embeddings, "location_embeddings"),
                "target_audience": (self.target_audience_embeddings, "target_audience_embeddings"),
                "sentiment": (self.sentiment_embeddings, "sentiment_embeddings"),
                "content_theme": (self.content_theme_embeddings, "content_theme_embeddings")
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
            log_service.error(f"Failed to add shoutout embeddings: {e}")
            return False

    def rebuild_indexes(self, _shoutouts_data: Optional[List[Dict[str, Any]]] = None):
        rebuild_start = time.perf_counter()

        log_service.user_content("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        log_service.user_content("🔨 REBUILDING USER CONTENT VECTOR INDEXES")
        log_service.user_content("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        log_service.user_content("\n📝 Step 1: Querying user_content database (TTS pattern - rowid based)...")

        import json
        if self.user_content_service is None:
            log_service.error("Cannot rebuild indexes: user_content_service is not available")
            return
        conn = self.user_content_service._get_connection()
        c = conn.cursor()
        c.execute("SELECT rowid, content_id, metadata_json FROM shoutouts ORDER BY rowid")

        unique_texts_needed = {
            "transcription": set(),
            "category": set(),
            "urgency": set(),
            "importance": set(),
            "tags": set(),
            "username": set(),
            "location": set(),
            "target_audience": set(),
            "sentiment": set(),
            "content_theme": set()
        }

        items_to_index = []
        self._content_rowid_cache.clear()  # Clear old cache
        self._content_metadata_cache.clear()
        
        for rowid, content_id, metadata_json in c.fetchall():
            item = json.loads(metadata_json)
            category_texts = self._extract_category_texts(item)
            items_to_index.append((rowid, content_id, item, category_texts))
            
            self._content_rowid_cache[rowid] = content_id
            self._content_metadata_cache[rowid] = item

            for category, text in category_texts.items():
                if text and text.strip():
                    unique_texts_needed[category].add(text.strip())
        
        conn.close()
        log_service.user_content(f"  ✓ Cached {len(self._content_rowid_cache)} content rowids in memory")

        conn.close()

        total_items = len(items_to_index)
        if total_items == 0:
            log_service.warning("No user content available for indexing")
            return

        log_service.user_content(f"📊 Total items to index: {total_items}")

        for category, texts in unique_texts_needed.items():
            if texts:
                log_service.user_content(f"  • {category}: {len(texts)} unique values")

        log_service.user_content("\n🚀 Step 2: Generating category embeddings...")

        cache_map = {
            "transcription": (self.transcription_embeddings, "transcription_embeddings"),
            "category": (self.category_embeddings, "category_embeddings"),
            "urgency": (self.urgency_embeddings, "urgency_embeddings"),
            "importance": (self.importance_embeddings, "importance_embeddings"),
            "tags": (self.tags_embeddings, "tags_embeddings"),
            "username": (self.username_embeddings, "username_embeddings"),
            "location": (self.location_embeddings, "location_embeddings"),
            "target_audience": (self.target_audience_embeddings, "target_audience_embeddings"),
            "sentiment": (self.sentiment_embeddings, "sentiment_embeddings"),
            "content_theme": (self.content_theme_embeddings, "content_theme_embeddings")
        }

        total_new_embeddings = 0
        emb_conn = self._get_connection()
        emb_c = emb_conn.cursor()

        for category, (cache, db_type) in cache_map.items():
            if db_type not in self.embedding_tables:
                continue

            needed = unique_texts_needed[category]
            missing = [text for text in needed if text not in cache]

            if missing:
                log_service.user_content(f"\n  📂 {category.upper()}")
                log_service.user_content(f"    Total needed: {len(needed)}")
                log_service.user_content(f"    Already cached: {len(needed) - len(missing)}")
                log_service.user_content(f"    Need to generate: {len(missing)}")

                new_embeddings = self._generate_embeddings_batch(missing)

                log_service.user_content(f"    💾 Saving {len(missing)} embeddings to {db_type}...")

                saved_count = 0
                for text, embedding in zip(missing, new_embeddings):
                    try:
                        emb_c.execute(
                            f"INSERT INTO {db_type} (text, embedding) VALUES (%s, %s) ON CONFLICT (text) DO NOTHING",
                            (text, embedding.tobytes())
                        )
                        if cache is not None:
                            cache[text] = embedding  # pylint: disable=unsupported-assignment-operation
                        saved_count += 1
                        total_new_embeddings += 1
                    except Exception:
                        pass

                emb_conn.commit()
                log_service.user_content(f"    ✓ Saved {saved_count} new embeddings")
            else:
                if needed:
                    log_service.user_content(f"  ✓ {category.upper()}: All {len(needed)} values already cached")

        emb_conn.close()

        if total_new_embeddings > 0:
            log_service.user_content(f"\n✓ Generated {total_new_embeddings} total new embeddings")
        else:
            log_service.user_content("\n✓ All embeddings already cached")

        log_service.user_content("\n📊 Step 3: Building Annoy index (TTS pattern - using rowid)...")
        log_service.user_content(f"  Creating index for {total_items} items...")

        new_index = AnnoyIndex(1024, 'angular')
        items_processed = 0

        for rowid, content_id, item, category_texts in items_to_index:
            items_processed += 1
            if items_processed % 100 == 0 or items_processed == total_items:
                progress_pct = int((items_processed / total_items) * 100)
                log_service.user_content(f"    Indexed {items_processed}/{total_items} items ({progress_pct}%)")

            category_embeddings = {
                "transcription": self.get_or_create_embedding(category_texts["transcription"],
                                                              "transcription_embeddings",
                                                              self.transcription_embeddings),
                "category": self.get_or_create_embedding(category_texts["category"],
                                                         "category_embeddings",
                                                         self.category_embeddings),
                "urgency": self.get_or_create_embedding(category_texts["urgency"],
                                                        "urgency_embeddings",
                                                        self.urgency_embeddings),
                "importance": self.get_or_create_embedding(category_texts["importance"],
                                                           "importance_embeddings",
                                                           self.importance_embeddings),
                "tags": self.get_or_create_embedding(category_texts["tags"],
                                                     "tags_embeddings",
                                                     self.tags_embeddings),
                "username": self.get_or_create_embedding(category_texts["username"],
                                                         "username_embeddings",
                                                         self.username_embeddings),
                "location": self.get_or_create_embedding(category_texts["location"],
                                                         "location_embeddings",
                                                         self.location_embeddings),
                "target_audience": self.get_or_create_embedding(category_texts["target_audience"],
                                                                "target_audience_embeddings",
                                                                self.target_audience_embeddings),
                "sentiment": self.get_or_create_embedding(category_texts["sentiment"],
                                                          "sentiment_embeddings",
                                                          self.sentiment_embeddings),
                "content_theme": self.get_or_create_embedding(category_texts["content_theme"],
                                                              "content_theme_embeddings",
                                                              self.content_theme_embeddings)
            }

            combined_embedding = self._create_weighted_embedding(category_embeddings)

            new_index.add_item(rowid - 1, combined_embedding)

        log_service.user_content("  🔨 Building Annoy index structure (this may take a moment)...")
        new_index.build(10)

        ann_file_1 = os.path.join(str(settings.USER_CONTENT_EMBEDDINGS_DIR), "user_content_1.ann")
        ann_file_2 = os.path.join(str(settings.USER_CONTENT_EMBEDDINGS_DIR), "user_content_2.ann")

        both_empty = (
            (not os.path.exists(ann_file_1) or self.annoy_index_content_1.get_n_items() == 0) and
            (not os.path.exists(ann_file_2) or self.annoy_index_content_2.get_n_items() == 0)
        )

        with self.index_lock:
            if both_empty:
                log_service.user_content("  💾 Full rebuild - saving to both index files...")

                new_index.save(ann_file_1)
                log_service.user_content("    ✓ Saved: user_content_1.ann")

                new_index.save(ann_file_2)
                log_service.user_content("    ✓ Saved: user_content_2.ann")

                self.annoy_index_content_1.load(ann_file_1)
                self.annoy_index_content_2.load(ann_file_2)

                self.current_index = 1
                log_service.user_content("  ✓ Both indexes built, using index 1")
            else:
                new_ann_file = ann_file_2 if self.current_index == 1 else ann_file_1
                current_index_to_update = self.annoy_index_content_2 if self.current_index == 1 else self.annoy_index_content_1

                current_index_to_update.unload()
                new_index.save(new_ann_file)
                log_service.user_content(f"  💾 Saved: {new_ann_file}")

                new_index.unload()
                current_index_to_update.load(new_ann_file)

                self.current_index = 3 - self.current_index
                log_service.user_content(f"  ✓ Switched to index {self.current_index}")

        rebuild_end = time.perf_counter()
        log_service.user_content(f"\n✓ Index rebuild completed in {rebuild_end - rebuild_start:.2f}s")
        log_service.user_content(f"✓ Indexed {total_items} items")
        log_service.user_content("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")

    @staticmethod
    def _extract_category_texts(item: Dict[str, Any]) -> Dict[str, str]:
        metadata = item.get("transcription_metadata", {}) or {}
        user_data = item.get("user_data", {}) or {}

        transcription = item.get('full_transcription', '')
        category = metadata.get("category", "")
        urgency_label = metadata.get("urgency_label", "")
        importance_label = metadata.get("importance_label", "")
        tags = metadata.get("tags") or []
        tags_text = ', '.join(tags) if isinstance(tags, list) else ""
        username = user_data.get("username", "")
        location = user_data.get("location", "")
        target_audience = metadata.get("target_audience", "")
        sentiment = metadata.get("sentiment", "")

        content_theme = transcription[:200] if transcription else ""

        return {
            "transcription": transcription,
            "category": category,
            "urgency": urgency_label,
            "importance": importance_label,
            "tags": tags_text,
            "username": username,
            "location": location,
            "target_audience": target_audience,
            "sentiment": sentiment,
            "content_theme": content_theme
        }

    @staticmethod
    def _create_weighted_embedding(
            category_embeddings: Dict[str, np.ndarray],
            weights: Optional[Dict[str, float]] = None
    ) -> np.ndarray:
        if weights is None:
            weights = {
                "transcription": 0.30,
                "category": 0.15,
                "urgency": 0.10,
                "importance": 0.10,
                "tags": 0.15,
                "username": 0.05,
                "location": 0.05,
                "target_audience": 0.05,
                "sentiment": 0.03,
                "content_theme": 0.02
            }

        combined = np.zeros(1024, dtype=np.float32)
        for category, weight in weights.items():
            if category in category_embeddings:
                combined += category_embeddings[category] * weight

        norm = np.linalg.norm(combined)
        if norm > 0:
            combined = combined / norm

        return combined