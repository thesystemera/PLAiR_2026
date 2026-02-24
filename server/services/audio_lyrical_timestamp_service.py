import asyncio
import json
import re
import aiofiles
import torch
import gc
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from difflib import SequenceMatcher
from services import log_service
from services.base_service import SingletonService
from config import settings

def _clear_cuda_cache():
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    gc.collect()

class LyricalTimestampService(SingletonService):
    def __init__(self):
        if getattr(self, '_initialized', False):
            return

        self.model = None
        self.model_name = "medium.en"
        self.model_loaded = False
        self.device = None
        self.lyric_timestamps_dir = None
        self.lock = asyncio.Lock()
        self._initialized = True

    async def _load_model_attempt(self, device: str, compute_type: str, download_root: Optional[str] = None):
        from faster_whisper import WhisperModel
        log_service.upscaling(f"Whisper: Loading {self.model_name} model...")
        log_service.upscaling(f"  Device: {device}, Compute: {compute_type}")

        self.model = await asyncio.to_thread(
            WhisperModel,
            self.model_name,
            device=device,
            compute_type=compute_type,
            download_root=download_root
        )
        self.device = device
        self.model_loaded = True
        log_service.upscaling(f"Whisper model loaded and ready ({self.model_name})")

    async def initialize(self):
        if self.model_loaded:
            log_service.upscaling("Whisper already loaded")
            return

        self.lyric_timestamps_dir = settings.LYRIC_TIMESTAMPS_DIR

        if torch.cuda.is_available():
            log_service.upscaling(f"GPU: {torch.cuda.get_device_name(0)}")

        try:
            await self._load_model_attempt(device="cuda", compute_type="float16")
        except Exception as e:
            log_service.error(f"Whisper GPU load failed: {str(e)}")
            log_service.upscaling("Falling back to CPU...")
            try:
                await self._load_model_attempt(device="cpu", compute_type="int8")
            except Exception as cpu_error:
                log_service.error(f"Whisper CPU load failed: {str(cpu_error)}")
                self.model_loaded = False

    @staticmethod
    def _parse_lyrics_from_metadata(metadata: Dict[str, Any]) -> Tuple[str, List[Dict[str, Any]]]:
        try:
            prompt = metadata.get("generation_params", {}).get("prompt", "")

            if not prompt:
                return "", []

            sections = []
            section_pattern = r'\[(Intro|Verse|Chorus|Bridge|Outro|Breakdown|Instrumental|Pre-Chorus)[^]]*?\]'

            for match in re.finditer(section_pattern, prompt, re.IGNORECASE):
                sections.append({
                    "type": match.group(1).lower(),
                    "position": match.start(),
                    "marker": match.group(0)
                })

            clean_text = re.sub(r'\[.*?]', '', prompt)
            clean_text = re.sub(r'\(.*?\)', '', clean_text)
            clean_text = re.sub(r'\n\s*\n', '\n', clean_text)
            clean_text = clean_text.strip()

            return clean_text, sections

        except Exception as e:
            log_service.warning(f"Failed to parse lyrics from metadata: {e}")
            return "", []

    @staticmethod
    def _normalize_text(text: str) -> str:
        text = text.lower()
        text = re.sub(r'[^\w\s]', '', text)
        text = re.sub(r'\s+', ' ', text)
        return text.strip()

    def _align_lyrics(
        self,
        whisper_words: List[Dict[str, Any]],
        ground_truth_lyrics: str
    ) -> List[Dict[str, Any]]:
        if not whisper_words or not ground_truth_lyrics:
            return []

        whisper_text = " ".join([w["word"] for w in whisper_words])
        whisper_normalized = self._normalize_text(whisper_text)
        ground_truth_normalized = self._normalize_text(ground_truth_lyrics)

        ground_truth_words = ground_truth_normalized.split()
        whisper_normalized_words = whisper_normalized.split()

        matcher = SequenceMatcher(None, whisper_normalized_words, ground_truth_words)
        opcodes = matcher.get_opcodes()

        aligned_words = []
        whisper_idx = 0
        ground_truth_idx = 0

        for tag, i1, i2, j1, j2 in opcodes:
            if tag == 'equal':
                for i in range(i1, i2):
                    if whisper_idx < len(whisper_words):
                        word_data = whisper_words[whisper_idx].copy()
                        word_data["word"] = ground_truth_words[ground_truth_idx]
                        aligned_words.append(word_data)
                        whisper_idx += 1
                        ground_truth_idx += 1

            elif tag == 'replace':
                whisper_count = i2 - i1
                ground_truth_count = j2 - j1

                if whisper_count == ground_truth_count:
                    for i in range(whisper_count):
                        if whisper_idx < len(whisper_words):
                            word_data = whisper_words[whisper_idx].copy()
                            word_data["word"] = ground_truth_words[ground_truth_idx]
                            word_data["aligned"] = True
                            aligned_words.append(word_data)
                            whisper_idx += 1
                            ground_truth_idx += 1
                else:
                    if whisper_idx < len(whisper_words):
                        start_time = whisper_words[whisper_idx]["start"]
                        end_idx = min(whisper_idx + whisper_count - 1, len(whisper_words) - 1)
                        end_time = whisper_words[end_idx]["end"]
                        duration = (end_time - start_time) / ground_truth_count if ground_truth_count > 0 else 0.5

                        for i in range(ground_truth_count):
                            aligned_words.append({
                                "word": ground_truth_words[ground_truth_idx],
                                "start": start_time + (i * duration),
                                "end": start_time + ((i + 1) * duration),
                                "confidence": 0.5,
                                "aligned": True
                            })
                            ground_truth_idx += 1

                    whisper_idx += whisper_count

            elif tag == 'delete':
                whisper_idx += (i2 - i1)

            elif tag == 'insert':
                if 0 < whisper_idx < len(whisper_words):
                    prev_end = whisper_words[whisper_idx - 1]["end"]
                    next_start = whisper_words[whisper_idx]["start"]
                    gap_duration = (next_start - prev_end) / (j2 - j1) if (j2 - j1) > 0 else 0.3

                    for i in range(j1, j2):
                        aligned_words.append({
                            "word": ground_truth_words[ground_truth_idx],
                            "start": prev_end + ((i - j1) * gap_duration),
                            "end": prev_end + ((i - j1 + 1) * gap_duration),
                            "confidence": 0.3,
                            "aligned": True
                        })
                        ground_truth_idx += 1
                else:
                    ground_truth_idx += (j2 - j1)

        return aligned_words

    def _group_words_into_lines(
        self,
        words: List[Dict[str, Any]],
        ground_truth_lyrics: str,
        max_line_duration: float = 8.0
    ) -> List[Dict[str, Any]]:
        if not words:
            return []

        original_lines = [line.strip() for line in ground_truth_lyrics.split('\n') if line.strip()]

        lines = []
        current_line_words = []
        accumulated_text = []

        for word in words:
            current_line_words.append(word)
            accumulated_text.append(word["word"])

            current_text = " ".join(accumulated_text)
            current_normalized = self._normalize_text(current_text)

            for orig_line in original_lines:
                orig_normalized = self._normalize_text(orig_line)
                if current_normalized == orig_normalized:
                    lines.append({
                        "line": orig_line,
                        "start": current_line_words[0]["start"],
                        "end": current_line_words[-1]["end"],
                        "words": current_line_words
                    })
                    current_line_words = []
                    accumulated_text = []
                    break

            if current_line_words:
                duration = current_line_words[-1]["end"] - current_line_words[0]["start"]
                if duration > max_line_duration:
                    line_text = " ".join([w["word"] for w in current_line_words])
                    lines.append({
                        "line": line_text,
                        "start": current_line_words[0]["start"],
                        "end": current_line_words[-1]["end"],
                        "words": current_line_words
                    })
                    current_line_words = []
                    accumulated_text = []

        if current_line_words:
            line_text = " ".join([w["word"] for w in current_line_words])
            lines.append({
                "line": line_text,
                "start": current_line_words[0]["start"],
                "end": current_line_words[-1]["end"],
                "words": current_line_words
            })

        return lines

    def _create_placeholder_timestamps(
        self,
        track_id: str,
        sections: List[Dict[str, Any]],
        is_instrumental: bool
    ) -> Dict[str, Any]:
        return {
            "id": track_id,
            "duration": 0.0,
            "sync_version": "1.0",
            "model": self.model_name,
            "confidence": 0.0,
            "alignment_score": 0.0,
            "word_count": 0,
            "line_count": 0,
            "lyrics": [],
            "raw_words": [],
            "sections": sections,
            "instrumental": is_instrumental
        }

    def _transcribe_sync(self, audio_path: Path) -> Tuple[Any, Any]:
        segments, info = self.model.transcribe(
            str(audio_path),
            language="en",
            word_timestamps=True,
            beam_size=5,
            temperature=0.0,
            vad_filter=False,
            condition_on_previous_text=False
        )
        return list(segments), info

    async def generate_timestamps(
        self,
        track_id: str,
        metadata: Dict[str, Any],
        audio_path: Optional[Path] = None,
        save: bool = True
    ) -> Optional[Dict[str, Any]]:
        async with self.lock:
            if not self.model_loaded:
                log_service.error("Whisper model not loaded")
                return None

            try:
                is_instrumental = metadata.get("generation_params", {}).get("instrumental", False)
                ground_truth_lyrics, sections = self._parse_lyrics_from_metadata(metadata)

                if is_instrumental or not ground_truth_lyrics:
                    status_msg = "instrumental track" if is_instrumental else "no lyrics found"
                    log_service.upscaling(f"Whisper: Skipping {track_id[:8]} ({status_msg})")
                    placeholder = self._create_placeholder_timestamps(track_id, sections, is_instrumental)
                    if save:
                        await self._save_timestamps(placeholder, track_id)
                    return placeholder

                if not audio_path or not await asyncio.to_thread(audio_path.exists):
                    log_service.error(f"Whisper: Audio not found for {track_id[:8]}")
                    return None

                log_service.upscaling(f"Whisper: Processing {audio_path.name}")
                log_service.upscaling(f"  Ground truth: {len(ground_truth_lyrics)} chars")

                segments, info = await asyncio.to_thread(self._transcribe_sync, audio_path)

                _clear_cuda_cache()

                whisper_words = []
                for segment in segments:
                    if hasattr(segment, 'words') and segment.words:
                        for word in segment.words:
                            whisper_words.append({
                                "word": word.word.strip(),
                                "start": round(word.start, 3),
                                "end": round(word.end, 3),
                                "confidence": round(word.probability, 3) if hasattr(word, 'probability') else 0.9
                            })

                if not whisper_words:
                    log_service.upscaling(f"Whisper: No words detected for {track_id[:8]}")
                    _clear_cuda_cache()
                    return None

                log_service.upscaling(f"  Detected {len(whisper_words)} words, aligning...")
                aligned_words = self._align_lyrics(whisper_words, ground_truth_lyrics)

                if not aligned_words:
                    log_service.warning("Whisper: Alignment failed, using raw output")
                    aligned_words = whisper_words

                lines = self._group_words_into_lines(aligned_words, ground_truth_lyrics)

                total_confidence = sum([w.get("confidence", 0.5) for w in aligned_words])
                avg_confidence = total_confidence / len(aligned_words) if aligned_words else 0.0

                aligned_count = sum([1 for w in aligned_words if w.get("aligned", False)])
                alignment_score = 1.0 - (aligned_count / len(aligned_words)) if aligned_words else 0.0

                result = {
                    "id": track_id,
                    "duration": round(info.duration, 3),
                    "sync_version": "1.0",
                    "model": self.model_name,
                    "confidence": round(avg_confidence, 3),
                    "alignment_score": round(alignment_score, 3),
                    "word_count": len(aligned_words),
                    "line_count": len(lines),
                    "lyrics": lines,
                    "raw_words": aligned_words,
                    "sections": sections
                }

                if save:
                    await self._save_timestamps(result, track_id)

                log_service.upscaling(
                    f"Whisper complete: {len(lines)} lines, {len(aligned_words)} words, "
                    f"confidence: {avg_confidence:.0%}"
                )

                _clear_cuda_cache()
                return result

            except Exception as e:
                log_service.error(f"Whisper failed for {track_id[:8]}: {str(e)}")
                _clear_cuda_cache()
                return None

    async def _save_timestamps(self, timestamps: Dict[str, Any], track_id: str) -> Path:
        filepath = self.lyric_timestamps_dir / f"{track_id}.json"

        try:
            async with aiofiles.open(filepath, 'w') as f:
                await f.write(json.dumps(timestamps, indent=2))
            return filepath

        except Exception as e:
            log_service.error(f"Whisper: Save failed: {e}")
            raise

    async def load_timestamps(self, track_id: str) -> Optional[Dict[str, Any]]:
        filepath = self.lyric_timestamps_dir / f"{track_id}.json"

        try:
            async with aiofiles.open(filepath, 'r') as f:
                content = await f.read()
                return json.loads(content)

        except FileNotFoundError:
            return None
        except Exception as e:
            log_service.error(f"Whisper: Load failed: {e}")
            return None

    async def unload(self):
        if self.model is not None:
            del self.model
            self.model = None

        _clear_cuda_cache()
        self.model_loaded = False
        log_service.upscaling("Whisper model unloaded")