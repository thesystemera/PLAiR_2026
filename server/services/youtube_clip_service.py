import asyncio
import hashlib
import json
import shutil
import sys
import aiofiles
from pathlib import Path
from typing import Optional
from datetime import datetime, timedelta

from config.settings import settings
from services import log_service

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

class YouTubeClipService:

    def __init__(self):
        self.cache_dir: Path = settings.YOUTUBE_CLIPS_DIR
        self.index_path = self.cache_dir / "clip_index.json"
        self.index = self._load_index()

        self.ytdlp_path = shutil.which("yt-dlp") or str(Path(sys.executable).parent / "yt-dlp.exe")

        self.default_clip_duration = 30
        self.max_video_age_days = 30
        self.cache_expiry_days = 7

    def _load_index(self) -> dict:
        if self.index_path.exists():
            try:
                return json.loads(self.index_path.read_text())
            except Exception as e:
                log_service.warning(f"[YOUTUBE] Failed to load index: {e}")
        return {"clips": {}, "searches": {}}

    def _refresh_index(self):
        self.index = self._load_index()

    def _save_index(self):
        try:
            self.index_path.write_text(json.dumps(self.index, indent=2))
        except Exception as e:
            log_service.error(f"[YOUTUBE] Failed to save index: {e}")

    def _keyword_hash(self, keyword: str) -> str:
        normalized = keyword.lower().strip()
        return hashlib.md5(normalized.encode()).hexdigest()[:12]

    def _is_cache_valid(self, cache_entry: dict) -> bool:
        if not cache_entry:
            return False
        cached_at = datetime.fromisoformat(cache_entry.get("cached_at", "2000-01-01"))
        return datetime.now() - cached_at < timedelta(days=self.cache_expiry_days)

    async def search_videos(
        self,
        keyword: str,
        max_results: int = 5
    ) -> list[dict]:

        search_query = f"{keyword} news"

        cmd = [
            self.ytdlp_path,
            f"ytsearch{max_results}:{search_query}",
            "--dump-json",
            "--no-download",
            "--no-warnings",
            "--flat-playlist",
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

            if proc.returncode != 0:
                log_service.error(f"[YOUTUBE] Search failed for '{keyword}': {stderr.decode()[:200]}")
                return []

            results = []
            for line in stdout.decode().strip().split("\n"):
                if line:
                    try:
                        data = json.loads(line)
                        results.append({
                            "id": data.get("id"),
                            "title": data.get("title"),
                            "duration": data.get("duration"),
                            "upload_date": data.get("upload_date"),
                            "url": f"https://www.youtube.com/watch?v={data.get('id')}"
                        })
                    except json.JSONDecodeError:
                        continue

            filtered = [v for v in results if 30 <= (v.get("duration") or 0) <= 600]

            return filtered[:max_results]

        except asyncio.TimeoutError:
            log_service.error(f"[YOUTUBE] Search timed out for '{keyword}'")
            return []
        except Exception as e:
            log_service.error(f"[YOUTUBE] Search error for '{keyword}': {e}")
            return []

    async def download_clip(
        self,
        video_url: str,
        video_id: str,
        start_time: float = 0,
        duration: float = 30,
        keyword_hash: str = ""
    ) -> Optional[Path]:

        filename = f"{keyword_hash}_{video_id}_{int(duration)}s.mp4"
        output_path = self.cache_dir / filename

        if output_path.exists():
            return output_path

        log_service.info(f"[YOUTUBE] Downloading {duration}s clip from {video_id} @ {start_time}s")

        temp_path = self.cache_dir / f"temp_{video_id}.mp4"

        try:
            dl_cmd = [
                self.ytdlp_path,
                video_url,
                "-f", "18",
                "-o", str(temp_path),
                "--no-warnings",
                "--no-playlist",
            ]

            proc = await asyncio.create_subprocess_exec(
                *dl_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)

            if proc.returncode != 0:
                log_service.error(f"[YOUTUBE] Download failed for {video_id}: {stderr.decode()[:200]}")
                return None

            possible_temps = list(self.cache_dir.glob(f"temp_{video_id}*"))
            if not possible_temps:
                log_service.error(f"[YOUTUBE] Downloaded file not found for {video_id}")
                return None
            actual_temp = possible_temps[0]

            ffmpeg_cmd = [
                "ffmpeg",
                "-y",
                "-ss", str(start_time),
                "-i", str(actual_temp),
                "-t", str(duration),
                "-an",
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "28",
                str(output_path)
            ]

            proc = await asyncio.create_subprocess_exec(
                *ffmpeg_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)

            if actual_temp.exists():
                actual_temp.unlink()

            if proc.returncode != 0:
                log_service.error(f"[YOUTUBE] FFmpeg failed for {video_id}: {stderr.decode()[-300:]}")
                return None

            if output_path.exists():
                size_mb = output_path.stat().st_size / (1024 * 1024)
                log_service.info(f"[YOUTUBE] Cached: {output_path.name} ({size_mb:.1f}MB)")
                return output_path

            return None

        except asyncio.TimeoutError:
            log_service.error(f"[YOUTUBE] Download timed out for {video_id}")
            if temp_path.exists():
                temp_path.unlink()
            return None
        except Exception as e:
            log_service.error(f"[YOUTUBE] Download error for {video_id}: {e}")
            return None

    async def get_clip_for_keyword(
        self,
        keyword: str,
        clip_duration: float = 30,
        start_offset: float = 10  # skip intros
    ) -> Optional[dict]:

        self._refresh_index()

        kw_hash = self._keyword_hash(keyword)
        cache_key = f"{kw_hash}_{int(clip_duration)}"

        if cache_key in self.index["clips"]:
            entry = self.index["clips"][cache_key]
            if self._is_cache_valid(entry):
                clip_path = Path(entry["path"])
                if clip_path.exists():
                    return {
                        "path": clip_path,
                        "keyword": keyword,
                        "source_title": entry.get("source_title"),
                        "source_url": entry.get("source_url"),
                        "duration": clip_duration
                    }

        videos = await self.search_videos(keyword, max_results=3)
        if not videos:
            log_service.warning(f"[YOUTUBE] No videos found for '{keyword}'")
            return None

        for video in videos:
            video_duration = video.get("duration", 0)

            max_start = max(0, video_duration - clip_duration - 5)
            actual_start = min(start_offset, max_start)

            clip_path = await self.download_clip(
                video_url=video["url"],
                video_id=video["id"],
                start_time=actual_start,
                duration=clip_duration,
                keyword_hash=kw_hash
            )

            if clip_path:
                self.index["clips"][cache_key] = {
                    "path": str(clip_path),
                    "keyword": keyword,
                    "source_title": video["title"],
                    "source_url": video["url"],
                    "source_id": video["id"],
                    "cached_at": datetime.now().isoformat()
                }
                self._save_index()

                return {
                    "path": clip_path,
                    "keyword": keyword,
                    "source_title": video["title"],
                    "source_url": video["url"],
                    "duration": clip_duration
                }

        return None

    async def get_clips_for_theme(
        self,
        keywords: list[str],
        max_clips: int = 8,
        clip_duration: float = 30
    ) -> list[dict]:

        log_service.info(f"[YOUTUBE] Fetching clips for {len(keywords)} keywords, max {max_clips}")

        clips = []
        for keyword in keywords[:max_clips]:
            clip = await self.get_clip_for_keyword(
                keyword=keyword,
                clip_duration=clip_duration
            )
            if clip:
                clips.append(clip)

        log_service.info(f"[YOUTUBE] Retrieved {len(clips)}/{max_clips} clips")
        return clips

    async def get_clips_for_track(self, track_id: str) -> dict:

        metadata_path = settings.METADATA_DIR / f"{track_id}.json"
        if not metadata_path.exists():
            return {"clips": [], "keywords": [], "reason": "metadata_not_found"}

        try:
            async with aiofiles.open(metadata_path, 'r', encoding='utf-8') as f:
                content = await f.read()
                metadata = json.loads(content)
        except (json.JSONDecodeError, IOError) as e:
            log_service.error(f"[YOUTUBE] Failed to load metadata for {track_id}: {e}")
            return {"clips": [], "keywords": [], "reason": "metadata_load_error"}

        gen_params = metadata.get("generation_params", {})
        derived_tags = metadata.get("derived_tags", {})
        track_info = metadata.get("track_info", {})

        keywords = gen_params.get("video_search_terms") or derived_tags.get("video_search_terms")

        if not keywords or not isinstance(keywords, list) or len(keywords) == 0:
            return {"clips": [], "keywords": [], "reason": "no_video_search_terms"}

        duration_ms = track_info.get("duration", 120000)
        duration_seconds = duration_ms / 1000
        clips_needed = int(duration_seconds / 15)
        max_clips = max(4, min(15, clips_needed))

        log_service.info(f"[YOUTUBE] Track {track_id}: {duration_seconds:.0f}s -> requesting {max_clips} clips from {len(keywords)} keywords")

        clips = await self.get_clips_for_theme(
            keywords=keywords,
            max_clips=max_clips,
            clip_duration=30
        )

        def get_clip_filename(clip_path):
            if hasattr(clip_path, 'name'):
                return clip_path.name
            return str(clip_path).replace('\\', '/').split('/')[-1]

        return {
            "clips": [
                {
                    "keyword": clip["keyword"],
                    "url": f"/api/video-clip-file/{get_clip_filename(clip['path'])}",
                    "source_title": clip.get("source_title", ""),
                    "duration": clip.get("duration", 6)
                }
                for clip in clips
            ],
            "keywords": keywords,
            "requested_clips": max_clips,
            "track_duration_seconds": duration_seconds
        }

    async def pre_download_for_track(self, metadata: dict, clip_duration: float = 30):

        gen_params = metadata.get("generation_params", {})
        derived_tags = metadata.get("derived_tags", {})
        track_info = metadata.get("track_info", {})
        track_id = metadata.get("id", "unknown")

        keywords = gen_params.get("video_search_terms") or derived_tags.get("video_search_terms")
        if not keywords:
            return 0

        duration_ms = track_info.get("duration", 120000)
        duration_seconds = duration_ms / 1000
        max_clips = max(4, min(15, int(duration_seconds / 15)))

        self._refresh_index()
        cached_count = 0
        for keyword in keywords[:max_clips]:
            kw_hash = self._keyword_hash(keyword)
            cache_key = f"{kw_hash}_{int(clip_duration)}"
            if cache_key in self.index["clips"]:
                entry = self.index["clips"][cache_key]
                if self._is_cache_valid(entry) and Path(entry["path"]).exists():
                    cached_count += 1

        needed = max_clips - cached_count
        if needed <= 0:
            return cached_count

        log_service.info(f"[YOUTUBE] Pre-downloading {needed} clips for {track_id} ({cached_count} already cached)")

        clips = await self.get_clips_for_theme(
            keywords=keywords,
            max_clips=max_clips,
            clip_duration=clip_duration
        )

        return len(clips)

    async def cleanup_old_clips(self, max_age_days: int = 14):
        cutoff = datetime.now() - timedelta(days=max_age_days)
        removed = 0

        for cache_key, entry in list(self.index["clips"].items()):
            cached_at = datetime.fromisoformat(entry.get("cached_at", "2000-01-01"))
            if cached_at < cutoff:
                clip_path = Path(entry["path"])
                if clip_path.exists():
                    clip_path.unlink()
                    removed += 1
                del self.index["clips"][cache_key]

        if removed:
            self._save_index()
            log_service.info(f"[YOUTUBE] Cleaned up {removed} old clips")

    def get_cache_stats(self) -> dict:
        total_size = sum(
            Path(e["path"]).stat().st_size
            for e in self.index["clips"].values()
            if Path(e["path"]).exists()
        )
        return {
            "clip_count": len(self.index["clips"]),
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "cache_dir": str(self.cache_dir)
        }

_youtube_service: Optional[YouTubeClipService] = None

def get_youtube_clip_service() -> YouTubeClipService:

    global _youtube_service
    if _youtube_service is None:
        _youtube_service = YouTubeClipService()
    return _youtube_service