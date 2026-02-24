import asyncio
import psycopg2
import psycopg2.extras
import json
import re
import numpy as np
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Any
from services import log_service
from services.base_service import SingletonService
from config import settings

class CatalogDatabaseService(SingletonService):
    def __init__(self):
        if getattr(self, '_initialized', False):
            return
        self.metadata_dir = settings.METADATA_DIR
        self.audio_dir = settings.AUDIO_DIR
        self.artwork_dir = settings.ARTWORK_DIR
        self.tracks = {}
        self.track_ids = []
        self._catalog_initialized = False
        self._artwork_cache = {}
        self._initialized = True
        self.vector_db_service = None
        self.broadcast_callback = None
        self._initialize_database()

    def _get_connection(self):
        return psycopg2.connect(settings.CATALOG_DATABASE_URL)

    def _initialize_database(self):
        conn = self._get_connection()
        c = conn.cursor()

        c.execute('''CREATE TABLE IF NOT EXISTS tracks
                     (
                         track_id TEXT UNIQUE NOT NULL,
                         metadata_json TEXT NOT NULL,
                         created_at TEXT,
                         title TEXT,
                         primary_artist TEXT,
                         primary_genre TEXT,
                         duration INTEGER,
                         instrumental INTEGER,
                         vocal_gender TEXT,
                         has_mp3 INTEGER DEFAULT 0,
                         has_wav INTEGER DEFAULT 0,
                         has_artwork INTEGER DEFAULT 0,
                         is_ai_generated INTEGER DEFAULT 1,
                         uploaded_by_user_id INTEGER DEFAULT NULL
                     )''')

        c.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_track_id ON tracks(track_id)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_created_at ON tracks(created_at DESC)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_primary_genre ON tracks(primary_genre)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_primary_artist ON tracks(primary_artist)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_uploaded_by_user_id ON tracks(uploaded_by_user_id)')

        conn.commit()
        conn.close()

        log_service.catalog("✓ Catalog database initialized (PostgreSQL)")

    async def initialize(self):
        if self._catalog_initialized:
            return
        log_service.catalog("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        log_service.catalog("Loading Catalog from JSON → PostgreSQL Database")

        if not self.metadata_dir.exists():
            log_service.warning(f"Metadata directory not found: {self.metadata_dir}")
            self._catalog_initialized = True
            return

        from services.suno_metadata_service import MusicMetadata
        metadata_service = MusicMetadata()

        json_files = await asyncio.to_thread(list, self.metadata_dir.glob("*.json"))

        existing_db_ids = set(await asyncio.to_thread(self._get_all_track_ids_from_db))
        json_file_ids = {f.stem for f in json_files}

        removed_ids = existing_db_ids - json_file_ids
        if removed_ids:
            log_service.catalog(f"Removing {len(removed_ids)} tracks no longer in JSON files...")
            await asyncio.to_thread(self._bulk_delete_tracks, list(removed_ids))

        master_wav_dir = settings.ENHANCED_WAV_DIR
        no_master_ids = []
        for tid in existing_db_ids - removed_ids:
            wav_path = master_wav_dir / f"{tid}.wav"
            if not await asyncio.to_thread(wav_path.exists):
                no_master_ids.append(tid)
        if no_master_ids:
            log_service.catalog(f"Removing {len(no_master_ids)} tracks without master WAV...")
            await asyncio.to_thread(self._bulk_delete_tracks, no_master_ids)

        semaphore = asyncio.Semaphore(50)
        master_wav_dir = settings.ENHANCED_WAV_DIR
        added_count = 0
        updated_count = 0

        async def process_file(json_file):
            async with semaphore:
                try:
                    metadata = await metadata_service.load_metadata(json_file.name)
                    if not metadata:
                        return None

                    track_id = json_file.stem

                    audio_formats = ['.mp3', '.wav', '.flac', '.ogg', '.m4a', '.aac', '.opus', '.webm']
                    audio_exists = False
                    for ext in audio_formats:
                        audio_path = self.audio_dir / f"{track_id}{ext}"
                        if await asyncio.to_thread(audio_path.exists):
                            audio_exists = True
                            break

                    wav_path = master_wav_dir / f"{track_id}.wav"
                    art_path = self.artwork_dir / f"{track_id}.jpeg"

                    wav_exists, art_exists = await asyncio.gather(
                        asyncio.to_thread(wav_path.exists),
                        asyncio.to_thread(art_path.exists)
                    )

                    if wav_exists:
                        metadata['id'] = track_id
                        is_new = await asyncio.to_thread(self._upsert_track_to_db, track_id, metadata, audio_exists,
                                                         wav_exists, art_exists)
                        self._artwork_cache[track_id] = art_exists
                        return (track_id, metadata, is_new)
                except Exception as e:
                    log_service.error(f"Error processing {json_file.name}: {e}")
                return None

        tasks = [process_file(f) for f in json_files]
        results = await asyncio.gather(*tasks)

        for res in results:
            if res:
                tid, meta, is_new = res
                self.tracks[tid] = meta
                if tid not in self.track_ids:
                    self.track_ids.append(tid)
                if is_new:
                    added_count += 1
                else:
                    updated_count += 1

        self.track_ids.sort(key=lambda tid: self.tracks[tid].get("created_at", ""), reverse=True)

        log_service.catalog(
            f"📊 Sync complete: {len(self.tracks)} total | +{added_count} new | ~{updated_count} updated | -{len(removed_ids)} removed")
        log_service.catalog("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        self._catalog_initialized = True

    def _get_all_track_ids_from_db(self) -> List[str]:
        conn = self._get_connection()
        c = conn.cursor()
        c.execute("SELECT track_id FROM tracks")
        ids = [row[0] for row in c.fetchall()]
        conn.close()
        return ids

    def _bulk_delete_tracks(self, track_ids: List[str]):
        conn = self._get_connection()
        c = conn.cursor()
        c.executemany("DELETE FROM tracks WHERE track_id = %s", [(tid,) for tid in track_ids])
        conn.commit()
        conn.close()

    def _delete_track_from_db(self, track_id: str):
        conn = self._get_connection()
        c = conn.cursor()
        c.execute("DELETE FROM tracks WHERE track_id = %s", (track_id,))
        conn.commit()
        conn.close()

    def _upsert_track_to_db(self, track_id: str, metadata: Dict, has_mp3: bool, has_wav: bool,
                            has_artwork: bool) -> bool:
        params = metadata.get("generation_params", {}) or {}
        derived = metadata.get("derived_tags", {}) or {}
        info = metadata.get("track_info", {}) or {}

        title = (params.get("title") or "").strip()
        artist = (params.get("artist_name") or "").strip()
        genre = (derived.get("primary_genre") or "").strip()
        vocal_gender = (params.get("vocal_gender") or "").strip()

        is_ai_generated = 1 if metadata.get("is_ai_generated", True) else 0
        uploaded_by_user_id = metadata.get("uploaded_by_user_id")

        conn = self._get_connection()
        c = conn.cursor()
        c.execute("SELECT track_id FROM tracks WHERE track_id = %s", (track_id,))
        exists = c.fetchone() is not None

        if exists:
            c.execute('''UPDATE tracks
                         SET metadata_json=%s,
                             created_at=%s,
                             title=%s,
                             primary_artist=%s,
                             primary_genre=%s,
                             duration=%s,
                             instrumental=%s,
                             vocal_gender=%s,
                             has_mp3=%s,
                             has_wav=%s,
                             has_artwork=%s,
                             is_ai_generated=%s,
                             uploaded_by_user_id=%s
                         WHERE track_id = %s''',
                      (json.dumps(metadata), metadata.get("created_at", ""), title,
                       artist, genre, info.get("duration", 0),
                       1 if params.get("instrumental") else 0, vocal_gender,
                       int(has_mp3), int(has_wav), int(has_artwork),
                       is_ai_generated, uploaded_by_user_id, track_id))
        else:
            c.execute('SELECT COALESCE(MAX(rowid), 0) + 1 FROM tracks')
            row_result = c.fetchone()
            next_rowid = row_result[0] if row_result else 1

            c.execute('''INSERT INTO tracks (rowid, metadata_json, created_at, title, primary_artist, primary_genre,
                                             duration, instrumental, vocal_gender, has_mp3, has_wav, has_artwork,
                                             is_ai_generated, uploaded_by_user_id, track_id)
                         VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)''',
                      (next_rowid, json.dumps(metadata), metadata.get("created_at", ""), title,
                       artist, genre, info.get("duration", 0),
                       1 if params.get("instrumental") else 0, vocal_gender,
                       int(has_mp3), int(has_wav), int(has_artwork),
                       is_ai_generated, uploaded_by_user_id, track_id))

        conn.commit()
        conn.close()
        return not exists

    def get_track(self, track_id: str) -> Optional[Dict]:
        return self.tracks.get(track_id)

    def get_all_tracks(self, skip: int = 0, limit: int = 100, sort_by: str = "created_at", order: str = "desc",
                       genre: Optional[str] = None, banned_ids: Optional[set] = None) -> tuple:
        ids = self.track_ids

        if genre:
            ids = [tid for tid in ids if
                   (self.tracks[tid].get("derived_tags", {}).get("primary_genre") or "").lower() == genre.lower()]

        if banned_ids:
            ids = [tid for tid in ids if tid not in banned_ids]

        filtered_total = len(ids)

        reverse = (order == "desc")
        if sort_by == "title":
            ids = sorted(ids, key=lambda tid: (self.tracks[tid].get("generation_params", {}).get("title") or "").lower(),
                         reverse=reverse)
        elif sort_by == "artist_name":
            ids = sorted(ids,
                         key=lambda tid: (self.tracks[tid].get("generation_params", {}).get("artist_name") or "").lower(),
                         reverse=reverse)
        elif sort_by == "genre":
            ids = sorted(ids,
                         key=lambda tid: (self.tracks[tid].get("derived_tags", {}).get("primary_genre") or "zzz").lower(),
                         reverse=reverse)
        elif sort_by != "created_at":
            ids = sorted(ids, key=lambda tid: self.tracks[tid].get("created_at", ""), reverse=reverse)

        return [self.tracks[tid] for tid in ids[skip:skip + limit]], filtered_total

    def has_artwork(self, track_id: str) -> bool:
        if track_id in self._artwork_cache:
            return self._artwork_cache[track_id]
        exists = (self.artwork_dir / f"{track_id}.jpeg").exists()
        self._artwork_cache[track_id] = exists
        return exists

    def update_track_artwork_status(self, track_id: str, has_artwork: bool) -> bool:
        conn = self._get_connection()
        c = conn.cursor()

        try:
            c.execute('''UPDATE tracks SET has_artwork = %s WHERE track_id = %s''',
                      (int(has_artwork), track_id))
            conn.commit()

            self._artwork_cache[track_id] = has_artwork

            log_service.info(f"[Catalog] Updated artwork status for {track_id}: {has_artwork}")
            return True
        except Exception as e:
            log_service.error(f"[Catalog] Failed to update artwork status: {e}")
            return False
        finally:
            conn.close()

    def set_vector_services(self, vector_db_service=None, broadcast_callback=None):
        self.vector_db_service = vector_db_service
        self.broadcast_callback = broadcast_callback

    async def reload_catalog(self):
        old_count = len(self.tracks)
        self.tracks, self.track_ids, self._catalog_initialized = {}, [], False
        await self.initialize()
        new_track_count = len(self.tracks) - old_count

        if new_track_count > 0 and self.vector_db_service:
            await asyncio.to_thread(
                self.vector_db_service.rebuild_indexes,
                self
            )
            log_service.success(
                f"✅ Catalog vector indexes rebuilt immediately (+{new_track_count} new tracks)"
            )

            if self.broadcast_callback:
                await self.broadcast_callback("track", "batch_reload", {
                    "new_tracks_count": new_track_count,
                    "total_tracks": len(self.tracks)
                })

        return new_track_count

    def get_all_genres(self) -> List[Dict]:
        counts = Counter()
        sub_genres = {}
        for t in self.tracks.values():
            d = t.get("derived_tags", {})
            pg = d.get("primary_genre")
            if pg:
                counts[pg] += 1
                if pg not in sub_genres:
                    sub_genres[pg] = set()
                sub_genres[pg].update(d.get("secondary_genres", []))

        return sorted([{"genre": g, "count": c, "sub_genres": sorted(list(sub_genres[g]))} for g, c in counts.items()],
                      key=lambda x: x["count"], reverse=True)

    def get_audio_path(self, track_id: str) -> Optional[Path]:
        return self.audio_dir / f"{track_id}.mp3" if track_id in self.tracks else None

    def get_artwork_path(self, track_id: str) -> Optional[Path]:
        return self.artwork_dir / f"{track_id}.jpeg" if self.has_artwork(track_id) else None

    def get_repeated_titles(self, min_occurrences: int = 2) -> List[str]:
        counts = Counter(
            (self.tracks[tid].get("generation_params", {}).get("title") or "").strip().lower()
            for tid in self.tracks if self.tracks[tid].get("generation_params", {}).get("title")
        )
        return [t for t, c in counts.most_common() if c >= min_occurrences]

    def get_repeated_artists(self) -> List[str]:
        counts = Counter(
            (self.tracks[tid].get("generation_params", {}).get("artist_name") or "").strip().lower()
            for tid in self.tracks if self.tracks[tid].get("generation_params", {}).get("artist_name")
        )
        if not counts:
            return []
        threshold = np.percentile(list(counts.values()), 85)
        return [a for a, c in counts.most_common() if c > threshold and c > 1]

    def get_repeated_phrases(self, min_occurrences: int = 2, top_n: int = 20) -> List[str]:
        phrase_counts = Counter()
        for tid in self.tracks:
            prompt = self.tracks[tid].get("generation_params", {}).get("prompt", "")
            if not prompt:
                continue
            words = re.sub(r'\[.*?]|\(.*?\)', '', prompt).lower().split()
            for n in range(4, 7):
                for i in range(len(words) - n + 1):
                    p = ' '.join(words[i:i + n])
                    if len(p) > 15:
                        phrase_counts[p] += 1
        return [p for p, c in phrase_counts.most_common(top_n) if c >= min_occurrences]

    def get_cycled_phrases(self, cycle_index: int = 0, phrases_per_cycle: int = 5, top_n: int = 20) -> List[str]:
        phrases = self.get_repeated_phrases(min_occurrences=2, top_n=top_n)
        if not phrases:
            return []
        idx = (cycle_index * phrases_per_cycle) % len(phrases)
        return (phrases + phrases)[idx: idx + phrases_per_cycle]

    async def get_tracks_by_artist(self, artist_name: str) -> Dict[str, Any]:
        artist_lower = (artist_name or "").strip().lower()

        matching_tracks = []
        for tid in self.tracks:
            track = self.tracks[tid]
            track_artist = (track.get("generation_params", {}).get("artist_name") or "").strip().lower()
            inspired_artist = (track.get("derived_tags", {}).get("inspired_artist") or "").strip().lower()

            if track_artist == artist_lower or inspired_artist == artist_lower:
                matching_tracks.append(track)

        if not matching_tracks:
            return {
                "artist": artist_name,
                "track_count": 0,
                "message": f"No existing tracks found for {artist_name}. This is a NEW artist for your catalog!"
            }

        unique_tracks = {}
        seen_titles = set()

        for track in matching_tracks:
            title = (track.get("generation_params", {}).get("title") or "Untitled").strip()
            title_lower = title.lower()

            if title_lower in seen_titles:
                continue

            theme = track.get("derived_tags", {}).get("lyrical_interpretation", "")

            if title and theme:
                unique_tracks[title] = theme
                seen_titles.add(title_lower)

        existing_tracks = [
            {"title": title, "theme": theme}
            for title, theme in list(unique_tracks.items())[:15]
        ]

        result = {
            "artist": artist_name,
            "track_count": len(matching_tracks),
            "unique_track_count": len(unique_tracks),
            "existing_tracks": existing_tracks,
            "advice": f"You have {len(unique_tracks)} unique tracks for {artist_name}. Avoid repeating these titles and themes - create something FRESH with different subject matter."
        }

        try:
            log_service.catalog(f"[Catalog Tool] Returning data for '{artist_name}':")
            log_service.catalog(f"  - {len(matching_tracks)} total tracks ({len(unique_tracks)} unique)")
        except Exception as e:
            log_service.warning(f"[Catalog Tool] Log error: {e}")

        return result

    def get_stats(self, genre: Optional[str] = None) -> Dict:
        total, duration, inst, vocal = 0, 0, 0, 0
        for tid, t in self.tracks.items():
            if genre:
                track_genre = (t.get("derived_tags", {}).get("primary_genre") or "").lower()
                if track_genre != genre.lower():
                    continue
            total += 1
            duration += t.get("track_info", {}).get("duration", 0)
            if t.get("generation_params", {}).get("instrumental"):
                inst += 1
            else:
                vocal += 1
        return {
            "total_tracks": total, "total_duration_ms": duration,
            "total_duration_formatted": self._format_duration(duration),
            "instrumental_count": inst, "vocal_count": vocal
        }

    def _format_duration(self, ms: int) -> str:
        m, s = divmod(ms // 1000, 60)
        h, m = divmod(m, 60)
        return f"{h}h {m}m" if h else f"{m}m {s}s"