import os
import psycopg2
import numpy as np
import torch
from mutagen._util import MutagenError
from mutagen.id3 import ID3
from typing import List, Tuple, Dict, Optional
from models_global import get_tokenizer, get_vector_model, get_device
from config.settings import settings
from services import log_service


class TTSDatabaseMigrationService:

    def __init__(self):
        log_service.tts_vector_db("Initializing TTSDatabaseMigrationService (PostgreSQL)")

        self.tokenizer = get_tokenizer()
        self.vector_model = get_vector_model()
        self.device = get_device()

        self.directory_map = {
            "tts_embeddings": settings.TTS_AUDIO_DIR,
            "meta_embeddings": settings.META_AUDIO_DIR,
            "impulse_embeddings": settings.IMPULSE_AUDIO_DIR,
            "audio_embeddings": settings.AUDIO_EFFECT_DIR
        }

    def _get_connection(self):
        """Get a PostgreSQL connection to the embeddings database."""
        return psycopg2.connect(settings.EMBEDDINGS_DATABASE_URL)

    def extract_title_subtitle(self, file_path: str) -> Tuple[Optional[str], Optional[str]]:
        try:
            audio = ID3(file_path)

            title_frame = audio.getall('TIT2')
            if not title_frame:
                return None, None

            title = str(title_frame[0]).strip()

            invalid_chars = {'*', '\n', '[', ']', '🎵', '@', '$', '%', 'N/A', '"'}
            if any(char in title for char in invalid_chars):
                return None, None

            punct_count = sum(c in ".!?" for c in title)
            if punct_count > 1:
                return None, None

            if title.startswith('"') or title.endswith('"'):
                return None, None

            subtitle_frame = audio.getall('TIT3')
            if subtitle_frame:
                subtitle = str(subtitle_frame[0]).strip()
                if 'N/A' in subtitle:
                    return None, None
                return title, subtitle

            return title, None

        except (MutagenError, KeyError, Exception) as e:
            log_service.debug(f"Failed to extract ID3 tags from {file_path}: {e}")
            return None, None

    def generate_embeddings_batch(self, texts: List[str]) -> List[np.ndarray]:
        batch_size = 32
        embeddings = []

        for i in range(0, len(texts), batch_size):
            batch_texts = texts[i:i + batch_size]

            inputs = self.tokenizer.batch_encode_plus(
                batch_texts,
                return_tensors='pt',
                padding=True,
                truncation=True,
                max_length=512
            )
            inputs = {k: v.to(self.device) for k, v in inputs.items()}

            with torch.no_grad():
                outputs = self.vector_model.forward(**inputs)

            last_hidden_states = outputs.last_hidden_state  # type: ignore
            if last_hidden_states is None:
                continue

            for j, _text in enumerate(batch_texts):
                if j >= len(last_hidden_states):
                    break
                embedding = last_hidden_states[j][-1].cpu().numpy()
                embedding = embedding / np.linalg.norm(embedding)
                embeddings.append(embedding)

                if (i + j + 1) % 100 == 0:
                    log_service.tts_vector_db(f"  Generated {i + j + 1}/{len(texts)} embeddings")

        return embeddings

    def get_existing_entries(self, table_name: str) -> Dict[str, Tuple[Optional[str], Optional[str], Optional[str]]]:
        """Get existing entries from PostgreSQL table."""
        try:
            conn = self._get_connection()
            c = conn.cursor()

            # Check if table exists
            c.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = %s
                )
            """, (table_name,))
            result = c.fetchone()
            if result is None or not result[0]:
                conn.close()
                return {}

            c.execute(f"SELECT filename, title, embedding, voice FROM {table_name}")

            rows = c.fetchall()
            if rows is None:
                rows = []
            entries: Dict[str, Tuple[Optional[str], Optional[str], Optional[str]]] = {
                row[0]: (row[1], None, row[3]) for row in rows if row and len(row) >= 4
            }
            conn.close()
            return entries
        except psycopg2.Error as e:
            log_service.error(f"Error retrieving existing entries from {table_name}: {e}")
            return {}

    def remove_deleted_files_from_db(self, table_name: str, directory: str) -> int:
        """Remove DB entries for files that no longer exist on disk."""
        try:
            conn = self._get_connection()
            c = conn.cursor()

            c.execute(f"SELECT filename FROM {table_name}")
            db_files = set(row[0] for row in c.fetchall())

            existing_files = set()
            for _root, _, files in os.walk(directory):
                for file in files:
                    if file.endswith('.mp3'):
                        existing_files.add(file)

            files_to_remove = db_files - existing_files

            for file in files_to_remove:
                c.execute(f"DELETE FROM {table_name} WHERE filename = %s", (file,))
                log_service.tts_vector_db(f"  Removed DB entry for deleted file: {file}")

            conn.commit()
            conn.close()

            if files_to_remove:
                log_service.tts_vector_db(f"  Cleaned up {len(files_to_remove)} entries for deleted files")

            return len(files_to_remove)
        except psycopg2.Error as e:
            log_service.error(f"Error removing deleted file entries: {e}")
            return 0

    def process_directory(
        self,
        directory: str,
        voice_name: str,
        existing_entries: Dict[str, Tuple[Optional[str], Optional[str], Optional[str]]],
        validate_only: bool = True
    ) -> List[Tuple[str, str, str]]:

        data = []

        if not os.path.exists(directory):
            log_service.warning(f"Directory does not exist: {directory}")
            return data

        file_count = 0
        skipped_count = 0
        invalid_count = 0

        for root, _, files in os.walk(directory):
            for filename in files:
                if not filename.endswith(".mp3"):
                    continue

                file_count += 1
                file_path = os.path.join(root, filename)

                if filename in existing_entries:
                    existing_title, _, existing_voice = existing_entries[filename]
                    new_title, _new_subtitle = self.extract_title_subtitle(file_path)

                    if existing_title == new_title and existing_voice == voice_name:
                        skipped_count += 1
                        continue

                title, _subtitle = self.extract_title_subtitle(file_path)

                if title is None:
                    invalid_count += 1
                    if validate_only:
                        log_service.debug(f"  Invalid metadata (skipping): {filename}")
                    else:
                        log_service.warning(f"  Deleting file with invalid metadata: {filename}")
                        try:
                            os.remove(file_path)
                        except OSError as e:
                            log_service.error(f"Failed to delete {filename}: {e}")
                    continue

                data.append((filename, title, voice_name))

        log_service.tts_vector_db(
            f"  {voice_name}: {len(data)} new/changed, "
            f"{skipped_count} unchanged, {invalid_count} invalid (of {file_count} total)"
        )

        return data

    def store_embeddings(
        self,
        table_name: str,
        embeddings_data: List[Tuple[str, str, str, np.ndarray]]
    ) -> int:
        """Store embeddings in PostgreSQL table."""
        try:
            conn = self._get_connection()
            c = conn.cursor()

            # Ensure table exists
            c.execute(f'''
                CREATE TABLE IF NOT EXISTS {table_name} (
                    id SERIAL PRIMARY KEY,
                    filename TEXT UNIQUE NOT NULL,
                    title TEXT,
                    embedding BYTEA,
                    voice TEXT
                )
            ''')

            for filename, title, voice, embedding in embeddings_data:
                embedding_blob = embedding.tobytes()
                c.execute(
                    f"""
                    INSERT INTO {table_name} (filename, title, embedding, voice) 
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (filename) DO UPDATE SET
                        title = EXCLUDED.title,
                        embedding = EXCLUDED.embedding,
                        voice = EXCLUDED.voice
                    """,
                    (filename, title, embedding_blob, voice)
                )

            conn.commit()
            conn.close()

            log_service.tts_vector_db(f"  Stored {len(embeddings_data)} embeddings in {table_name}")
            return len(embeddings_data)
        except psycopg2.Error as e:
            log_service.error(f"Error storing embeddings in {table_name}: {e}")
            return 0

    def migrate_database(
        self,
        db_type: str,
        validate_only: bool = True,
        force_rebuild: bool = False
    ) -> Tuple[int, int]:

        if db_type not in self.directory_map:
            log_service.error(f"Unknown database type: {db_type}")
            return 0, 0

        directory = self.directory_map[db_type]

        log_service.tts_vector_db(f"\n🔨 Migrating {db_type}")
        log_service.tts_vector_db(f"  Directory: {directory}")

        log_service.tts_vector_db("\n📝 Step 1: Cleaning up deleted files...")
        cleaned = self.remove_deleted_files_from_db(db_type, str(directory))

        log_service.tts_vector_db("\n📝 Step 2: Scanning audio files...")
        existing_entries = {} if force_rebuild else self.get_existing_entries(db_type)

        all_data = []
        subdirs = [d for d in os.listdir(directory) if os.path.isdir(os.path.join(directory, d))]

        if subdirs:
            log_service.tts_vector_db(f"  Found voice directories: {', '.join(subdirs)}")
            for voice_name in subdirs:
                voice_dir = os.path.join(directory, voice_name)
                voice_data = self.process_directory(voice_dir, voice_name, existing_entries, validate_only)
                all_data.extend(voice_data)
        else:
            log_service.tts_vector_db("  No subdirectories, processing as single voice")
            all_data = self.process_directory(str(directory), "default", existing_entries, validate_only)

        if all_data:
            log_service.tts_vector_db(f"\n🚀 Step 3: Generating embeddings for {len(all_data)} entries...")

            texts = [title for _, title, _ in all_data]
            embeddings = self.generate_embeddings_batch(texts)

            embeddings_data = [
                (filename, title, voice, embedding)
                for (filename, title, voice), embedding in zip(all_data, embeddings)
            ]

            log_service.tts_vector_db("\n💾 Step 4: Storing embeddings in database...")
            stored = self.store_embeddings(db_type, embeddings_data)
            log_service.success(f"✓ Migrated {db_type}: {stored} new entries, {cleaned} cleaned")
            return stored, cleaned
        else:
            log_service.tts_vector_db(f"  No new entries to migrate for {db_type}")
            return 0, cleaned

    def migrate_all_databases(self, validate_only: bool = True, force_rebuild: bool = False) -> Dict[str, Tuple[int, int]]:

        log_service.tts_vector_db("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        log_service.tts_vector_db("🔨 TTS DATABASE MIGRATION - SCANNING DISK")
        log_service.tts_vector_db("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        results = {}
        total_new = 0
        total_cleaned = 0

        for db_type in ["tts_embeddings", "meta_embeddings", "impulse_embeddings", "audio_embeddings"]:
            new, cleaned = self.migrate_database(db_type, validate_only, force_rebuild)
            results[db_type] = (new, cleaned)
            total_new += new
            total_cleaned += cleaned

        log_service.tts_vector_db("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        log_service.success(f"✓ Migration complete: {total_new} new entries, {total_cleaned} cleaned")
        log_service.tts_vector_db("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")

        return results
