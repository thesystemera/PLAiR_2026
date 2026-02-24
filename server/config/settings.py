import os
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent.parent

class Settings:
    # PostgreSQL connection settings
    POSTGRES_USER: str = os.getenv("POSTGRES_USER", "postgres")
    POSTGRES_PASSWORD: str = os.getenv("POSTGRES_PASSWORD", "")
    POSTGRES_HOST: str = os.getenv("POSTGRES_HOST", "localhost")
    POSTGRES_PORT: str = os.getenv("POSTGRES_PORT", "5432")

    # Main user database
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/ai_radio"
    )

    # Catalog database (tracks, metadata)
    CATALOG_DATABASE_URL: str = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/ai_radio_catalog"

    # User content database (shoutouts, opinions)
    USER_CONTENT_DATABASE_URL: str = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/ai_radio_user_content"

    # Embeddings database (vector caches, query caches, TTS embeddings)
    EMBEDDINGS_DATABASE_URL: str = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/ai_radio_embeddings"

    CATALOG_DIR: Path = BASE_DIR / "server" / "catalog"
    METADATA_DIR: Path = CATALOG_DIR / "metadata"
    AUDIO_DIR: Path = CATALOG_DIR / "mp3"
    ARTWORK_DIR: Path = CATALOG_DIR / "artwork"
    ARTWORK_ENRICHED_DIR: Path = CATALOG_DIR / "artwork_enriched"
    WAV_DIR: Path = CATALOG_DIR / "apollo_wav"
    UPSCALED_WAV_DIR: Path = CATALOG_DIR / "audiosr_wav"
    SONIC_WAV_DIR: Path = CATALOG_DIR / "sonic_wav"
    ENHANCED_WAV_DIR: Path = CATALOG_DIR / "master_wav"
    AUDIOFEATURES_DIR: Path = CATALOG_DIR / "audiofeatures"
    LYRIC_TIMESTAMPS_DIR: Path = CATALOG_DIR / "lyric_timestamps"
    OPUS_128K_DIR: Path = CATALOG_DIR / "opus_128k"
    OPUS_192K_DIR: Path = CATALOG_DIR / "opus_192k"
    OPUS_256K_DIR: Path = CATALOG_DIR / "opus_256k"

    DEMUCS_STEMS_DIR: Path = CATALOG_DIR / "demucs_stems"
    VOCAL_ENHANCED_WAV_DIR: Path = CATALOG_DIR / "vocal_enhanced_wav"
    ANALYTICS_DIR: Path = CATALOG_DIR / "analytics"
    FAILED_PROMPTS_DIR: Path = BASE_DIR / "data" / "failed_prompts"
    LOGS_DIR: Path = BASE_DIR / "data" / "logs"
    USERS_DIR: Path = CATALOG_DIR / "users"

    @staticmethod
    def get_user_dir(user_id: int) -> Path:
        """Get the root directory for a specific user (ensures it exists)"""
        path = Settings.USERS_DIR / str(user_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def get_user_shoutouts_dir(user_id: int) -> Path:
        """Get shoutouts directory for a specific user (ensures it exists)"""
        path = Settings.USERS_DIR / str(user_id) / "shoutouts"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def get_user_uploads_dir(user_id: int) -> Path:
        """Get uploads directory for a specific user (ensures it exists)"""
        path = Settings.USERS_DIR / str(user_id) / "uploads"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def get_user_profile_picture_path(user_id: int, filename: str) -> Path:
        """Get profile picture path for a specific user (ensures parent dir exists)"""
        user_dir = Settings.USERS_DIR / str(user_id)
        user_dir.mkdir(parents=True, exist_ok=True)
        return user_dir / filename

    USER_CONTENT_DIR: Path = BASE_DIR / "data" / "user_content"
    SHOUTOUTS_DIR: Path = USERS_DIR
    OPINIONS_DIR: Path = USER_CONTENT_DIR / "opinions"
    UPLOADS_DIR: Path = USERS_DIR

    TTS_ENGINE_DATA_DIR: Path = BASE_DIR / "data" / "tts_dj_engine_data"
    TTS_AUDIO_DIR: Path = TTS_ENGINE_DATA_DIR / "tts_audio"
    META_AUDIO_DIR: Path = TTS_ENGINE_DATA_DIR / "meta_audio"
    IMPULSE_AUDIO_DIR: Path = TTS_ENGINE_DATA_DIR / "impulse_audio"
    BREATH_AUDIO_DIR: Path = TTS_ENGINE_DATA_DIR / "breath_audio"
    AUDIO_EFFECT_DIR: Path = TTS_ENGINE_DATA_DIR / "audio_effect_audio"
    STUDIO_AUDIO_DIR: Path = TTS_ENGINE_DATA_DIR / "studio_audio"

    # All vector databases and embeddings stored here
    EMBEDDINGS_DIR: Path = BASE_DIR / "data" / "embeddings"
    
    # Catalog Annoy indexes stored here (same as embeddings for now)
    CATALOG_EMBEDDINGS_DIR: Path = BASE_DIR / "data" / "embeddings"
    
    # User content Annoy indexes stored here (same as embeddings for now)
    USER_CONTENT_EMBEDDINGS_DIR: Path = BASE_DIR / "data" / "embeddings"

    # NOTE: Main databases (catalog, user_content, users) migrated to PostgreSQL 2026-02-01
    # See: ai_radio_catalog, ai_radio_user_content, ai_radio databases
    # Old SQLite backups in: data/sqlite_backup_2026_02_01/

    # TTS embedding table names (stored in ai_radio_embeddings PostgreSQL database)
    TTS_EMBEDDING_TABLES: list = [
        "tts_embeddings", "meta_embeddings", "impulse_embeddings", "audio_embeddings"
    ]

    # TTS database pools - maps table names to PostgreSQL database URL
    # NOTE: All TTS embeddings now stored in ai_radio_embeddings PostgreSQL database
    @property
    def TTS_DB_POOLS(self) -> dict:
        """Returns mapping of TTS embedding table names to PostgreSQL connection."""
        return {
            "tts_embeddings": self.EMBEDDINGS_DATABASE_URL,
            "meta_embeddings": self.EMBEDDINGS_DATABASE_URL,
            "impulse_embeddings": self.EMBEDDINGS_DATABASE_URL,
            "audio_embeddings": self.EMBEDDINGS_DATABASE_URL,
        }

    # Catalog embedding table names (stored in ai_radio_embeddings PostgreSQL database)
    CATALOG_EMBEDDING_TABLES: list = [
        "song_title_embeddings", "primary_genre_embeddings", "secondary_genres_embeddings",
        "mood_embeddings", "primary_artist_embeddings", "similar_artists_embeddings",
        "style_embeddings", "theme_embeddings", "vocal_embeddings", "lyrics_embeddings"
    ]

    QUERY_CACHE_DIR: Path = BASE_DIR / "data" / "query_cache"
    CONTEXT_ROUTING_CACHE_DIR: Path = BASE_DIR / "data" / "context_routing_cache"

    # YouTube video clips cache (background video art for tracks)
    YOUTUBE_CLIPS_DIR: Path = BASE_DIR / "data" / "youtube_clips"
    PROMPT_DEBUG_ENABLED: bool = True  # Enable/disable prompt & response debug file saving
    PROMPT_DEBUG_DIR: Path = BASE_DIR / "data" / "prompt_debug"

    # User content embedding table names (stored in ai_radio_embeddings PostgreSQL database)
    USER_CONTENT_EMBEDDING_TABLES: list = [
        "transcription_embeddings", "category_embeddings", "urgency_embeddings",
        "importance_embeddings", "tags_embeddings", "username_embeddings",
        "location_embeddings", "target_audience_embeddings", "sentiment_embeddings",
        "content_theme_embeddings"
    ]
    USER_CONTENT_QUERY_CACHE_DIR: Path = BASE_DIR / "data" / "user_content_query_cache"

    IMPULSE_SIMILARITY_THRESHOLD: float = 0.75
    TTS_SIMILARITY_THRESHOLD: float = 0.8
    META_SIMILARITY_THRESHOLD: float = 0.75
    AUDIO_SIMILARITY_THRESHOLD: float = 0.5

    VECTOR_DB_SHOTGUN_COOLDOWN: int = 600
    VECTOR_DB_SHOTGUN_CLEANUP_INTERVAL: int = 1200

    VOICE_PREFERENCES: dict = {
        "terry": {
            "voice_id": "68pqF1U7kYY9Mx5Mjv6C",
            "model_id": "eleven_multilingual_v2",
            "stability": 0.30,
            "similarity_boost": 0.85,
            "style": 0,
            "use_speaker_boost": False
        },
        "shaquille": {
            "voice_id": "O69sbyoK3Wa9pcqeiEYM",
            "model_id": "eleven_multilingual_v2",
            "stability": 0.30,
            "similarity_boost": 0.85,
            "style": 0,
            "use_speaker_boost": False
        }
    }

    GENERATION_PERMISSIONS: dict = {
        'meta': set(),
        'impulse': {'terry', 'shaquille'},
        'sentence': {'terry', 'shaquille'},
        'audio': set()
    }

    AUDIO_EFFECT_CONFIG: dict = {
        'global': {
            'reverb': {
                'room_size': 0.0025,
                'damping': 0.7,
                'wet_level': 0.15,
                'dry_level': 0.85
            },
            'gain': {
                'min_gain_db': 0,
                'max_gain_db': -4
            },
            'eq': {
                'high_shelf_cut_max': -6,
                'low_shelf_boost_max': 3
            }
        }
    }

    APOLLO_DIR: Path = BASE_DIR / "server" / "Apollo"
    APOLLO_CHECKPOINTS_DIR: Path = APOLLO_DIR / "checkpoints"
    JWT_SECRET_KEY: str = os.getenv(
        "JWT_SECRET_KEY",
        "dev-secret-key-change-in-production"
    )
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_DAYS: int = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRE_DAYS", "30"))

    CORS_ORIGINS: list = os.getenv("CORS_ORIGINS", "*").split(",")

    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-pro")
    GEMINI_TEMPERATURE: float = float(os.getenv("GEMINI_TEMPERATURE", "1.0"))

    GEMINI_DJ_MODEL: str = os.getenv("GEMINI_DJ_MODEL", "gemini-2.5-flash-lite")
    GEMINI_DJ_TEMPERATURE: float = float(os.getenv("GEMINI_DJ_TEMPERATURE", "0.9"))
    GEMINI_DJ_MAX_TOKENS: int = int(os.getenv("GEMINI_DJ_MAX_TOKENS", "1000"))

    GEMINI_COMMAND_MODEL: str = os.getenv("GEMINI_COMMAND_MODEL", "gemini-2.5-flash-lite")
    GEMINI_COMMAND_TEMPERATURE: float = float(os.getenv("GEMINI_COMMAND_TEMPERATURE", "0.2"))
    GEMINI_COMMAND_MAX_TOKENS: int = int(os.getenv("GEMINI_COMMAND_MAX_TOKENS", "500"))

    GEMINI_AUDIO_MODEL: str = os.getenv("GEMINI_AUDIO_MODEL", "gemini-2.5-flash-lite")
    GEMINI_AUDIO_TEMPERATURE: float = float(os.getenv("GEMINI_AUDIO_TEMPERATURE", "0.8"))
    GEMINI_AUDIO_MAX_TOKENS: int = int(os.getenv("GEMINI_AUDIO_MAX_TOKENS", "200"))

    GEMINI_NODE_PRODUCER_MODEL: str = os.getenv("GEMINI_NODE_PRODUCER_MODEL", "gemini-2.5-flash-lite")
    GEMINI_NODE_PRODUCER_TEMPERATURE: float = float(os.getenv("GEMINI_NODE_PRODUCER_TEMPERATURE", "0.1"))
    GEMINI_NODE_PRODUCER_SIMILARITY_THRESHOLD: float = float(os.getenv("GEMINI_NODE_PRODUCER_SIMILARITY_THRESHOLD", "0.85"))

    WHISPER_FAST_MODEL: str = os.getenv("WHISPER_FAST_MODEL", "base.en")
    WHISPER_QUALITY_MODEL: str = os.getenv("WHISPER_QUALITY_MODEL", "large-v3")
    WHISPER_DEVICE: str = os.getenv("WHISPER_DEVICE", "cuda")
    WHISPER_COMPUTE_TYPE: str = os.getenv("WHISPER_COMPUTE_TYPE", "float16")
    WHISPER_TIMEOUT: int = int(os.getenv("WHISPER_TIMEOUT", "30"))
    WHISPER_MAX_FILE_SIZE_MB: int = int(os.getenv("WHISPER_MAX_FILE_SIZE_MB", "10"))

    SUNO_MODEL_VERSION: str = os.getenv("SUNO_MODEL_VERSION", "V5")
    GENERATION_COOLDOWN_SECONDS: int = int(os.getenv("GENERATION_COOLDOWN_SECONDS", "300"))
    GENERATION_DAILY_LIMIT_GUEST: int = int(os.getenv("GENERATION_DAILY_LIMIT_GUEST", "10"))
    GENERATION_DAILY_LIMIT_FREE: int = int(os.getenv("GENERATION_DAILY_LIMIT_FREE", "20"))
    GENERATION_DAILY_LIMIT_PREMIUM: int = int(os.getenv("GENERATION_DAILY_LIMIT_PREMIUM", "100"))

    AUDIO_SAMPLE_RATE: int = int(os.getenv("AUDIO_SAMPLE_RATE", "44100"))
    AUDIO_FFT_SIZE: int = int(os.getenv("AUDIO_FFT_SIZE", "4096"))
    AUDIO_HOP_LENGTH: int = int(os.getenv("AUDIO_HOP_LENGTH", "512"))
    AUDIO_CHUNK_DURATION_SECONDS: int = int(os.getenv("AUDIO_CHUNK_DURATION_SECONDS", "20"))
    AUDIO_CHUNK_OVERLAP_SECONDS: int = int(os.getenv("AUDIO_CHUNK_OVERLAP_SECONDS", "1"
    ))
    ENABLE_VOCAL_ENHANCEMENT: bool = os.getenv("ENABLE_VOCAL_ENHANCEMENT", "True").lower() == "true"
    DEMUCS_MODEL: str = os.getenv("DEMUCS_MODEL", "htdemucs_ft")

    MAX_PARALLEL_CPU_WORKERS: int = 16

    PLAYBACK_QUEUE_SIZE: int = int(os.getenv("PLAYBACK_QUEUE_SIZE", "10"))
    PLAYBACK_HISTORY_SIZE: int = int(os.getenv("PLAYBACK_HISTORY_SIZE", "50"))

    ANNOUNCER_MIN_SAFE_ZONE_DURATION: float = float(os.getenv("ANNOUNCER_MIN_SAFE_ZONE_DURATION", "3.0"))
    ANNOUNCER_COOLDOWN_SECONDS: int = int(os.getenv("ANNOUNCER_COOLDOWN_SECONDS", "0"))
    ANNOUNCER_TRIGGER_PROBABILITY: float = float(os.getenv("ANNOUNCER_TRIGGER_PROBABILITY", "1.0"))
    ANNOUNCER_TRIGGER_EARLY_MS: int = int(os.getenv("ANNOUNCER_TRIGGER_EARLY_MS", "5000"))
    ANNOUNCER_PENDING_THRESHOLD_MS: int = int(os.getenv("ANNOUNCER_PENDING_THRESHOLD_MS", "60000"))

    VECTOR_SEARCH_DEFAULT_RESULTS: int = int(os.getenv("VECTOR_SEARCH_DEFAULT_RESULTS", "10"))
    VECTOR_EMBEDDING_CACHE_SECONDS: int = int(os.getenv("VECTOR_EMBEDDING_CACHE_SECONDS", "3600"))

    CLEANUP_INTERVAL_SECONDS: int = int(os.getenv("CLEANUP_INTERVAL_SECONDS", "3600"))
    QUEUE_MONITOR_INTERVAL_SECONDS: int = int(os.getenv("QUEUE_MONITOR_INTERVAL_SECONDS", "60"))
    ENABLE_FILE_LOGGING: bool = os.getenv("ENABLE_FILE_LOGGING", "True").lower() == "true"

    ELEVENLABS_API_KEY: str = os.getenv("ELEVENLABS_API_KEY", "")
    TTS_CONCURRENCY_LIMIT: int = int(os.getenv("TTS_CONCURRENCY_LIMIT", "4"))

    SUNO_API_KEY: str = os.getenv("SUNO_API_KEY", "")
    SUNO_BASE_URL: str = os.getenv("SUNO_BASE_URL", "https://api.sunoapi.org/api/v1")
    SUNO_CALLBACK_URL: str = os.getenv("SUNO_CALLBACK_URL", "https://example.com/callback")
    SUNO_POLL_INTERVAL: int = int(os.getenv("SUNO_POLL_INTERVAL", "5"))
    SUNO_MAX_WAIT: int = int(os.getenv("SUNO_MAX_WAIT", "600"))

    @classmethod
    def load_api_key_from_file(cls, key_name: str) -> str:
        if not getattr(cls, key_name, ""):
            try:
                import sys
                keys_path = str(BASE_DIR / "server" / "keys")
                if keys_path not in sys.path:
                    sys.path.insert(0, keys_path)
                from api_secrets import ELEVENLABS_API_KEY
                if key_name == "ELEVENLABS_API_KEY":
                    cls.ELEVENLABS_API_KEY = ELEVENLABS_API_KEY
            except ImportError as e:
                print(f"[SETTINGS] Failed to import api_secrets: {e}")
            except Exception as e:
                print(f"[SETTINGS] Unexpected error loading key: {e}")

        if key_name == "GEMINI_API_KEY" and not cls.GEMINI_API_KEY:
            key_file = BASE_DIR / "server" / "keys" / "gemini_key.txt"
            if key_file.exists():
                cls.GEMINI_API_KEY = key_file.read_text().strip()
        elif key_name == "SUNO_API_KEY" and not cls.SUNO_API_KEY:
            key_file = BASE_DIR / "server" / "keys" / "suno_key.txt"
            if key_file.exists():
                cls.SUNO_API_KEY = key_file.read_text().strip()
        elif key_name == "ELEVENLABS_API_KEY" and not cls.ELEVENLABS_API_KEY:
            key_file = BASE_DIR / "server" / "keys" / "elevenlabs_key.txt"
            if key_file.exists():
                cls.ELEVENLABS_API_KEY = key_file.read_text().strip()
        return getattr(cls, key_name, "")

    @classmethod
    def ensure_directories(cls):
        """Ensure all required directories exist (centralized directory creation)"""
        # Core data directories
        cls.EMBEDDINGS_DIR.mkdir(parents=True, exist_ok=True)
        cls.LOGS_DIR.mkdir(parents=True, exist_ok=True)
        cls.FAILED_PROMPTS_DIR.mkdir(parents=True, exist_ok=True)

        # Catalog directories (all audio processing outputs)
        cls.METADATA_DIR.mkdir(parents=True, exist_ok=True)
        cls.AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        cls.ARTWORK_DIR.mkdir(parents=True, exist_ok=True)
        cls.ARTWORK_ENRICHED_DIR.mkdir(parents=True, exist_ok=True)
        cls.WAV_DIR.mkdir(parents=True, exist_ok=True)
        cls.UPSCALED_WAV_DIR.mkdir(parents=True, exist_ok=True)
        cls.SONIC_WAV_DIR.mkdir(parents=True, exist_ok=True)
        cls.ENHANCED_WAV_DIR.mkdir(parents=True, exist_ok=True)
        cls.AUDIOFEATURES_DIR.mkdir(parents=True, exist_ok=True)
        cls.LYRIC_TIMESTAMPS_DIR.mkdir(parents=True, exist_ok=True)
        cls.OPUS_128K_DIR.mkdir(parents=True, exist_ok=True)
        cls.OPUS_192K_DIR.mkdir(parents=True, exist_ok=True)
        cls.OPUS_256K_DIR.mkdir(parents=True, exist_ok=True)
        cls.DEMUCS_STEMS_DIR.mkdir(parents=True, exist_ok=True)
        cls.VOCAL_ENHANCED_WAV_DIR.mkdir(parents=True, exist_ok=True)
        cls.ANALYTICS_DIR.mkdir(parents=True, exist_ok=True)
        (cls.ANALYTICS_DIR / "tracks").mkdir(parents=True, exist_ok=True)
        (cls.ANALYTICS_DIR / "daily_events").mkdir(parents=True, exist_ok=True)
        (cls.ANALYTICS_DIR / "exports").mkdir(parents=True, exist_ok=True)

        # User content directories
        cls.USERS_DIR.mkdir(parents=True, exist_ok=True)
        cls.OPINIONS_DIR.mkdir(parents=True, exist_ok=True)

        # TTS engine directories
        cls.TTS_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        cls.META_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        cls.IMPULSE_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        cls.BREATH_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        cls.AUDIO_EFFECT_DIR.mkdir(parents=True, exist_ok=True)
        cls.STUDIO_AUDIO_DIR.mkdir(parents=True, exist_ok=True)

        # Cache directories
        cls.QUERY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cls.CONTEXT_ROUTING_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cls.USER_CONTENT_QUERY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cls.PROMPT_DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        cls.YOUTUBE_CLIPS_DIR.mkdir(parents=True, exist_ok=True)

settings = Settings()
settings.ensure_directories()