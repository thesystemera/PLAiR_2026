"""The video clip library: 30 s clips from YouTube that the background director cuts into a song's edit.

- A song asks for one clip per search term (its `video_search_terms`, 4-15 by length). Each term gets the closest clip
  in the library by meaning (the shared encoder on the term each clip was found with), at least
  YOUTUBE_CLIPS_MATCH_THRESHOLD, never two clips from one source video in one song; the per-listener anti-repeat (the
  voice cache's shotgun) keeps a clip from showing in song after song.
- Only a term with nothing close enough is downloaded: the playing song's first, then the catalog's in chart order,
  one clip every 3600 / YOUTUBE_CLIPS_PER_HOUR seconds. A new clip joins the library at once and covers every term
  close to its own.
- The library is YOUTUBE_CLIPS_DIR (clip_index.json and the MP4s). Above YOUTUBE_CLIPS_MAX_CACHE_GB the clips used
  least recently go.
"""
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from collections import OrderedDict, deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiofiles
import numpy as np

from config.settings import BASE_DIR, settings
from models_global import run_on_gpu_executor, run_on_rank_executor
from services import log_service
from services.semantic_source import Category, SemanticVectorDatabaseService, field_text
from services.vector_store import VectorStore, prime_query_vectors

INDEX_VERSION = 2
LEGACY_DIR = BASE_DIR / "data" / "youtube_clips"
TERM = "clip_term"
CLIP_SECONDS = 30
SECONDS_PER_CLIP = 15
START_OFFSET_S = 10
KEY_FRAME_S = 2
MIN_SOURCE_SECONDS = 30
MAX_SOURCE_SECONDS = 6 * 3600
SEARCH_RESULTS = 5
DOWNLOAD_TRIES = 3
PICK_DEPTH = 24
FAILED_SEARCH_RETRY_S = 24 * 3600
SEARCH_TIMEOUT_S = 30
DOWNLOAD_TIMEOUT_S = 120
ENCODE_TIMEOUT_S = 60
STALE_TEMP_S = DOWNLOAD_TIMEOUT_S + ENCODE_TIMEOUT_S + 60
EMBED_CHUNK = 512
CHART_TRACKS = 500
FILL_PASS_IDLE_S = 1800
REPORT_EVERY_S = 3600
RECENT_SONGS = 2000

