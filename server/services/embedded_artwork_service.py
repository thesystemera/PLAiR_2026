import asyncio
from pathlib import Path
from typing import Optional, Tuple
from io import BytesIO
from PIL import Image
from services import log_service
from services.base_service import SingletonService
from config import settings

class EmbeddedArtworkService(SingletonService):

    def __init__(self):
        super().__init__()
        if getattr(self, '_initialized', False):
            return

        self.supported_formats = {'.mp3', '.flac', '.m4a', '.aac', '.ogg', '.opus', '.wav'}
        self.artwork_dir = settings.ARTWORK_DIR
        self._initialized = True

    async def initialize(self):
        log_service.system("✓ EmbeddedArtworkService initialized")

    async def extract_artwork(
        self,
        audio_path: Path,
        output_path: Optional[Path] = None
    ) -> Tuple[Optional[Path], Optional[str]]:

        if not audio_path.exists():
            log_service.warning(f"[EmbeddedArtwork] File not found: {audio_path}")
            return None, None

        suffix = audio_path.suffix.lower()
        if suffix not in self.supported_formats:
            log_service.debug(f"[EmbeddedArtwork] Unsupported format: {suffix}")
            return None, None

        try:
            artwork_data, mime_type = await asyncio.to_thread(
                self._extract_sync, audio_path
            )

            if not artwork_data:
                log_service.debug(f"[EmbeddedArtwork] No embedded artwork in: {audio_path.name}")
                return None, None

            if output_path is None:
                output_path = self.artwork_dir / f"{audio_path.stem}.jpeg"

            saved_path = await asyncio.to_thread(
                self._save_artwork, artwork_data, output_path
            )

            if saved_path:
                log_service.success(f"[EmbeddedArtwork] Extracted: {audio_path.name} -> {saved_path.name}")
                return saved_path, mime_type

            return None, None

        except Exception as e:
            log_service.error(f"[EmbeddedArtwork] Extraction failed: {e}")
            return None, None

    def _extract_sync(self, audio_path: Path) -> Tuple[Optional[bytes], Optional[str]]:

        suffix = audio_path.suffix.lower()

        try:
            if suffix == '.mp3':
                return self._extract_from_mp3(audio_path)
            elif suffix == '.flac':
                return self._extract_from_flac(audio_path)
            elif suffix in ['.m4a', '.aac']:
                return self._extract_from_m4a(audio_path)
            elif suffix in ['.ogg', '.opus']:
                return self._extract_from_ogg(audio_path)
            elif suffix == '.wav':
                return self._extract_from_wav(audio_path)
            else:
                return None, None

        except Exception as e:
            log_service.debug(f"[EmbeddedArtwork] Format-specific extraction failed: {e}")
            return None, None

    def _extract_from_mp3(self, audio_path: Path) -> Tuple[Optional[bytes], Optional[str]]:
        try:
            from mutagen.id3 import ID3
            from mutagen._util import MutagenError  # type: ignore

            try:
                audio = ID3(str(audio_path))
            except MutagenError:
                return None, None

            for key in audio.keys():
                if key.startswith('APIC'):
                    frame = audio[key]
                    return frame.data, frame.mime

            return None, None

        except ImportError:
            log_service.error("[EmbeddedArtwork] mutagen not available")
            return None, None

    def _extract_from_flac(self, audio_path: Path) -> Tuple[Optional[bytes], Optional[str]]:
        try:
            from mutagen.flac import FLAC

            audio = FLAC(str(audio_path))

            if audio.pictures:
                for pic in audio.pictures:
                    if pic.type == 3:  # Front cover
                        return pic.data, pic.mime

                return audio.pictures[0].data, audio.pictures[0].mime

            return None, None

        except Exception:
            return None, None

    def _extract_from_m4a(self, audio_path: Path) -> Tuple[Optional[bytes], Optional[str]]:
        try:
            from mutagen.mp4 import MP4

            audio = MP4(str(audio_path))

            if audio.tags is not None and 'covr' in audio.tags:  # type: ignore
                covers = audio.tags['covr']  # type: ignore
                if covers:
                    cover = covers[0]
                    mime = 'image/jpeg' if cover.imageformat == 13 else 'image/png'
                    return bytes(cover), mime

            return None, None

        except Exception:
            return None, None

    def _extract_from_ogg(self, audio_path: Path) -> Tuple[Optional[bytes], Optional[str]]:
        try:
            from mutagen.oggvorbis import OggVorbis
            from mutagen.oggopus import OggOpus
            from mutagen.flac import Picture
            import base64

            suffix = audio_path.suffix.lower()

            if suffix == '.ogg':
                audio = OggVorbis(str(audio_path))
            else:  # .opus
                audio = OggOpus(str(audio_path))

            metadata_pics = audio.get('metadata_block_picture', []) or []
            for b64_data in metadata_pics:
                try:
                    data = base64.b64decode(b64_data)
                    picture = Picture(data)
                    return picture.data, picture.mime
                except Exception:
                    continue

            return None, None

        except Exception:
            return None, None

    def _extract_from_wav(self, audio_path: Path) -> Tuple[Optional[bytes], Optional[str]]:
        try:
            from mutagen.id3 import ID3
            from mutagen._util import MutagenError  # type: ignore

            try:
                audio = ID3(str(audio_path))
                for key in audio.keys():
                    if key.startswith('APIC'):
                        frame = audio[key]
                        return frame.data, frame.mime
            except MutagenError:
                pass

            return None, None

        except Exception:
            return None, None

    def _save_artwork(self, artwork_data: bytes, output_path: Path) -> Optional[Path]:
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)

            image = Image.open(BytesIO(artwork_data))

            if image.mode in ('RGBA', 'P'):
                background = Image.new('RGB', image.size, (255, 255, 255))
                if image.mode == 'P':
                    image = image.convert('RGBA')
                background.paste(image, mask=image.split()[-1])
                image = background
            elif image.mode != 'RGB':
                image = image.convert('RGB')

            if output_path.suffix.lower() not in ['.jpg', '.jpeg']:
                output_path = output_path.with_suffix('.jpeg')

            image.save(str(output_path), 'JPEG', quality=90, optimize=True)

            return output_path

        except Exception as e:
            log_service.error(f"[EmbeddedArtwork] Failed to save: {e}")
            return None

    async def extract_and_save_for_track(
        self,
        audio_path: Path,
        track_id: str
    ) -> Tuple[bool, Optional[Path]]:

        output_path = self.artwork_dir / f"{track_id}.jpeg"

        if output_path.exists():
            log_service.debug(f"[EmbeddedArtwork] Artwork already exists: {track_id}")
            return True, output_path

        saved_path, _ = await self.extract_artwork(audio_path, output_path)

        if saved_path:
            return True, saved_path
        else:
            return False, None

embedded_artwork_service = EmbeddedArtworkService()
