import asyncio
import psycopg2
import psycopg2.extras
import json
import os
import aiofiles
from typing import Dict, List, Optional, Union, Callable
from datetime import datetime
from services import log_service
from services.base_service import SingletonService
from config import settings
from pathlib import Path
from sqlalchemy import select
from database.models import User

class UserContentDatabaseService(SingletonService):
    def __init__(self):
        if getattr(self, '_initialized', False):
            return
        self.users_dir = settings.USERS_DIR

        self.shoutouts = {}
        self.shoutout_ids = []
        self._service_initialized = False
        self._initialized = True

        self._initialize_database()

    def _get_connection(self):
        return psycopg2.connect(settings.USER_CONTENT_DATABASE_URL)

    def _initialize_database(self):
        conn = self._get_connection()
        c = conn.cursor()

        c.execute('''CREATE TABLE IF NOT EXISTS shoutouts
                     (
                         content_id TEXT UNIQUE NOT NULL,
                         user_id INTEGER,
                         metadata_json TEXT NOT NULL,
                         created_at TEXT,
                         transcription TEXT,
                         category TEXT,
                         duration REAL,
                         username TEXT,
                         location TEXT,
                         has_mp3 INTEGER DEFAULT 0,
                         parent_id TEXT DEFAULT NULL,
                         reply_count INTEGER DEFAULT 0
                     )''')

        c.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_content_id ON shoutouts(content_id)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_shoutouts_created_at ON shoutouts(created_at DESC)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_shoutouts_user_id ON shoutouts(user_id)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_shoutouts_category ON shoutouts(category)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_shoutouts_parent_id ON shoutouts(parent_id)')

        conn.commit()
        conn.close()

        log_service.user_content("✓ User Content database table initialized (PostgreSQL)")

    async def initialize(self):
        if self._service_initialized:
            return
        log_service.user_content("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        log_service.user_content("Loading Shoutouts from JSON → PostgreSQL Database")

        if not self.users_dir.exists():
            log_service.warning(f"Users directory not found: {self.users_dir}")
            self._service_initialized = True
            return

        json_files = []

        try:
            for user_dir in self.users_dir.iterdir():
                if not user_dir.is_dir():
                    continue

                try:
                    user_id = int(user_dir.name)
                    shoutouts_dir = settings.get_user_shoutouts_dir(user_id)
                except ValueError:
                    continue

                if shoutouts_dir.exists() and shoutouts_dir.is_dir():
                    user_json_files = list(shoutouts_dir.glob("*.json"))
                    json_files.extend(user_json_files)
        except Exception as e:
            log_service.error(f"Error scanning user directories: {e}")

        existing_db_ids = set(await asyncio.to_thread(self._get_all_shoutout_ids_from_db))

        file_id_map = {}
        for f in json_files:
            try:
                user_id = f.parent.parent.name
                timestamp = f.stem
                shoutout_id = f"{user_id}_{timestamp}"
                file_id_map[shoutout_id] = f
            except Exception:
                continue

        json_file_ids = set(file_id_map.keys())
        removed_ids = existing_db_ids - json_file_ids

        if removed_ids:
            log_service.user_content(f"Removing {len(removed_ids)} shoutouts no longer on disk...")
            await asyncio.to_thread(self._bulk_delete_shoutouts, list(removed_ids))

        semaphore = asyncio.Semaphore(50)
        added_count = 0
        updated_count = 0

        async def process_file(shoutout_id, json_path):
            async with semaphore:
                try:
                    async with aiofiles.open(json_path, 'r', encoding='utf-8') as f:
                        content = await f.read()
                        metadata = json.loads(content)

                    audio_path = json_path.with_suffix('.mp3')
                    has_mp3 = audio_path.exists()

                    if has_mp3:
                        metadata['id'] = shoutout_id
                        user_id_from_path = json_path.parent.parent.name

                        is_new = await asyncio.to_thread(
                            self._upsert_shoutout_to_db,
                            shoutout_id,
                            int(user_id_from_path),
                            metadata,
                            has_mp3
                        )
                        return shoutout_id, metadata, is_new
                except Exception as e:
                    log_service.error(f"Error processing {json_path.name}: {e}")
                return None

        tasks = [process_file(sid, fpath) for sid, fpath in file_id_map.items()]
        results = await asyncio.gather(*tasks)

        for res in results:
            if res:
                sid, meta, is_new = res
                self.shoutouts[sid] = meta
                if sid not in self.shoutout_ids:
                    self.shoutout_ids.append(sid)
                if is_new:
                    added_count += 1
                else:
                    updated_count += 1

        self.shoutout_ids.sort(key=lambda sid: self.shoutouts[sid].get("timestamp", ""), reverse=True)

        log_service.user_content(
            f"📊 Sync complete: {len(self.shoutouts)} shoutouts | +{added_count} new | ~{updated_count} updated | -{len(removed_ids)} removed")
        log_service.user_content("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        self._service_initialized = True

    async def save_audio_file(self, user_id: Union[int, str], timestamp: str, audio_bytes: bytes) -> Optional[Path]:
        try:
            uploads_dir = settings.get_user_uploads_dir(int(user_id))
            webm_path = uploads_dir / f"{timestamp}.webm"

            async with aiofiles.open(webm_path, 'wb') as f:
                await f.write(audio_bytes)
            return webm_path
        except Exception as e:
            log_service.error(f"Failed to save audio file: {e}")
            return None

    async def save_metadata_file(self, user_id: Union[int, str], timestamp: str, metadata: Dict) -> Optional[Path]:
        try:
            uploads_dir = settings.get_user_uploads_dir(int(user_id))
            json_path = uploads_dir / f"{timestamp}.json"

            async with aiofiles.open(json_path, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(metadata, indent=2, ensure_ascii=False))
            return json_path
        except Exception as e:
            log_service.error(f"Failed to save metadata file: {e}")
            return None

    async def _find_latest_upload(self, user_id: int) -> Optional[Path]:
        uploads_dir = settings.get_user_uploads_dir(user_id)
        if not uploads_dir.exists():
            return None
        try:
            files = list(uploads_dir.glob("*.webm"))
            if not files:
                return None
            return max(files, key=lambda f: f.stat().st_mtime)
        except Exception as e:
            log_service.error(f"Error finding latest upload for user {user_id}: {e}")
            return None

    async def process_shoutout_upload(self, user_id: int, enhancement_service,
                                      gemini_service, vector_db_service=None, broadcast_callback: Optional[Callable] = None,
                                      parent_id: Optional[str] = None) -> tuple[bool, str]:
        try:
            webm_path = await self._find_latest_upload(user_id)
            if not webm_path:
                return (False, "")

            timestamp = webm_path.stem
            json_path = webm_path.with_suffix(".json")

            if not json_path.exists():
                return (False, "")

            shoutouts_dir = settings.get_user_shoutouts_dir(user_id)

            target_mp3_path = shoutouts_dir / f"{timestamp}.mp3"
            target_json_path = shoutouts_dir / f"{timestamp}.json"

            await enhancement_service.enhance_audio(
                str(webm_path),
                str(target_mp3_path),
                "shoutout",
                str(json_path),
                str(target_json_path),
                gemini_service
            )

            if not target_json_path.exists():
                return (False, "")

            async with aiofiles.open(target_json_path, 'r', encoding='utf-8') as f:
                processed_metadata = json.loads(await f.read())

            transcription = processed_metadata['full_transcription']

            shoutout_id = f"{user_id}_{timestamp}"
            processed_metadata['id'] = shoutout_id
            processed_metadata['content_type'] = 'reply' if parent_id else 'shoutout'

            if parent_id:
                processed_metadata['parent_id'] = parent_id
                if not self.is_root_shoutout(parent_id):
                    log_service.error(f"Cannot reply to a reply: {parent_id}")
                    return (False, "")
                if parent_id not in self.shoutouts:
                    log_service.error(f"Parent shoutout not found: {parent_id}")
                    return (False, "")

            await asyncio.to_thread(self._upsert_shoutout_to_db, shoutout_id, user_id, processed_metadata, True, parent_id)

            self.shoutouts[shoutout_id] = processed_metadata
            if shoutout_id not in self.shoutout_ids:
                self.shoutout_ids.insert(0, shoutout_id)
                self.shoutout_ids.sort(key=lambda sid: self.shoutouts[sid].get("timestamp", ""), reverse=True)

            if vector_db_service:
                await asyncio.to_thread(vector_db_service.add_single_shoutout, processed_metadata)
                all_shoutouts = await self.load_all_shoutouts_for_indexing()
                await asyncio.to_thread(vector_db_service.rebuild_indexes, all_shoutouts)

            if broadcast_callback:
                event_type = "reply" if parent_id else "shoutout"
                await broadcast_callback(event_type, shoutout_id, processed_metadata)

            content_type_label = "Reply" if parent_id else "Shoutout"
            log_service.user_content(f"✅ {content_type_label} {shoutout_id} fully indexed and ready")
            return (True, transcription)

        except Exception as e:
            log_service.error(f"Failed to process shoutout upload: {e}")
            return (False, "")

    async def process_opinion_upload(self, user_id: int, track_info: Dict, enhancement_service, gemini_service) -> bool:
        try:
            webm_path = await self._find_latest_upload(user_id)
            if not webm_path:
                return False

            json_path = webm_path.with_suffix(".json")
            if not json_path.exists():
                return False

            track_id = str(track_info.get('id', 'unknown'))
            opinions_dir = settings.OPINIONS_DIR / track_id
            opinions_dir.mkdir(parents=True, exist_ok=True)

            opinion_id = f"{user_id}_{int(datetime.now().timestamp())}"
            target_mp3_path = opinions_dir / f"{opinion_id}.mp3"
            target_json_path = opinions_dir / f"{opinion_id}.json"

            await enhancement_service.enhance_audio(
                str(webm_path),
                str(target_mp3_path),
                "opinion",
                str(json_path),
                str(target_json_path),
                gemini_service
            )

            if target_json_path.exists():
                async with aiofiles.open(target_json_path, 'r', encoding='utf-8') as f:
                    content = await f.read()
                    opinion_data = json.loads(content)

                opinion_data['track_info'] = {
                    'track_id': track_id,
                    'title': track_info.get('generation_params', {}).get('title', 'Unknown'),
                    'artist': track_info.get('generation_params', {}).get('artist_name', 'Unknown'),
                    'style': track_info.get('generation_params', {}).get('style', 'Unknown')
                }

                async with aiofiles.open(target_json_path, 'w', encoding='utf-8') as f:
                    await f.write(json.dumps(opinion_data, indent=2, ensure_ascii=False))
                return True

            return False
        except Exception as e:
            log_service.error(f"Failed to process opinion: {e}")
            return False

    async def load_all_shoutouts_for_indexing(self) -> List[Dict]:
        all_data = []
        for sid, data in self.shoutouts.items():
            item = data.copy()
            item['id'] = sid
            item['content_type'] = 'shoutout'
            if 'user_data' in item and 'timestamp' in item['user_data']:
                item['date'] = item['user_data']['timestamp']
            else:
                item['date'] = ''
            all_data.append(item)
        return all_data

    def _get_all_shoutout_ids_from_db(self) -> List[str]:
        conn = self._get_connection()
        c = conn.cursor()
        c.execute("SELECT content_id FROM shoutouts")
        ids = [row[0] for row in c.fetchall()]
        conn.close()
        return ids

    def _bulk_delete_shoutouts(self, shoutout_ids: List[str]):
        conn = self._get_connection()
        c = conn.cursor()
        c.executemany("DELETE FROM shoutouts WHERE content_id = %s", [(sid,) for sid in shoutout_ids])
        conn.commit()
        conn.close()

    def _upsert_shoutout_to_db(self, shoutout_id: str, user_id: int, metadata: Dict, has_mp3: bool,
                                parent_id: Optional[str] = None) -> bool:
        conn = self._get_connection()
        c = conn.cursor()
        c.execute("SELECT content_id FROM shoutouts WHERE content_id = %s", (shoutout_id,))
        exists = c.fetchone() is not None

        transcription = metadata.get("full_transcription", "")
        meta_info = metadata.get("transcription_metadata", {}) or {}
        user_info = metadata.get("user_data", {}) or {}

        category = meta_info.get("category", "general")
        duration = meta_info.get("duration", 0.0)
        username = user_info.get("username", "Unknown")
        location = user_info.get("location", "")
        created_at = metadata.get("timestamp", datetime.now().isoformat())

        if parent_id is None:
            parent_id = metadata.get("parent_id")

        if exists:
            c.execute('''UPDATE shoutouts
                         SET user_id=%s,
                             metadata_json=%s,
                             created_at=%s,
                             transcription=%s,
                             category=%s,
                             duration=%s,
                             username=%s,
                             location=%s,
                             has_mp3=%s,
                             parent_id=%s
                         WHERE content_id = %s''',
                      (user_id, json.dumps(metadata), created_at, transcription, category,
                       duration, username, location, int(has_mp3), parent_id, shoutout_id))
        else:
            c.execute('SELECT COALESCE(MAX(rowid), 0) + 1 FROM shoutouts')
            row_result = c.fetchone()
            next_rowid = row_result[0] if row_result else 1

            c.execute('''INSERT INTO shoutouts (rowid, user_id, metadata_json, created_at, transcription, category,
                                                duration, username, location, has_mp3, parent_id, content_id)
                         VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)''',
                      (next_rowid, user_id, json.dumps(metadata), created_at, transcription, category,
                       duration, username, location, int(has_mp3), parent_id, shoutout_id))

            if parent_id:
                c.execute("UPDATE shoutouts SET reply_count = reply_count + 1 WHERE content_id = %s", (parent_id,))

        conn.commit()
        conn.close()
        return not exists

    def get_shoutout(self, shoutout_id: str) -> Optional[Dict]:
        return self.shoutouts.get(shoutout_id)

    def get_enriched_shoutout(self, shoutout_id: str) -> Optional[Dict]:
        shoutout_data = self.shoutouts.get(shoutout_id)
        if not shoutout_data:
            return None

        try:
            uid, timestamp = shoutout_id.split('_', 1)

            enriched = shoutout_data.copy()
            enriched['id'] = shoutout_id
            enriched['audio_url'] = f"/api/user_content/shoutouts/audio/{uid}/{timestamp}.mp3"
            enriched['has_audio'] = True

            if 'full_transcription' in enriched and 'transcription' not in enriched:
                enriched['transcription'] = enriched['full_transcription']

            if 'user_data' in enriched and 'user_id' not in enriched:
                enriched['user_id'] = enriched['user_data'].get('user_id')

            reply_info = self._get_reply_info(shoutout_id)
            enriched['reply_count'] = reply_info.get('reply_count', 0)
            enriched['parent_id'] = reply_info.get('parent_id')
            enriched['is_reply'] = enriched['parent_id'] is not None

            return enriched
        except (ValueError, KeyError):
            return None

    def _get_reply_info(self, shoutout_id: str) -> Dict:
        try:
            conn = self._get_connection()
            c = conn.cursor()
            c.execute("SELECT reply_count, parent_id FROM shoutouts WHERE content_id = %s", (shoutout_id,))
            row = c.fetchone()
            conn.close()
            if row:
                return {'reply_count': row[0] or 0, 'parent_id': row[1]}
            return {'reply_count': 0, 'parent_id': None}
        except Exception:
            return {'reply_count': 0, 'parent_id': None}

    async def enrich_shoutout_results(self, results: List[Dict], db_session=None) -> List[Dict]:
        user_ids = list(set(
            int(r.get('user_data', {}).get('user_id', 0)) for r in results if r.get('user_data', {}).get('user_id')))

        if not user_ids or not db_session:
            return results

        try:
            result = await db_session.execute(select(User).where(User.id.in_(user_ids)))
            users_map = {u.id: u for u in result.scalars().all()}

            for r in results:
                try:
                    uid = int(r.get('user_data', {}).get('user_id', 0))
                    if uid in users_map:
                        r['profile_picture'] = users_map[uid].profile_picture
                        if hasattr(users_map[uid], 'username'):
                            if 'user_data' not in r:
                                r['user_data'] = {}
                            r['user_data']['username'] = users_map[uid].username
                            r['username'] = users_map[uid].username

                    if 'user_data' in r:
                        r['user_id'] = r['user_data'].get('user_id')
                except (ValueError, TypeError):
                    pass
            return results
        except Exception as e:
            log_service.error(f"Error enriching shoutouts: {e}")
            return results

    def delete_shoutout(self, shoutout_id: str, cascade_replies: bool = True) -> bool:
        if shoutout_id not in self.shoutouts:
            return False

        try:
            conn = self._get_connection()
            c = conn.cursor()

            c.execute("SELECT parent_id FROM shoutouts WHERE content_id = %s", (shoutout_id,))
            row = c.fetchone()
            parent_id = row[0] if row else None

            reply_ids = []
            if cascade_replies:
                c.execute("SELECT content_id FROM shoutouts WHERE parent_id = %s", (shoutout_id,))
                reply_ids = [r[0] for r in c.fetchall()]

            conn.close()

            for reply_id in reply_ids:
                self._delete_shoutout_files_and_memory(reply_id)

            self._delete_shoutout_files_and_memory(shoutout_id)

            conn = self._get_connection()
            c = conn.cursor()

            if reply_ids:
                c.execute("DELETE FROM shoutouts WHERE content_id = ANY(%s)", (reply_ids,))
                log_service.user_content(f"Cascade deleted {len(reply_ids)} replies for shoutout {shoutout_id}")

            c.execute("DELETE FROM shoutouts WHERE content_id = %s", (shoutout_id,))

            if parent_id:
                c.execute("UPDATE shoutouts SET reply_count = GREATEST(0, reply_count - 1) WHERE content_id = %s", (parent_id,))

            conn.commit()
            conn.close()

            return True
        except Exception as e:
            log_service.error(f"Failed to delete shoutout {shoutout_id}: {e}")
            return False

    def _delete_shoutout_files_and_memory(self, shoutout_id: str):
        try:
            uid, timestamp = shoutout_id.split('_', 1)
            user_dir = settings.get_user_shoutouts_dir(int(uid))
            json_path = user_dir / f"{timestamp}.json"
            mp3_path = user_dir / f"{timestamp}.mp3"

            if json_path.exists():
                os.remove(json_path)
            if mp3_path.exists():
                os.remove(mp3_path)

            if shoutout_id in self.shoutouts:
                del self.shoutouts[shoutout_id]
            if shoutout_id in self.shoutout_ids:
                self.shoutout_ids.remove(shoutout_id)
        except Exception as e:
            log_service.error(f"Error deleting files for {shoutout_id}: {e}")

    def get_stats(self, category: Optional[str] = None) -> Dict:
        total = 0
        duration = 0.0
        categories = {}
        for t in self.shoutouts.values():
            shoutout_category = t.get("transcription_metadata", {}).get("category", "unknown")
            if category:
                if shoutout_category.lower() != category.lower():
                    continue
            total += 1
            dur = t.get("transcription_metadata", {}).get("duration", 0)
            if dur:
                duration += float(dur)
            if shoutout_category:
                categories[shoutout_category] = categories.get(shoutout_category, 0) + 1
        return {
            "total_shoutouts": total,
            "total_duration_ms": int(duration * 1000),
            "total_duration_formatted": self._format_duration(int(duration * 1000)),
            "category_counts": categories
        }

    def _format_duration(self, ms: int) -> str:
        m, s = divmod(ms // 1000, 60)
        h, m = divmod(m, 60)
        return f"{h}h {m}m" if h else f"{m}m {s}s"

    def get_replies(self, parent_id: str, sort_by: str = 'popularity') -> List[Dict]:
        try:
            conn = self._get_connection()
            c = conn.cursor()
            c.execute("SELECT content_id FROM shoutouts WHERE parent_id = %s", (parent_id,))
            reply_ids = [row[0] for row in c.fetchall()]
            conn.close()

            replies = []
            for rid in reply_ids:
                enriched = self.get_enriched_shoutout(rid)
                if enriched:
                    replies.append(enriched)

            if sort_by == 'popularity':
                replies.sort(key=lambda r: r.get('popularity_score', 0), reverse=True)
            else:
                replies.sort(key=lambda r: r.get('timestamp', ''), reverse=True)

            return replies
        except Exception as e:
            log_service.error(f"Error getting replies for {parent_id}: {e}")
            return []

    def is_root_shoutout(self, shoutout_id: str) -> bool:
        try:
            conn = self._get_connection()
            c = conn.cursor()
            c.execute("SELECT parent_id FROM shoutouts WHERE content_id = %s", (shoutout_id,))
            row = c.fetchone()
            conn.close()
            return row is None or row[0] is None
        except Exception:
            return True