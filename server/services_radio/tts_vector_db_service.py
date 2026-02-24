import os
import time
import datetime
import psycopg2
import numpy as np
import torch
import random
from annoy import AnnoyIndex
from threading import Lock
from typing import List, Tuple

from models_global import get_tokenizer, get_vector_model, get_device
from config.settings import settings
from services import log_service

class VectorDBService:
    def __init__(self):
        log_service.tts_vector_db("Initializing VectorDBService (PostgreSQL)")

        self.tokenizer = get_tokenizer()
        self.vector_matching_model = get_vector_model()
        self.device = get_device()

        self.db_pools = settings.TTS_DB_POOLS

        self.tts_db_data = None
        self.meta_db_data = None
        self.impulse_db_data = None
        self.audio_db_data = None

        self.annoy_index_tts_1 = AnnoyIndex(1024, 'angular')
        self.annoy_index_tts_2 = AnnoyIndex(1024, 'angular')
        self.annoy_index_meta_1 = AnnoyIndex(1024, 'angular')
        self.annoy_index_meta_2 = AnnoyIndex(1024, 'angular')
        self.annoy_index_impulse_1 = AnnoyIndex(1024, 'angular')
        self.annoy_index_impulse_2 = AnnoyIndex(1024, 'angular')
        self.annoy_index_audio_1 = AnnoyIndex(1024, 'angular')
        self.annoy_index_audio_2 = AnnoyIndex(1024, 'angular')

        self.current_index = 1
        self.new_embeddings_log = []
        self.shotgun_cache = {}
        self.last_rebuild_time = None

        self.log_lock = Lock()
        self.index_lock = Lock()
        self.shotgun_lock = Lock()

        self._initialize_databases()

    def _get_connection(self):
        return psycopg2.connect(settings.EMBEDDINGS_DATABASE_URL)

    def _initialize_databases(self):
        conn = self._get_connection()
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
        
        conn.commit()
        conn.close()
        log_service.tts_vector_db("TTS embedding tables initialized (PostgreSQL)")

    def load_initial_data(self):
        start_time = time.perf_counter()
        self.tts_db_data = {}
        self.meta_db_data = {}
        self.impulse_db_data = {}
        self.audio_db_data = {}

        db_data_results = {}
        for table_name in settings.TTS_EMBEDDING_TABLES:
            data, voice_counts = self._preload_database(table_name)
            db_data_results[table_name] = (data, voice_counts)

            if table_name == "tts_embeddings":
                self.tts_db_data = data
            elif table_name == "meta_embeddings":
                self.meta_db_data = data
            elif table_name == "impulse_embeddings":
                self.impulse_db_data = data
            elif table_name == "audio_embeddings":
                self.audio_db_data = data

        log_service.tts_vector_db("Loading Annoy indexes from disk...")
        self._load_annoy_indexes()

        end_time = time.perf_counter()
        log_service.tts_vector_db(f"load_initial_data took {end_time - start_time:.4f} seconds.")
        return db_data_results

    def _load_annoy_indexes(self):
        indexes = [
            ("tts_embeddings", self.annoy_index_tts_1, self.annoy_index_tts_2),
            ("meta_embeddings", self.annoy_index_meta_1, self.annoy_index_meta_2),
            ("impulse_embeddings", self.annoy_index_impulse_1, self.annoy_index_impulse_2),
            ("audio_embeddings", self.annoy_index_audio_1, self.annoy_index_audio_2)
        ]

        missing_files = []
        for db_name, index_1, index_2 in indexes:
            ann_file_1 = os.path.join(str(settings.EMBEDDINGS_DIR), f"{db_name}_1.ann")
            ann_file_2 = os.path.join(str(settings.EMBEDDINGS_DIR), f"{db_name}_2.ann")
            if not os.path.exists(ann_file_1):
                missing_files.append(f"{db_name}_1.ann")
            if not os.path.exists(ann_file_2):
                missing_files.append(f"{db_name}_2.ann")

        if missing_files:
            log_service.warning("  ✗ Missing index file(s): " + ", ".join(missing_files))
            log_service.warning("  🔨 Triggering rebuild to create missing index files...")
            self.rebuild_indexes()
            log_service.success("  ✓ Rebuild complete - all index files created")
            return

        for db_name, index_1, index_2 in indexes:
            ann_file_1 = os.path.join(str(settings.EMBEDDINGS_DIR), f"{db_name}_1.ann")
            ann_file_2 = os.path.join(str(settings.EMBEDDINGS_DIR), f"{db_name}_2.ann")

            try:
                index_1.load(ann_file_1)
                items_1 = index_1.get_n_items()
                log_service.tts_vector_db(f"  ✓ Loaded {db_name}_1.ann: {items_1} items")

                index_2.load(ann_file_2)
                items_2 = index_2.get_n_items()
                log_service.tts_vector_db(f"  ✓ Loaded {db_name}_2.ann: {items_2} items")

                if items_1 == 0 and items_2 == 0:
                    log_service.warning(f"  ✗ Both {db_name} indexes are empty (0 items)")
                    log_service.warning(f"  🔨 Triggering rebuild for {db_name}...")
                    self.rebuild_indexes()
                    return

            except Exception as e:
                log_service.error(f"Failed to load Annoy indexes for {db_name}: {e}")
                log_service.warning(f"  🔨 Triggering rebuild for {db_name}...")
                self.rebuild_indexes()

    def _preload_database(self, table_name: str):
        conn = self._get_connection()
        c = conn.cursor()
        
        c.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = %s
            )
        """, (table_name,))
        fetch_result = c.fetchone()
        if fetch_result is None:
            table_exists = False
        else:
            table_exists = fetch_result[0]
        
        if not table_exists:
            log_service.tts_vector_db(f"Vector Database: {table_name} table not found. It will be created when needed.")
            conn.close()
            return {}, {}

        c.execute(f"SELECT id, filename, title, embedding, voice FROM {table_name}")
        data = {}
        voice_counts = {}

        for row in c.fetchall():
            data[row[0]] = {
                'filename': row[1],
                'title': row[2],
                'embedding': np.frombuffer(bytes(row[3]), dtype=np.float32),
                'voice': row[4]
            }
            voice_counts[row[4]] = voice_counts.get(row[4], 0) + 1

        conn.close()

        if not data:
            log_service.tts_vector_db(f"Vector Database: {table_name} table is empty. It will be populated as responses are generated.")
        else:
            log_service.tts_vector_db(f"Vector Database: Loaded {len(data)} embeddings from {table_name}.")
            for voice, count in voice_counts.items():
                log_service.tts_vector_db(f"Vector Database:   {voice}: {count} embeddings")

        return data, voice_counts

    def _generate_embedding(self, text: str) -> np.ndarray:
        inputs = self.tokenizer.encode_plus(
            text,
            return_tensors='pt',
            max_length=512,
            truncation=True,
            padding='max_length'
        )
        inputs = {k: v.to(self.device) for k, v in inputs.items()}

        with torch.no_grad():
            outputs = self.vector_matching_model(**inputs)

        last_hidden_state = outputs.last_hidden_state
        embedding = last_hidden_state[0][-1].cpu().numpy()

        return embedding

    def save_embedding(self, audio_path: str, text: str, voice_name: str, db_type: str):
        embedding = self._generate_embedding(text)

        conn = self._get_connection()
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
                (os.path.basename(audio_path), text, embedding.tobytes(), voice_name)
            )
            fetch_result = c.fetchone()
            if fetch_result is None:
                new_id = None
            else:
                new_id = fetch_result[0]
            conn.commit()
        except Exception as e:
            log_service.error(f"Failed to save embedding to {db_type}: {e}")
            conn.rollback()
            new_id = None
        finally:
            conn.close()

        if new_id:
            with self.log_lock:
                self.new_embeddings_log.append((
                    new_id,
                    embedding,
                    os.path.basename(audio_path),
                    text,
                    voice_name,
                    db_type
                ))

            log_service.tts_vector_db(f"Vector Cache: Saved new {db_type} embedding to cache and memory: {os.path.basename(audio_path)}")

    def query_embeddings(
            self,
            response_str: str,
            voice_name: str,
            db_type: str,
            top_n: int = 5
    ) -> List[Tuple[str, str, float]]:
        query_start_time = time.perf_counter()
        query_embedding = self._generate_embedding(response_str)
        query_embedding = query_embedding / np.linalg.norm(query_embedding)

        current_time = time.time()
        log_service.tts_vector_db(f"Vector Cache: Querying for similar {db_type} responses to: '{response_str[:50]}...' for voice: {voice_name}")

        results = []
        skipped_count = 0

        if db_type == "tts_embeddings":
            current_annoy_index = self.annoy_index_tts_1 if self.current_index == 1 else self.annoy_index_tts_2
        elif db_type == "meta_embeddings":
            current_annoy_index = self.annoy_index_meta_1 if self.current_index == 1 else self.annoy_index_meta_2
        elif db_type == "impulse_embeddings":
            current_annoy_index = self.annoy_index_impulse_1 if self.current_index == 1 else self.annoy_index_impulse_2
        elif db_type == "audio_embeddings":
            current_annoy_index = self.annoy_index_audio_1 if self.current_index == 1 else self.annoy_index_audio_2
        else:
            log_service.error(f"Unknown database type: {db_type}")
            return results

        with self.index_lock:
            if current_annoy_index.get_n_items() == 0:
                log_service.warning(f"Annoy index for {db_type} is empty. Queries may be slow or incomplete.")
                nearest_ids = []
            else:
                nearest_ids = current_annoy_index.get_nns_by_vector(query_embedding, top_n * 10)

        conn = self._get_connection()
        c = conn.cursor()
        all_matches = []

        for item_id in nearest_ids:
            c.execute(f"SELECT filename, title, embedding, voice FROM {db_type} WHERE id = %s", (item_id + 1,))
            result = c.fetchone()
            if result:
                filename, title, embedding_bytes, db_voice = result
                if db_voice == voice_name:
                    embedding = np.frombuffer(bytes(embedding_bytes), dtype=np.float32)
                    embedding = embedding / np.linalg.norm(embedding)
                    similarity = np.dot(query_embedding, embedding)

                    with self.shotgun_lock:
                        if filename not in self.shotgun_cache or (
                                current_time - self.shotgun_cache[filename]) >= settings.VECTOR_DB_SHOTGUN_COOLDOWN:
                            all_matches.append((filename, title, similarity))
                        else:
                            skipped_count += 1

        with self.log_lock:
            if len(all_matches) < top_n:
                for _row_id, embedding, filename, title, item_voice, item_db_type in self.new_embeddings_log:
                    if item_voice == voice_name and item_db_type == db_type:
                        embedding = embedding / np.linalg.norm(embedding)
                        similarity = np.dot(query_embedding, embedding)
                        with self.shotgun_lock:
                            if filename not in self.shotgun_cache or (
                                    current_time - self.shotgun_cache[filename]) >= settings.VECTOR_DB_SHOTGUN_COOLDOWN:
                                all_matches.append((filename, title, similarity))
                            else:
                                skipped_count += 1

        conn.close()

        all_matches.sort(key=lambda x: x[2], reverse=True)

        while all_matches and len(results) < top_n:
            current_similarity = all_matches[0][2]
            identical_matches = [m for m in all_matches if abs(m[2] - current_similarity) < 1e-10]

            if len(identical_matches) > 1:
                selected_match = random.choice(identical_matches)
            else:
                selected_match = identical_matches[0]

            results.append(selected_match)
            all_matches = [m for m in all_matches if abs(m[2] - current_similarity) >= 1e-10]

        if results:
            top_filename = results[0][0]
            with self.shotgun_lock:
                self.shotgun_cache[top_filename] = current_time

        log_service.tts_vector_db(f"Vector Cache: Process complete: {len(results)} results selected, {skipped_count} skipped")

        query_end_time = time.perf_counter()
        log_service.tts_vector_db(f"VectorDB query_embeddings for '{response_str[:30]}...' took {query_end_time - query_start_time:.4f} seconds.")
        return results

    def rebuild_indexes(self):
        rebuild_start_time = time.perf_counter()

        def _verify_file_saved(file_path: str, timeout: int = 5) -> bool:
            start_time = time.time()
            while time.time() - start_time < timeout:
                if os.path.exists(file_path):
                    try:
                        with open(file_path, 'rb') as f:
                            f.read(1)
                        return True
                    except IOError:
                        time.sleep(0.1)
            return False

        log_service.tts_vector_db("Vector Database: Starting index rebuild and database sync")

        databases = [
            ("tts_embeddings", self.annoy_index_tts_1, self.annoy_index_tts_2),
            ("meta_embeddings", self.annoy_index_meta_1, self.annoy_index_meta_2),
            ("impulse_embeddings", self.annoy_index_impulse_1, self.annoy_index_impulse_2),
            ("audio_embeddings", self.annoy_index_audio_1, self.annoy_index_audio_2)
        ]

        for db_name, index_1, index_2 in databases:
            db_rebuild_start_time = time.perf_counter()
            new_index = None
            try:
                new_index = AnnoyIndex(1024, 'angular')
                ann_file_1 = os.path.join(str(settings.EMBEDDINGS_DIR), f"{db_name}_1.ann")
                ann_file_2 = os.path.join(str(settings.EMBEDDINGS_DIR), f"{db_name}_2.ann")

                conn = self._get_connection()
                c = conn.cursor()

                with self.log_lock:
                    self.new_embeddings_log[:] = [item for item in self.new_embeddings_log if item[5] != db_name]

                c.execute(f"SELECT id, embedding FROM {db_name}")
                items_added = 0
                for row in c.fetchall():
                    row_id, embedding_bytes = row
                    embedding = np.frombuffer(bytes(embedding_bytes), dtype=np.float32)
                    new_index.add_item(row_id - 1, embedding)
                    items_added += 1

                conn.close()
                log_service.tts_vector_db(f"Vector Database: Added {items_added} items from DB to the new '{db_name}' index.")

                log_service.tts_vector_db(f"Vector Database: Building new Annoy index for {db_name}")
                new_index.build(10)

                if not os.path.exists(ann_file_1) or not os.path.exists(ann_file_2):
                    new_index.save(ann_file_1)
                    new_index.save(ann_file_2)
                    log_service.tts_vector_db(f"Vector Database: Created new Annoy index files: {ann_file_1} and {ann_file_2}")

                    if _verify_file_saved(ann_file_1) and _verify_file_saved(ann_file_2):
                        new_index.unload()
                        index_1.unload()
                        index_2.unload()
                        index_1.load(ann_file_1)
                        index_2.load(ann_file_2)
                    else:
                        raise IOError(f"Failed to verify saved files: {ann_file_1} or {ann_file_2}")
                else:
                    with self.index_lock:
                        new_ann_file = ann_file_2 if self.current_index == 1 else ann_file_1
                        current_index_to_update = index_2 if self.current_index == 1 else index_1

                        current_index_to_update.unload()
                        new_index.save(new_ann_file)
                        log_service.tts_vector_db(f"Vector Database: Updated Annoy index file: {new_ann_file}")

                        if _verify_file_saved(new_ann_file):
                            new_index.unload()
                            current_index_to_update.load(new_ann_file)
                        else:
                            raise IOError(f"Failed to verify saved file: {new_ann_file}")

                log_service.tts_vector_db(f"Vector Database: Total items in the {db_name} index: {new_index.get_n_items()}")

            except Exception as e:
                log_service.error(f"Failed to rebuild/update index for {db_name}: {str(e)}")
            finally:
                if new_index is not None:
                    new_index.unload()

            db_rebuild_end_time = time.perf_counter()
            log_service.tts_vector_db(f"Rebuilding index for '{db_name}' took {db_rebuild_end_time - db_rebuild_start_time:.4f} seconds.")

        with self.index_lock:
            self.current_index = 3 - self.current_index
        log_service.tts_vector_db(f"Vector Database: Switched current_index to {self.current_index}")

        self.last_rebuild_time = datetime.datetime.now()
        log_service.tts_vector_db(f"Vector Database: Index rebuilt and database synced at {self.last_rebuild_time}")

        rebuild_end_time = time.perf_counter()
        log_service.tts_vector_db(f"VectorDB full index rebuild took {rebuild_end_time - rebuild_start_time:.4f} seconds.")