VIDEO_SEARCH_TERMS_PROMPT = """
**video_search_terms**: 10-15 YouTube search queries for background video art

You are a professional VJ (video jockey) curating visuals for a live music performance.
Your job is to find footage that ENHANCES the music - not generic backgrounds, but
visuals that create a cohesive audio-visual experience.

THINK LIKE A VJ:
- READ THE LYRICS carefully - extract specific imagery, locations, themes, emotions
- MATCH THE ENERGY - fast cuts for high-energy, slow/atmospheric for ambient
- BE LITERAL when lyrics mention specific things (war, dancing, rain, cities)
- BE ABSTRACT when the mood calls for it (patterns, particles, textures)
- CONSIDER THE ERA - 80s synth gets VHS/CRT aesthetics, modern EDM gets clean digital

CONTEXT-AWARE MATCHING:
- Political/protest lyrics → "protest march documentary", "demonstration crowd footage", "activist rally 4k", "riot footage news", "revolution documentary", "civil rights archive footage"
- Love/romance → "couple silhouette sunset", "intimate moments cinema", "romantic rain scene", "love story cinematography"
- Isolation/loneliness → "empty city streets night", "lone figure walking", "abandoned places urban exploration", "solitary person window rain"
- Party/celebration → "crowd dancing festival 4k", "nightclub lights POV", "concert crowd energy", "rave lasers smoke"
- Nature/peace → "forest morning mist 4k", "ocean waves aerial drone", "mountain timelapse clouds", "nature documentary peaceful"
- Aggression/anger → "fire explosion slow motion", "storm lightning 4k", "industrial machinery heavy", "destruction demolition footage"
- Psychedelic/trippy → "fractal zoom infinite", "kaleidoscope patterns", "liquid light show 60s", "DMT visuals simulation", "mandelbrot zoom"
- Futuristic/sci-fi → "cyberpunk city rain", "hologram interface", "space nebula 4k", "matrix code rain", "blade runner aesthetic"
- Retro/nostalgic → "VHS glitch overlay", "80s TV static", "vintage film grain", "old home movies footage", "CRT monitor aesthetic"
- Trance/EDM → "VJ loops abstract", "particle system 4k", "geometric patterns morph", "tunnel infinite zoom", "waveform visualizer"

GENRE-SPECIFIC VJ THINKING:
- Electronic/Dance: Abstract geometry, particles, tunnels, waveforms, club visuals, laser shows
- Rock/Metal: Fire, storms, industrial, concert crowds, urban decay, dramatic skies
- Hip-Hop/Rap: Urban streets, city life, cars, nightlife, documentary style, gritty realism
- Indie/Folk: Nature, vintage film, intimate moments, road trips, golden hour, 16mm aesthetic
- Classical/Ambient: Slow nature, timelapses, abstract art, flowing water, minimalist
- Punk/Hardcore: DIY footage, mosh pits, political imagery, raw documentary, handheld chaos

SEARCH TERM QUALITY:
- Add qualifiers: "4k", "cinematic", "footage", "timelapse", "slow motion", "aerial drone"
- Be SPECIFIC: "tokyo neon rain night" not "city rain"
- Reference visual styles: "Terrence Malick style nature", "Gaspar Noé club scene"
- Use YouTube-friendly terms: "stock footage", "b-roll", "visual"
- NEVER use: "music video", "lyric video", "background music", "royalty free"

VARIETY IS KEY - include:
- 3-4 LITERAL terms (what lyrics actually describe)
- 3-4 ATMOSPHERIC terms (mood/feeling of the track)
- 3-4 ABSTRACT/TEXTURE terms (VJ loops, patterns, overlays)
- 2-3 MOVEMENT terms (match the BPM/energy - slow or fast)
"""

WEIGHTS = {TERM: 1.0}


def _shotgun_key(file: str) -> str:
    return f"video_clip:{file}"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class ClipLibraryVectorDatabaseService(SemanticVectorDatabaseService):
    category_specs = (Category(TERM, 1.0, field_text("keyword"), "The search term the clip was found with"),)
    display_name = "Video Clips"
    key_field = "file"
    item_noun = "clips"

    def __init__(self, clips: Dict[str, Dict[str, Any]]):
        super().__init__()
        self.clips = clips

    def load(self) -> None:
        self._load_tables()
        self.ensure_embeddings(TERM, [meta.get("keyword", "") for meta in list(self.clips.values())], True)
        VectorStore.load(self)

    def load_rows(self, keys: Optional[List[str]] = None) -> List[Tuple[int, str, Any]]:
        wanted = set(keys) if keys is not None else None
        return [(row, file, dict(meta, file=file)) for row, (file, meta) in enumerate(list(self.clips.items()), start=1)
                if wanted is None or file in wanted]

    def term_vectors(self, terms: List[str], persist: bool) -> np.ndarray:
        return np.array(self.ensure_embeddings(TERM, terms, persist), dtype=np.float32).reshape(len(terms), -1)

    def best_similarity(self, vectors: np.ndarray) -> np.ndarray:
        best = np.full(len(vectors), -1.0, dtype=np.float32)
        slots, hidden = self.views()
        for slot in slots:
            matrix = slot.matrices.get(TERM)
            if matrix is None or not len(matrix):
                continue
            scores = matrix @ vectors.T
            if hidden:
                gone = np.array([entry.key in hidden for entry in slot.entries])
                if gone.any():
                    scores[gone] = -1.0
            best = np.maximum(best, scores.max(axis=0))
        return best


class YouTubeClipService:

    def __init__(self):
        self.cache_dir: Path = settings.YOUTUBE_CLIPS_DIR
        self.index_path = self.cache_dir / "clip_index.json"
        self.index: Dict[str, Any] = {"version": INDEX_VERSION, "clips": {}, "failed": {}}
        self.library: Optional[ClipLibraryVectorDatabaseService] = None
        self.index_lock = asyncio.Lock()
        self.index_dirty = False

        self.ytdlp_path = shutil.which("yt-dlp") or str(Path(sys.executable).parent / "yt-dlp.exe")
        self.runtime_args = ["--encoding", "utf-8"] + (["--js-runtimes", "node"] if shutil.which("node") else [])
        self.process_kwargs = {"creationflags": subprocess.IDLE_PRIORITY_CLASS} if sys.platform == "win32" else {}
        self.failed_searches: Dict[str, float] = {}
        self.priority: Dict[str, None] = {}
        self.recent_picks: OrderedDict = OrderedDict()
        self.fill_queue: deque = deque()
        self.next_fill_pass = 0.0
        self.work_event = asyncio.Event()
        self.consecutive_failures = 0
        self.backoff_until = 0.0
        self.max_cache_bytes = int(settings.YOUTUBE_CLIPS_MAX_CACHE_GB * 1024 ** 3)
        self.cache_bytes = 0
        self.downloaded_since_report = 0
        self.last_report = time.time()

    async def prepare(self):
        await asyncio.to_thread(self._prepare)

    def _prepare(self):
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._move_legacy_library()
        self.index = self._load_index()
        self.failed_searches = dict(self.index["failed"])
        clips = self.index["clips"]
        for file in list(clips):
            path = self.cache_dir / file
            if path.exists():
                self.cache_bytes += path.stat().st_size
            else:
                del clips[file]
        library = ClipLibraryVectorDatabaseService(clips)
        library.load()
        self.library = library
        log_service.success(f"✓ Video clip library: {len(clips)} clips, {self.cache_bytes / 1024 ** 3:.1f} GB "
                            f"in {self.cache_dir}")

    def _move_legacy_library(self):
        legacy_index = LEGACY_DIR / "clip_index.json"
        if self.cache_dir.resolve() == LEGACY_DIR.resolve() or self.index_path.exists() or not legacy_index.exists():
            return
        clips = json.loads(legacy_index.read_text(encoding="utf-8")).get("clips", {})
        moved = 0
        for entry in clips.values():
            source = LEGACY_DIR / Path(entry.get("path", "")).name
            if source.is_file():
                shutil.move(str(source), str(self.cache_dir / source.name))
                moved += 1
        shutil.copy2(legacy_index, self.index_path)
        os.replace(legacy_index, LEGACY_DIR / "clip_index.moved.json")
        log_service.success(f"✓ Video clip library moved: {moved} clips from {LEGACY_DIR} to {self.cache_dir}")

    def _load_index(self) -> Dict[str, Any]:
        empty = {"version": INDEX_VERSION, "clips": {}, "failed": {}}
        if not self.index_path.exists():
            return empty
        try:
            raw = json.loads(self.index_path.read_text(encoding="utf-8"))
        except Exception as e:
            log_service.warning(f"[YOUTUBE] Failed to load index: {e}")
            return empty
        clips = raw.get("clips", {})
        if raw.get("version") != INDEX_VERSION:
            clips = {Path(entry["path"]).name: {key: value for key, value in entry.items() if key != "path"}
                     for entry in clips.values() if entry.get("path")}
        return {"version": INDEX_VERSION, "clips": clips, "failed": raw.get("failed", {})}

    async def _save_index(self):
        async with self.index_lock:
            self.index_dirty = False
            payload = json.dumps(self.index)
            temp = self.index_path.with_suffix(".tmp")

            def write():
                temp.write_text(payload, encoding="utf-8")
                os.replace(temp, self.index_path)
            try:
                await asyncio.to_thread(write)
            except Exception as e:
                log_service.error(f"[YOUTUBE] Failed to save index: {e}")

    def _failed_key(self, term: str) -> str:
        return f"{hashlib.md5(term.lower().strip().encode()).hexdigest()[:12]}_{CLIP_SECONDS}"

    async def _run(self, cmd: List[str], timeout: float) -> Tuple[int, bytes, bytes]:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **self.process_kwargs
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except BaseException:
            await _kill_tree(proc)
            raise
        return proc.returncode, stdout, stderr

    def _remove_temps(self, video_id: str):
        for path in self.cache_dir.glob(f"temp_{video_id}.*"):
            _discard(path)

    def _sweep_stale_temps(self):
        cutoff = time.time() - STALE_TEMP_S
        for path in self.cache_dir.glob("temp_*"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue

    def _recently_failed(self, term: str) -> bool:
        failed_at = self.failed_searches.get(self._failed_key(term))
        return failed_at is not None and time.time() - failed_at < FAILED_SEARCH_RETRY_S

    def _mark_failed(self, term: str):
        now = time.time()
        key = self._failed_key(term)
        self.failed_searches[key] = now
        self.index["failed"][key] = now

    def _note_attempt(self, ok: bool):
        if ok:
            self.consecutive_failures = 0
            return
        self.consecutive_failures += 1
        if self.consecutive_failures >= settings.YOUTUBE_CLIPS_FAILURES_BEFORE_BACKOFF and not self.backing_off():
            self.backoff_until = time.time() + settings.YOUTUBE_CLIPS_BACKOFF_S
            log_service.warning(
                f"[YOUTUBE] {self.consecutive_failures} downloads failed in a row; pausing clip downloads for "
                f"{settings.YOUTUBE_CLIPS_BACKOFF_S // 60} min"
            )

    def backing_off(self) -> bool:
        return time.time() < self.backoff_until

    def queue_terms(self, terms: List[str]):
        added = False
        for term in terms:
            if term not in self.priority and not self._recently_failed(term):
                self.priority[term] = None
                added = True
        if added:
            self.work_event.set()

    async def wait_for_work(self, timeout: float):
        self.work_event.clear()
        if self.priority:
            return
        try:
            await asyncio.wait_for(self.work_event.wait(), timeout=max(1.0, timeout))
        except asyncio.TimeoutError:
            pass

    @staticmethod
    def track_terms(metadata: Dict[str, Any]) -> List[str]:
        gen_params = metadata.get("generation_params", {}) or {}
        derived_tags = metadata.get("derived_tags", {}) or {}
        keywords = gen_params.get("video_search_terms") or derived_tags.get("video_search_terms")
        if not keywords or not isinstance(keywords, list):
            return []
        duration_seconds = ((metadata.get("track_info", {}) or {}).get("duration") or 120000) / 1000
        wanted = max(4, min(15, int(duration_seconds / SECONDS_PER_CLIP)))
        terms = [keyword.strip() for keyword in keywords if isinstance(keyword, str) and keyword.strip()]
        return list(dict.fromkeys(terms))[:wanted]

    async def get_clips_for_track(self, track_id: str, listener: Optional[str], note_use: bool = True) -> Dict[str, Any]:
        metadata_path = settings.METADATA_DIR / f"{track_id}.json"
        if not metadata_path.exists():
            return {"clips": [], "keywords": [], "reason": "metadata_not_found"}

        try:
            async with aiofiles.open(metadata_path, 'r', encoding='utf-8') as f:
                metadata = json.loads(await f.read())
        except (json.JSONDecodeError, IOError) as e:
            log_service.error(f"[YOUTUBE] Failed to load metadata for {track_id}: {e}")
            return {"clips": [], "keywords": [], "reason": "metadata_load_error"}

        terms = self.track_terms(metadata)
        if not terms:
            return {"clips": [], "keywords": [], "reason": "no_video_search_terms"}
        if self.library is None:
            return {"clips": [], "keywords": terms, "reason": "library_loading"}

        await run_on_gpu_executor(prime_query_vectors, terms)
        vectors = self.library.term_vectors(terms, persist=False)
        song = (listener, track_id)
        kept = self.recent_picks.get(song, frozenset())
        picks, missing = await run_on_rank_executor(self._choose, terms, vectors, listener, note_use, kept)
        if note_use:
            self.recent_picks[song] = frozenset(file for _, file, _ in picks)
            self.recent_picks.move_to_end(song)
            while len(self.recent_picks) > RECENT_SONGS:
                self.recent_picks.popitem(last=False)

        used_at = _now_iso()
        for _, file, _ in picks:
            if file in self.index["clips"]:
                self.index["clips"][file]["used_at"] = used_at
                self.index_dirty = True
        self.queue_terms(missing)
        log_service.detail(f"[YOUTUBE] Track {track_id}: {len(picks)}/{len(terms)} clips from the library, "
                           f"{len(missing)} to download", "external")

        return {
            "clips": [
                {
                    "keyword": term,
                    "url": f"/api/video-clip-file/{file}",
                    "source_title": meta.get("source_title", ""),
                    "duration": CLIP_SECONDS
                }
                for term, file, meta in picks
            ],
            "keywords": terms,
            "requested_clips": len(terms),
            "pending": len(missing),
        }

    def _choose(self, terms: List[str], vectors: np.ndarray, listener: Optional[str], note_use: bool,
                kept: frozenset):
        from service_registry import services
        shotgun = services.tts_vector_db_service
        threshold = settings.YOUTUBE_CLIPS_MATCH_THRESHOLD
        now = time.time()
        sources: set = set()
        picks, missing = [], []
        for term, vector in zip(terms, vectors):
            matches = [match for match in self.library.rank(vector, WEIGHTS, PICK_DEPTH,
                                                             keep=lambda meta: meta.get("source_id") not in sources)
                       if match.similarity >= threshold]
            if not matches:
                missing.append(term)
                continue
            fresh = [match for match in matches
                     if shotgun is None or match.key in kept
                     or shotgun.is_fresh(listener, _shotgun_key(match.key), now)]
            pick = (fresh or matches)[0]
            sources.add(pick.meta.get("source_id") or pick.key)
            picks.append((term, pick.key, pick.meta))
        if shotgun is not None and note_use:
            for _, file, _ in picks:
                shotgun.note_used(listener, _shotgun_key(file), now)
        return picks, missing

    async def _needs_download(self, term: str) -> bool:
        if self.library is None or self._recently_failed(term):
            return False
        vectors = await run_on_gpu_executor(self.library.term_vectors, [term], True)
        return float(self.library.best_similarity(vectors)[0]) < settings.YOUTUBE_CLIPS_MATCH_THRESHOLD

    async def _importance_order(self, catalog_service) -> List[Dict[str, Any]]:
        from services.analytics_service import analytics_service
        tracks = dict(catalog_service.tracks)
        try:
            hits = await analytics_service.get_top_hits("all", limit=CHART_TRACKS)
        except Exception as e:
            log_service.warning(f"[YOUTUBE] Charts unavailable for the clip fill order: {e}")
            hits = []
        ranked = list(dict.fromkeys(hit.get("track_id") for hit in hits if hit.get("track_id") in tracks))
        charted = set(ranked)
        return [tracks[track_id] for track_id in ranked + [track_id for track_id in tracks if track_id not in charted]
                if isinstance(tracks[track_id], dict)]

    async def _fill_pass(self, catalog_service) -> List[str]:
        terms = list(dict.fromkeys(term for track in await self._importance_order(catalog_service)
                                   for term in self.track_terms(track) if not self._recently_failed(term)))
        threshold = settings.YOUTUBE_CLIPS_MATCH_THRESHOLD
        gaps = []
        for start in range(0, len(terms), EMBED_CHUNK):
            chunk = terms[start:start + EMBED_CHUNK]
            vectors = await run_on_gpu_executor(self.library.term_vectors, chunk, True)
            best = await asyncio.to_thread(self.library.best_similarity, vectors)
            gaps += [term for term, score in zip(chunk, best) if score < threshold]
        log_service.info(f"[YOUTUBE] Clip fill: {len(gaps)} of {len(terms)} catalog search terms have no clip "
                         f"close enough (>= {threshold})")
        return gaps

    async def _next_term(self, catalog_service) -> Optional[str]:
        while self.priority:
            term = next(iter(self.priority))
            self.priority.pop(term)
            if await self._needs_download(term):
                return term
        if self.cache_bytes >= self.max_cache_bytes:
            return None
        while True:
            while self.fill_queue:
                term = self.fill_queue.popleft()
                if await self._needs_download(term):
                    return term
            if time.time() < self.next_fill_pass:
                return None
            self.fill_queue.extend(await self._fill_pass(catalog_service))
            self.next_fill_pass = time.time() + FILL_PASS_IDLE_S
            if not self.fill_queue:
                return None

    async def run_downloads(self, catalog_service):
        gap = 3600 / max(1, settings.YOUTUBE_CLIPS_PER_HOUR)
        while True:
            try:
                if self.backing_off():
                    await self.wait_for_work(self.backoff_until - time.time())
                    continue
                term = await self._next_term(catalog_service)
                if term is None:
                    await self._housekeeping()
                    await self.wait_for_work(max(gap, min(FILL_PASS_IDLE_S, self.next_fill_pass - time.time())))
                    continue
                started = time.time()
                if await self.get_clip_for_keyword(term):
                    self.downloaded_since_report += 1
                await self._housekeeping()
                await asyncio.sleep(max(0.0, gap - (time.time() - started)))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log_service.error(f"[YOUTUBE] Clip downloader error: {e}")
                await asyncio.sleep(300)

    async def _housekeeping(self):
        self._sweep_stale_temps()
        if self.cache_bytes > self.max_cache_bytes:
            await self._evict_least_used()
        if self.index_dirty:
            await self._save_index()
        if self.downloaded_since_report and time.time() - self.last_report >= REPORT_EVERY_S:
            log_service.success(
                f"✓ Video clips: +{self.downloaded_since_report} downloaded "
                f"({len(self.index['clips'])} in the library, {self.cache_bytes / 1024 ** 3:.1f} GB; "
                f"{len(self.priority) + len(self.fill_queue)} search terms waiting)"
            )
            self.downloaded_since_report = 0
            self.last_report = time.time()

    async def _evict_least_used(self):
        clips = self.index["clips"]
        order = sorted(clips, key=lambda file: clips[file].get("used_at") or clips[file].get("cached_at") or "")
        removed = 0
        for file in order:
            if self.cache_bytes <= self.max_cache_bytes:
                break
            path = self.cache_dir / file
            size = path.stat().st_size if path.exists() else 0
            _discard(path)
            del clips[file]
            if self.library is not None:
                self.library.remove(file)
            self.cache_bytes -= size
            removed += 1
        self.index_dirty = True
        log_service.info(f"[YOUTUBE] Removed {removed} least recently used clips to stay under "
                         f"{settings.YOUTUBE_CLIPS_MAX_CACHE_GB} GB")

    async def search_videos(self, keyword: str, max_results: int = SEARCH_RESULTS) -> Optional[List[Dict[str, Any]]]:
        cmd = [
            self.ytdlp_path,
            *self.runtime_args,
            f"ytsearch{max_results}:{keyword}",
            "--dump-json",
            "--no-download",
            "--no-warnings",
            "--flat-playlist",
        ]

        try:
            returncode, stdout, stderr = await self._run(cmd, SEARCH_TIMEOUT_S)

            if returncode != 0:
                log_service.error(f"[YOUTUBE] Search failed for '{keyword}': {_one_line(stderr)}")
                return None

            results = []
            for line in stdout.decode('utf-8', 'replace').strip().split("\n"):
                if line:
                    try:
                        data = json.loads(line)
                        results.append({
                            "id": data.get("id"),
                            "title": data.get("title"),
                            "duration": data.get("duration"),
                            "url": f"https://www.youtube.com/watch?v={data.get('id')}"
                        })
                    except json.JSONDecodeError:
                        continue

            return [video for video in results
                    if MIN_SOURCE_SECONDS <= (video.get("duration") or 0) <= MAX_SOURCE_SECONDS]

        except asyncio.TimeoutError:
            log_service.error(f"[YOUTUBE] Search timed out for '{keyword}'")
            return None
        except Exception as e:
            log_service.error(f"[YOUTUBE] Search error for '{keyword}': {e}")
            return None

    async def download_clip(self, video_url: str, video_id: str, start_time: float, duration: float,
                            keyword_hash: str) -> Optional[Path]:
        output_path = self.cache_dir / f"{keyword_hash}_{video_id}_{int(duration)}s.mp4"
        if output_path.exists():
            return output_path

        temp_path = self.cache_dir / f"temp_{video_id}.mp4"

        try:
            self._remove_temps(video_id)
            dl_cmd = [
                self.ytdlp_path,
                *self.runtime_args,
                video_url,
                "-f", "18/bv*[height<=480][ext=mp4]/b[height<=480]",
                "--download-sections", f"*{start_time}-{start_time + duration}",
                "-o", str(temp_path),
                "--no-warnings",
                "--no-playlist",
                "--limit-rate", settings.YOUTUBE_CLIPS_RATE_LIMIT,
            ]

            returncode, _, stderr = await self._run(dl_cmd, DOWNLOAD_TIMEOUT_S)

            if returncode != 0:
                log_service.error(f"[YOUTUBE] Download failed for {video_id}: {_one_line(stderr)}")
                return None

            if not temp_path.exists():
                log_service.error(f"[YOUTUBE] Downloaded file not found for {video_id}")
                return None

            ffmpeg_cmd = [
                "ffmpeg",
                "-y",
                "-i", str(temp_path),
                "-t", str(duration),
                "-an",
                "-threads", "1",
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "28",
                "-pix_fmt", "yuv420p",
                "-force_key_frames", f"expr:gte(t,n_forced*{KEY_FRAME_S})",
                "-movflags", "+faststart",
                str(output_path)
            ]

            returncode, _, stderr = await self._run(ffmpeg_cmd, ENCODE_TIMEOUT_S)

            if returncode != 0:
                log_service.error(f"[YOUTUBE] FFmpeg failed for {video_id}: {_one_line(stderr, tail=True)}")
                _discard(output_path)
                return None

            return output_path if output_path.exists() else None

        except asyncio.TimeoutError:
            log_service.error(f"[YOUTUBE] Download timed out for {video_id}")
            _discard(output_path)
            return None
        except Exception as e:
            log_service.error(f"[YOUTUBE] Download error for {video_id}: {e}")
            return None
        finally:
            self._remove_temps(video_id)

    async def get_clip_for_keyword(self, term: str) -> Optional[Path]:
        if self.backing_off():
            return None
        videos = await self.search_videos(term)
        if videos is None:
            self._note_attempt(False)
            return None
        owned = {meta.get("source_id") for meta in self.index["clips"].values()}
        videos = [video for video in videos if video["id"] not in owned][:DOWNLOAD_TRIES]
        keyword_hash = self._failed_key(term).split("_")[0]

        for video in videos:
            if self.backing_off():
                return None
            start = min(START_OFFSET_S, max(0, (video.get("duration") or 0) - CLIP_SECONDS - 5))
            clip_path = await self.download_clip(video["url"], video["id"], start, CLIP_SECONDS, keyword_hash)
            self._note_attempt(clip_path is not None)
            if not clip_path:
                continue
            meta = {
                "keyword": term,
                "source_title": video["title"],
                "source_url": video["url"],
                "source_id": video["id"],
                "cached_at": _now_iso(),
            }
            self.index["clips"][clip_path.name] = meta
            self.cache_bytes += clip_path.stat().st_size
            await run_on_gpu_executor(self.library.add_meta, clip_path.name, dict(meta, file=clip_path.name))
            await self._save_index()
            return clip_path

        log_service.detail(f"[YOUTUBE] No usable video for '{term}'", "external")
        self._mark_failed(term)
        await self._save_index()
        return None


def _discard(path: Path):
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


async def _kill_tree(proc: asyncio.subprocess.Process):
    if proc.returncode is not None:
        return
    if sys.platform == "win32":
        killer = await asyncio.create_subprocess_exec(
            "taskkill", "/F", "/T", "/PID", str(proc.pid),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        await killer.wait()
    else:
        proc.kill()
    await proc.wait()


def _one_line(stderr: bytes, tail: bool = False, limit: int = 240) -> str:
    text = " ".join(stderr.decode("utf-8", "replace").split())
    return text[-limit:] if tail else text[:limit]


_youtube_service: Optional[YouTubeClipService] = None


def get_youtube_clip_service() -> YouTubeClipService:
    global _youtube_service
    if _youtube_service is None:
        _youtube_service = YouTubeClipService()
    return _youtube_service
