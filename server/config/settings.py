import os
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

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

    # User content database (shoutouts, replies, reviews)
    USER_CONTENT_DATABASE_URL: str = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/ai_radio_user_content"

    # Embeddings database (vector caches, query caches, TTS embeddings)
    EMBEDDINGS_DATABASE_URL: str = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/ai_radio_embeddings"

    CATALOG_DIR: Path = Path(os.getenv("CATALOG_DIR", str(BASE_DIR / "server" / "catalog")))
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
    MUSIC_BEDS_DIR: Path = Path(os.getenv("MUSIC_BEDS_DIR", str(CATALOG_DIR / "music_beds")))
    FAILED_PROMPTS_DIR: Path = BASE_DIR / "data" / "failed_prompts"
    LOGS_DIR: Path = BASE_DIR / "data" / "logs"
    USERS_DIR: Path = CATALOG_DIR / "users"

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

    TTS_ENGINE_DATA_DIR: Path = BASE_DIR / "data" / "tts_dj_engine_data"
    TTS_AUDIO_DIR: Path = TTS_ENGINE_DATA_DIR / "tts_audio"
    PARALANGUAGE_AUDIO_DIR: Path = TTS_ENGINE_DATA_DIR / "paralanguage_audio"
    BREATH_AUDIO_DIR: Path = TTS_ENGINE_DATA_DIR / "breath_audio"
    AUDIO_EFFECT_DIR: Path = TTS_ENGINE_DATA_DIR / "audio_effect_audio"
    STUDIO_AUDIO_DIR: Path = TTS_ENGINE_DATA_DIR / "studio_audio"

    # All vector databases and embeddings stored here
    EMBEDDINGS_DIR: Path = Path(os.getenv("EMBEDDINGS_DIR") or BASE_DIR / "data" / "embeddings")
    CATALOG_EMBEDDINGS_DIR: Path = EMBEDDINGS_DIR
    USER_CONTENT_EMBEDDINGS_DIR: Path = EMBEDDINGS_DIR

    # NOTE: Main databases (catalog, user_content, users) migrated to PostgreSQL 2026-02-01
    # See: ai_radio_catalog, ai_radio_user_content, ai_radio databases
    # Old SQLite backups in: data/sqlite_backup_2026_02_01/

    # TTS embedding table names (stored in ai_radio_embeddings PostgreSQL database)
    TTS_EMBEDDING_TABLES: list = [
        "tts_embeddings", "paralanguage_embeddings", "audio_embeddings", "breath_embeddings"
    ]

    # TTS database pools - maps table names to PostgreSQL database URL
    # NOTE: All TTS embeddings now stored in ai_radio_embeddings PostgreSQL database
    @property
    def TTS_DB_POOLS(self) -> dict:
        """Returns mapping of TTS embedding table names to PostgreSQL connection."""
        return {
            "tts_embeddings": self.EMBEDDINGS_DATABASE_URL,
            "paralanguage_embeddings": self.EMBEDDINGS_DATABASE_URL,
            "audio_embeddings": self.EMBEDDINGS_DATABASE_URL,
            "breath_embeddings": self.EMBEDDINGS_DATABASE_URL,
        }


    QUERY_CACHE_DIR: Path = BASE_DIR / "data" / "query_cache"
    CONTEXT_ROUTING_CACHE_DIR: Path = BASE_DIR / "data" / "context_routing_cache"

    # YouTube video clips cache (background video art for tracks)
    YOUTUBE_CLIPS_DIR: Path = BASE_DIR / "data" / "youtube_clips"
    NEWS_DEFAULT_COUNTRY: str = os.getenv("NEWS_DEFAULT_COUNTRY", "US")
    YOUTUBE_CLIPS_MAX_CACHE_GB: float = float(os.getenv("YOUTUBE_CLIPS_MAX_CACHE_GB", "5"))
    YOUTUBE_CLIPS_PREFETCH_PER_CYCLE: int = int(os.getenv("YOUTUBE_CLIPS_PREFETCH_PER_CYCLE", "12"))
    YOUTUBE_CLIPS_RATE_LIMIT: str = os.getenv("YOUTUBE_CLIPS_RATE_LIMIT", "2M")
    YOUTUBE_CLIPS_FAILURES_BEFORE_BACKOFF: int = int(os.getenv("YOUTUBE_CLIPS_FAILURES_BEFORE_BACKOFF", "3"))
    YOUTUBE_CLIPS_BACKOFF_S: int = int(os.getenv("YOUTUBE_CLIPS_BACKOFF_S", "3600"))
    PROMPT_DEBUG_ENABLED: bool = os.getenv("PROMPT_DEBUG_ENABLED", "false").lower() == "true"
    PROMPT_DEBUG_DIR: Path = BASE_DIR / "data" / "prompt_debug"

    SEMANTIC_ENCODER: str = os.getenv("SEMANTIC_ENCODER", "sentence-transformers/all-mpnet-base-v2")
    SEMANTIC_ENCODER_DIM: int = int(os.getenv("SEMANTIC_ENCODER_DIM", "768"))
    SEMANTIC_ENCODER_SLUG: str = os.getenv("SEMANTIC_ENCODER_SLUG", "mpnet")

    USER_CONTENT_QUERY_CACHE_DIR: Path = BASE_DIR / "data" / "user_content_query_cache"

    FILLER_LEARN_BELOW: float = float(os.getenv("FILLER_LEARN_BELOW", "0.6"))
    FILLER_COOLDOWN_S: int = int(os.getenv("FILLER_COOLDOWN_S", "600"))
    FILLER_LEARN_MAX_PENDING: int = int(os.getenv("FILLER_LEARN_MAX_PENDING", "4"))
    INTERLUDE_SILENCE_S: float = float(os.getenv("INTERLUDE_SILENCE_S", "1.0"))
    INTERLUDE_MAX_PER_TURN: int = int(os.getenv("INTERLUDE_MAX_PER_TURN", "3"))
    INTERLUDE_SEQUENCE: int = int(os.getenv("INTERLUDE_SEQUENCE", "3"))
    INTERLUDE_MONITOR_INTERVAL_S: float = float(os.getenv("INTERLUDE_MONITOR_INTERVAL_S", "0.5"))
    TTS_SIMILARITY_THRESHOLD: float = float(os.getenv("TTS_SIMILARITY_THRESHOLD", "0.975"))
    PARALANGUAGE_SIMILARITY_THRESHOLD: float = float(os.getenv("PARALANGUAGE_SIMILARITY_THRESHOLD", "0.85"))
    AUDIO_SIMILARITY_THRESHOLD: float = float(os.getenv("AUDIO_SIMILARITY_THRESHOLD", "0.75"))
    BREATH_SIMILARITY_THRESHOLD: float = 0.65

    VECTOR_DB_SHOTGUN_COOLDOWN: int = 600

    TTS_SERVER_URL: str = os.getenv("TTS_SERVER_URL", "http://127.0.0.1:8090")
    TTS_SERVER_EXTERNAL: bool = os.getenv("TTS_SERVER_EXTERNAL", "false").lower() == "true"
    TTS_REQUEST_TIMEOUT: float = float(os.getenv("TTS_REQUEST_TIMEOUT", "120"))
    TTS_SAMPLE_RATE: int = int(os.getenv("TTS_SAMPLE_RATE", "24000"))

    TTS_SFX_TARGET_DBFS: float = float(os.getenv("TTS_SFX_TARGET_DBFS", "-46"))
    TTS_SFX_MAX_PEAK_DBFS: float = float(os.getenv("TTS_SFX_MAX_PEAK_DBFS", "-24"))
    PARALANGUAGE_ENGINE_TAGS: bool = os.getenv("PARALANGUAGE_ENGINE_TAGS", "true").lower() == "true"
    ENGINE_SOUND_TAGS: tuple = ("laugh", "chuckle", "sigh", "gasp", "cough", "clear throat", "sniff", "groan",
                                "shush", "crying", "whispering")
    VOICE_PREFERENCES: dict = {
        "jess": {"voice": "jess", "temperature": 0.8},
        "leo": {"voice": "leo", "temperature": 0.8, "gain_db": float(os.getenv("LEO_GAIN_DB", "2.2"))},
        "station": {"voice": "station", "temperature": 0.8},
    }

    GENERATION_PERMISSIONS: dict = {
        'paralanguage': {'jess', 'leo'},
        'sentence': {'jess', 'leo'},
        'breath': {'jess', 'leo'},
        'audio': set()
    }

    TTS_BACKGROUND_CONCURRENCY: int = int(os.getenv("TTS_BACKGROUND_CONCURRENCY", "1"))
    TTS_BACKGROUND_MAX_PENDING: int = int(os.getenv("TTS_BACKGROUND_MAX_PENDING", "8"))
    TTS_BACKGROUND_REFRESH_COOLDOWN_S: int = int(os.getenv("TTS_BACKGROUND_REFRESH_COOLDOWN_S", "600"))

    AUDIO_EFFECT_CONFIG: dict = {
        'proximity': {
            'near_m': 0.05,
            'far_m': 4.0,
            'reference_m': 0.12,
            'max_near_boost_db': 4.0,
            'proximity_bass_db': 6.0,
            'proximity_bass_hz': 150.0,
            'proximity_bass_until_m': 0.3,
            'air_loss_db': -8.0,
            'air_loss_hz': 5000.0,
            'output_trim_db': -3.7,
            'room': {
                'room_size': 0.0025,
                'damping': 0.7,
                'width': 1.0,
                'wet_level': 0.15,
                'dry_level': 0.85
            }
        },
        'station': {
            'highpass_hz': 190.0,
            'lowpass_hz': 6200.0,
            'presence_hz': 2100.0,
            'presence_db': 3.5,
            'ring_mod_hz': 52.0,
            'ring_mod_mix': 0.18,
            'comb_delay_s': 0.0055,
            'comb_feedback': 0.32,
            'comb_mix': 0.22,
            'crush_bits': 9.0,
            'crush_mix': 0.22,
            'compressor_threshold_db': -22.0,
            'compressor_ratio': 3.5,
            'slap_delay_s': 0.085,
            'slap_mix': 0.12,
            'reverb_room_size': 0.14,
            'reverb_wet': 0.09,
            'reverb_width': 0.6
        }
    }

    APOLLO_DIR: Path = BASE_DIR / "server" / "Apollo"
    APOLLO_CHECKPOINTS_DIR: Path = APOLLO_DIR / "checkpoints"
    JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "")
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_DAYS: int = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRE_DAYS", "7"))

    CORS_ORIGINS: list = [o.strip() for o in os.getenv("CORS_ORIGINS", "https://plair.live,https://www.plair.live").split(",") if o.strip()]

    PUBLIC_BASE_URL: str = os.getenv("PUBLIC_BASE_URL", "https://plair.live").rstrip("/")
    ENABLE_API_DOCS: bool = os.getenv("ENABLE_API_DOCS", "false").lower() == "true"
    ADMIN_USER_IDS: frozenset = frozenset(
        int(uid) for uid in os.getenv("ADMIN_USER_IDS", "1").split(",") if uid.strip().isdigit()
    )
    TRUSTED_PROXY_IPS: frozenset = frozenset(
        ip.strip() for ip in os.getenv("TRUSTED_PROXY_IPS", "127.0.0.1,::1").split(",") if ip.strip()
    )

    RATE_LIMITS: dict = {
        "dj_talk_guest": (int(os.getenv("RATE_LIMIT_DJ_TALK_GUEST_PER_HOUR", "20")), 3600),
        "dj_talk_user": (int(os.getenv("RATE_LIMIT_DJ_TALK_USER_PER_HOUR", "120")), 3600),
        "dj_talk_guest_ip": (int(os.getenv("RATE_LIMIT_DJ_TALK_GUEST_IP_PER_HOUR", "60")), 3600),
        "transcribe_guest": (int(os.getenv("RATE_LIMIT_TRANSCRIBE_GUEST_PER_HOUR", "20")), 3600),
        "transcribe_user": (int(os.getenv("RATE_LIMIT_TRANSCRIBE_USER_PER_HOUR", "120")), 3600),
        "transcribe_guest_ip": (int(os.getenv("RATE_LIMIT_TRANSCRIBE_GUEST_IP_PER_HOUR", "60")), 3600),
        "auth_ip": (int(os.getenv("RATE_LIMIT_AUTH_IP_PER_MINUTE", "10")), 60),
        "auth_username": (int(os.getenv("RATE_LIMIT_AUTH_USERNAME_PER_MINUTE", "10")), 60),
        "search_guest": (int(os.getenv("RATE_LIMIT_SEARCH_GUEST_PER_MINUTE", "30")), 60),
        "search_user": (int(os.getenv("RATE_LIMIT_SEARCH_USER_PER_MINUTE", "60")), 60),
        "search_ai_user": (int(os.getenv("RATE_LIMIT_SEARCH_AI_USER_PER_HOUR", "60")), 3600),
        "ai_upload_user": (int(os.getenv("RATE_LIMIT_AI_UPLOAD_USER_PER_HOUR", "30")), 3600),
        "shoutout_play_guest": (int(os.getenv("RATE_LIMIT_SHOUTOUT_PLAY_GUEST_PER_MINUTE", "30")), 60),
        "shoutout_play_user": (int(os.getenv("RATE_LIMIT_SHOUTOUT_PLAY_USER_PER_MINUTE", "120")), 60),
        "shoutout_play_guest_ip": (int(os.getenv("RATE_LIMIT_SHOUTOUT_PLAY_GUEST_IP_PER_MINUTE", "120")), 60),
        "usage_stats": (int(os.getenv("RATE_LIMIT_USAGE_STATS_PER_MINUTE", "30")), 60),
    }
    SHOUTOUT_PLAY_DEDUPE_HOURS: float = float(os.getenv("SHOUTOUT_PLAY_DEDUPE_HOURS", "6"))

    MAX_PROFILE_PICTURE_BYTES: int = 10 * 1024 * 1024
    MAX_ARTWORK_UPLOAD_BYTES: int = 15 * 1024 * 1024
    MAX_TRANSCRIBE_AUDIO_BYTES: int = 10 * 1024 * 1024
    MAX_SHARE_VIDEO_BYTES: int = 500 * 1024 * 1024
    MAX_BASE64_AUDIO_CHARS: int = 15 * 1024 * 1024
    UPLOAD_MAX_AUDIO_BYTES: int = int(os.getenv("UPLOAD_MAX_AUDIO_BYTES", str(100 * 1024 * 1024)))
    UPLOAD_MAX_VIDEO_BYTES: int = int(os.getenv("UPLOAD_MAX_VIDEO_BYTES", str(10 * 1024 * 1024 * 1024)))
    UPLOAD_MAX_DURATION_S: int = int(os.getenv("UPLOAD_MAX_DURATION_S", "1200"))
    GPU_IDLE_UNLOAD_MINUTES: float = float(os.getenv("GPU_IDLE_UNLOAD_MINUTES", "10"))

    PLAYBACK_SESSION_IDLE_TIMEOUT_S: int = int(os.getenv("PLAYBACK_SESSION_IDLE_TIMEOUT_S", "7200"))
    WS_MAX_CONNECTIONS_PER_SESSION: int = int(os.getenv("WS_MAX_CONNECTIONS_PER_SESSION", "8"))

    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
    GEMINI_TEMPERATURE: float = float(os.getenv("GEMINI_TEMPERATURE", "1.0"))

    GEMINI_DJ_MODEL: str = os.getenv("GEMINI_DJ_MODEL", "gemini-3.5-flash-lite")
    GEMINI_DJ_TEMPERATURE: float = float(os.getenv("GEMINI_DJ_TEMPERATURE", "0.9"))
    GEMINI_DJ_MAX_TOKENS: int = int(os.getenv("GEMINI_DJ_MAX_TOKENS", "8192"))

    DJ_TOOL_MAX_LIVE_FETCHES: int = int(os.getenv("DJ_TOOL_MAX_LIVE_FETCHES", "2"))
    PULSE_ENABLED: bool = os.getenv("PULSE_ENABLED", "true").lower() == "true"
    PULSE_NODE_TIMEOUT_S: float = float(os.getenv("PULSE_NODE_TIMEOUT_S", "2.5"))
    PULSE_FETCH_TIMEOUT_S: float = float(os.getenv("PULSE_FETCH_TIMEOUT_S", "7"))
    PULSE_FETCH_BELOW: int = int(os.getenv("PULSE_FETCH_BELOW", "2"))
    PULSE_PLACE_RADIUS_M: float = float(os.getenv("PULSE_PLACE_RADIUS_M", "2000"))
    PULSE_OFFERED_MEMORY_S: float = float(os.getenv("PULSE_OFFERED_MEMORY_S", "7200"))
    PULSE_AIRED_PENALTY: float = float(os.getenv("PULSE_AIRED_PENALTY", "0.35"))
    PULSE_DEMAND_ENABLED: bool = os.getenv("PULSE_DEMAND_ENABLED", "true").lower() == "true"
    PULSE_TREND_MIN_ASKERS: int = int(os.getenv("PULSE_TREND_MIN_ASKERS", "3"))
    PULSE_DEMAND_ANSWERS: int = int(os.getenv("PULSE_DEMAND_ANSWERS", "5"))
    PULSE_COMMUNITY_RADIUS_KM: float = float(os.getenv("PULSE_COMMUNITY_RADIUS_KM", "60"))
    PULSE_REQUEST_DEDUPE_S: float = float(os.getenv("PULSE_REQUEST_DEDUPE_S", "180"))
    PULSE_REQUEST_REBUILD_S: int = int(os.getenv("PULSE_REQUEST_REBUILD_S", "300"))
    PULSE_DEMAND_KEEP_DAYS: int = int(os.getenv("PULSE_DEMAND_KEEP_DAYS", "90"))
    PULSE_PREFETCH_MIN_ASKERS: int = int(os.getenv("PULSE_PREFETCH_MIN_ASKERS", "2"))
    PULSE_PREFETCH_TOPICS: int = int(os.getenv("PULSE_PREFETCH_TOPICS", "3"))
    PULSE_CHART_MIN_LISTENERS: int = int(os.getenv("PULSE_CHART_MIN_LISTENERS", "2"))
    PULSE_CHART_CACHE_S: float = float(os.getenv("PULSE_CHART_CACHE_S", "900"))
    PULSE_CONTEXT_ITEMS: int = int(os.getenv("PULSE_CONTEXT_ITEMS", "4"))
    PULSE_RECENCY_HALF_LIFE_DAYS: float = float(os.getenv("PULSE_RECENCY_HALF_LIFE_DAYS", "7"))
    PULSE_LINK_DISTANCE_M: float = float(os.getenv("PULSE_LINK_DISTANCE_M", "300"))
    PULSE_NEAR_RADIUS_M: float = float(os.getenv("PULSE_NEAR_RADIUS_M", "3000"))
    PULSE_CITY_RADIUS_KM: float = float(os.getenv("PULSE_CITY_RADIUS_KM", "60"))
    GEO_ENABLED: bool = os.getenv("GEO_ENABLED", "true").lower() == "true"
    GEO_MISS_RETRY_DAYS: int = int(os.getenv("GEO_MISS_RETRY_DAYS", "30"))
    DJ_TOOL_MAX_ROUNDS: int = int(os.getenv("DJ_TOOL_MAX_ROUNDS", "4"))
    DJ_TURN_TRACE_ENABLED: bool = os.getenv("DJ_TURN_TRACE_ENABLED", "true").lower() == "true"
    DJ_TURN_TRACE_HOURS: int = int(os.getenv("DJ_TURN_TRACE_HOURS", "48"))
    DJ_TOOL_THINKING_BUDGET: Optional[int] = (int(os.getenv("DJ_TOOL_THINKING_BUDGET", "-1"))
                                              if os.getenv("DJ_TOOL_THINKING_BUDGET", "-1").strip() else None)
    DJ_TOOL_FOLLOWUP_THINKING_BUDGET: int = int(os.getenv("DJ_TOOL_FOLLOWUP_THINKING_BUDGET", "0"))
    GEMINI_THINKING_BUDGET_FLOOR: dict = {
        model.strip(): int(floor) for model, floor in (
            entry.rsplit(":", 1) for entry in os.getenv(
                "GEMINI_THINKING_BUDGET_FLOOR", "gemini-3.5-flash-lite:1").split(",") if ":" in entry)}
    DJ_TOOL_CALL_TIMEOUT_S: float = float(os.getenv("DJ_TOOL_CALL_TIMEOUT_S", "8"))
    DJ_TOOL_MAX_CALLS_PER_TURN: int = int(os.getenv("DJ_TOOL_MAX_CALLS_PER_TURN", "10"))
    DJ_TIMELINE_DEFAULT_MINUTES: int = int(os.getenv("DJ_TIMELINE_DEFAULT_MINUTES", "120"))
    DJ_TIMELINE_MAX_MINUTES: int = int(os.getenv("DJ_TIMELINE_MAX_MINUTES", "1440"))
    DJ_TIMELINE_MAX_ENTRIES: int = int(os.getenv("DJ_TIMELINE_MAX_ENTRIES", "25"))
    DJ_TIMELINE_KEEP_DAYS: int = int(os.getenv("DJ_TIMELINE_KEEP_DAYS", "2"))
    DJ_MICRO_MAX_TOKENS: int = int(os.getenv("DJ_MICRO_MAX_TOKENS", "2048"))
    DJ_TURN_CANCEL_TIMEOUT_S: float = float(os.getenv("DJ_TURN_CANCEL_TIMEOUT_S", "2"))
    BIOGRAPHY_DEADLINE_S: float = float(os.getenv("BIOGRAPHY_DEADLINE_S", "20"))

    GEMINI_COMMAND_MODEL: str = os.getenv("GEMINI_COMMAND_MODEL", "gemini-3.5-flash-lite")
    GEMINI_COMMAND_TEMPERATURE: float = float(os.getenv("GEMINI_COMMAND_TEMPERATURE", "0.2"))

    GEMINI_AUDIO_MODEL: str = os.getenv("GEMINI_AUDIO_MODEL", "gemini-3.5-flash-lite")
    GEMINI_AUDIO_TEMPERATURE: float = float(os.getenv("GEMINI_AUDIO_TEMPERATURE", "0.8"))
    GEMINI_AUDIO_MAX_TOKENS: int = int(os.getenv("GEMINI_AUDIO_MAX_TOKENS", "2048"))

    GEMINI_NODE_PRODUCER_MODEL: str = os.getenv("GEMINI_NODE_PRODUCER_MODEL", "gemini-3.5-flash-lite")
    GEMINI_NODE_PRODUCER_TEMPERATURE: float = float(os.getenv("GEMINI_NODE_PRODUCER_TEMPERATURE", "0.1"))
    GEMINI_NODE_PRODUCER_SIMILARITY_THRESHOLD: float = float(os.getenv("GEMINI_NODE_PRODUCER_SIMILARITY_THRESHOLD", "0.85"))
    PRODUCER_CACHE_ENABLED: bool = os.getenv("PRODUCER_CACHE_ENABLED", "true").lower() == "true"

    GEMINI_METADATA_MODEL: str = os.getenv("GEMINI_METADATA_MODEL", "gemini-3.5-flash-lite")
    GEMINI_PERSONA_MODEL: str = os.getenv("GEMINI_PERSONA_MODEL", "gemini-3.5-flash-lite")
    GEMINI_UPLOAD_ANALYSIS_MODEL: str = os.getenv("GEMINI_UPLOAD_ANALYSIS_MODEL", "gemini-3.5-flash")

    DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
    DEEPSEEK_API_BASE_URL: str = os.getenv("DEEPSEEK_API_BASE_URL", "https://api.deepseek.com/chat/completions")

    LLM_LIVE: str = os.getenv("LLM_LIVE", "gemini:gemini-3.5-flash-lite,gemini:gemini-3.5-flash")
    LLM_DJ: str = os.getenv("LLM_DJ", "gemini:gemini-2.5-flash,gemini:gemini-3.5-flash-lite")
    LLM_BACKGROUND: str = os.getenv("LLM_BACKGROUND", "deepseek:deepseek-flash,gemini:gemini-3.5-flash-lite")
    LLM_ANNOUNCE: str = os.getenv("LLM_ANNOUNCE", "deepseek:deepseek-flash,gemini:gemini-3.5-flash-lite")
    LLM_INTERPRET: str = os.getenv("LLM_INTERPRET", "deepseek:deepseek-flash,gemini:gemini-3.5-flash-lite")
    LLM_LIVE_TIMEOUT_S: float = float(os.getenv("LLM_LIVE_TIMEOUT_S", "20"))
    LLM_DJ_TIMEOUT_S: float = float(os.getenv("LLM_DJ_TIMEOUT_S", "30"))
    LLM_BACKGROUND_TIMEOUT_S: float = float(os.getenv("LLM_BACKGROUND_TIMEOUT_S", "45"))
    LLM_ANNOUNCE_TIMEOUT_S: float = float(os.getenv("LLM_ANNOUNCE_TIMEOUT_S", "12"))
    LLM_INTERPRET_TIMEOUT_S: float = float(os.getenv("LLM_INTERPRET_TIMEOUT_S", "30"))
    LLM_FALLBACK_ENABLED: bool = os.getenv("LLM_FALLBACK_ENABLED", "true").lower() == "true"
    LLM_CIRCUIT_FAILURE_THRESHOLD: int = int(os.getenv("LLM_CIRCUIT_FAILURE_THRESHOLD", "5"))
    LLM_CIRCUIT_WINDOW_SECONDS: int = int(os.getenv("LLM_CIRCUIT_WINDOW_SECONDS", "300"))
    LLM_CIRCUIT_COOLDOWN_SECONDS: int = int(os.getenv("LLM_CIRCUIT_COOLDOWN_SECONDS", "300"))
    LLM_USAGE_LOG_INTERVAL_S: int = int(os.getenv("LLM_USAGE_LOG_INTERVAL_S", "600"))

    LLM_TOOL_RESULT_MAX_CHARS: int = int(os.getenv("LLM_TOOL_RESULT_MAX_CHARS", "3000"))
    LLM_RECOVERY_MAX_STRIKES: int = int(os.getenv("LLM_RECOVERY_MAX_STRIKES", "2"))
    LLM_TOOL_COMPRESS_MIN_CHARS: int = int(os.getenv("LLM_TOOL_COMPRESS_MIN_CHARS", "600"))

    LLM_RESULT_CACHE_DIR: Path = BASE_DIR / "data" / "llm_result_cache"
    LLM_CACHE_NEWS_TTL_S: int = int(os.getenv("LLM_CACHE_NEWS_TTL_S", "1200"))
    LLM_CACHE_WEATHER_TTL_S: int = int(os.getenv("LLM_CACHE_WEATHER_TTL_S", "3600"))
    LLM_CACHE_BIOGRAPHY_TTL_S: int = int(os.getenv("LLM_CACHE_BIOGRAPHY_TTL_S", str(7 * 86400)))
    LLM_CACHE_LYRICS_TTL_S: int = int(os.getenv("LLM_CACHE_LYRICS_TTL_S", str(7 * 86400)))

    LLM_DEEPSEEK_OFFPEAK_PRICING: bool = os.getenv("LLM_DEEPSEEK_OFFPEAK_PRICING", "true").lower() == "true"

    USAGE_TRACKING_ENABLED: bool = os.getenv("USAGE_TRACKING_ENABLED", "true").lower() == "true"
    USAGE_FLUSH_INTERVAL_S: float = float(os.getenv("USAGE_FLUSH_INTERVAL_S", "5"))
    USAGE_FLUSH_BATCH: int = int(os.getenv("USAGE_FLUSH_BATCH", "200"))
    GEMINI_CACHE_ENABLED: bool = os.getenv("GEMINI_CACHE_ENABLED", "true").lower() == "true"
    GEMINI_CACHE_TTL_S: int = int(os.getenv("GEMINI_CACHE_TTL_S", "1800"))
    GEMINI_CACHE_RETRY_S: int = int(os.getenv("GEMINI_CACHE_RETRY_S", "300"))
    USAGE_BUFFER_MAX: int = int(os.getenv("USAGE_BUFFER_MAX", "20000"))
    USAGE_WRITE_TIMEOUT_S: float = float(os.getenv("USAGE_WRITE_TIMEOUT_S", "10"))
    USAGE_RAW_RETENTION_DAYS: int = int(os.getenv("USAGE_RAW_RETENTION_DAYS", "90"))
    USAGE_STATS_VISIBILITY: str = os.getenv("USAGE_STATS_VISIBILITY", "admin").strip().lower()
    USAGE_TOP_GUESTS: int = int(os.getenv("USAGE_TOP_GUESTS", "25"))
    ELECTRICITY_COST_PER_GPU_HOUR_USD: float = float(os.getenv("ELECTRICITY_COST_PER_GPU_HOUR_USD", "0"))
    SUNO_COST_PER_GENERATION_USD: float = float(os.getenv("SUNO_COST_PER_GENERATION_USD", "0.06"))
    SUNO_CREDITS_PER_GENERATION: float = float(os.getenv("SUNO_CREDITS_PER_GENERATION", "12"))
    API_COST_PER_CALL_USD: dict = {
        "weather": float(os.getenv("API_COST_WEATHER_CALL_USD", "0")),
        "news": float(os.getenv("API_COST_NEWS_CALL_USD", "0")),
        "events": float(os.getenv("API_COST_EVENTS_CALL_USD", "0")),
        "places": float(os.getenv("API_COST_PLACES_CALL_USD", "0.032")),
        "places_details": float(os.getenv("API_COST_PLACES_DETAILS_CALL_USD", "0.017")),
        "places_ids": float(os.getenv("API_COST_PLACES_IDS_CALL_USD", "0")),
        "biography": float(os.getenv("API_COST_BIOGRAPHY_CALL_USD", "0")),
        "geocoding": float(os.getenv("API_COST_GEOCODING_CALL_USD", "0.005")),
        "air_quality": float(os.getenv("API_COST_AIR_QUALITY_CALL_USD", "0.005")),
        "pollen": float(os.getenv("API_COST_POLLEN_CALL_USD", "0.01")),
    }
    REGIONAL_EVENTS_ENABLED: bool = os.getenv("REGIONAL_EVENTS_ENABLED", "true").lower() == "true"
    REGIONAL_EVENTS_REFRESH_S: int = int(os.getenv("REGIONAL_EVENTS_REFRESH_S", str(8 * 3600)))
    REGIONAL_EVENTS_DAYS_AHEAD: int = int(os.getenv("REGIONAL_EVENTS_DAYS_AHEAD", "28"))
    REGIONAL_EVENTS_PAGES: int = int(os.getenv("REGIONAL_EVENTS_PAGES", "2"))
    REGIONAL_PLACES_ENABLED: bool = os.getenv("REGIONAL_PLACES_ENABLED", "true").lower() == "true"
    REGIONAL_PLACES_REFRESH_S: int = int(os.getenv("REGIONAL_PLACES_REFRESH_S", str(14 * 86400)))
    REGIONAL_PLACES_CATEGORIES: tuple = tuple(
        c.strip() for c in os.getenv("REGIONAL_PLACES_CATEGORIES", "cafe,bar,record store,live music venue,bookstore").split(",") if c.strip()
    )
    REGIONAL_PLACES_PER_CATEGORY: int = int(os.getenv("REGIONAL_PLACES_PER_CATEGORY", "10"))
    REGIONAL_PLACES_RADIUS_M: int = int(os.getenv("REGIONAL_PLACES_RADIUS_M", "15000"))
    REGIONAL_PLACE_ID_RETENTION_DAYS: int = int(os.getenv("REGIONAL_PLACE_ID_RETENTION_DAYS", "180"))
    REGIONAL_PLACE_LOOKUPS_PER_HOUR: int = int(os.getenv("REGIONAL_PLACE_LOOKUPS_PER_HOUR", "20"))
    REGIONAL_MAX_ITEMS_PER_KIND: int = int(os.getenv("REGIONAL_MAX_ITEMS_PER_KIND", "400"))
    REGIONAL_READ_CACHE_S: int = int(os.getenv("REGIONAL_READ_CACHE_S", "600"))
    REGIONAL_CITY_MATCH_KM: float = float(os.getenv("REGIONAL_CITY_MATCH_KM", "60"))
    REGIONAL_ACTIVE_GUEST_MAX_AGE_S: int = int(os.getenv("REGIONAL_ACTIVE_GUEST_MAX_AGE_S", str(24 * 3600)))
    PLACE_MEMORY_ENABLED: bool = os.getenv("PLACE_MEMORY_ENABLED", "true").lower() == "true"
    PLACE_MEMORY_TTL_DAYS: int = int(os.getenv("PLACE_MEMORY_TTL_DAYS", "30"))
    PLACE_MEMORY_MIN_HITS: int = int(os.getenv("PLACE_MEMORY_MIN_HITS", "3"))
    PLACE_MEMORY_QUERY_MATCH: float = float(os.getenv("PLACE_MEMORY_QUERY_MATCH", "0.6"))
    PLACE_MEMORY_REUSE_DISTANCE_M: int = int(os.getenv("PLACE_MEMORY_REUSE_DISTANCE_M", "400"))
    NEWS_STORE_ENABLED: bool = os.getenv("NEWS_STORE_ENABLED", "true").lower() == "true"
    NEWS_TOP_FRESH_S: int = int(os.getenv("NEWS_TOP_FRESH_S", "2700"))
    NEWS_GEO_FRESH_S: int = int(os.getenv("NEWS_GEO_FRESH_S", "3600"))
    NEWS_TOPIC_FRESH_S: int = int(os.getenv("NEWS_TOPIC_FRESH_S", str(3 * 3600)))
    NEWS_REUSE_SIMILARITY: float = float(os.getenv("NEWS_REUSE_SIMILARITY", "0.85"))
    NEWS_REUSE_MIN_ITEMS: int = int(os.getenv("NEWS_REUSE_MIN_ITEMS", "3"))
    NEWS_SAME_STORY_SIMILARITY: float = float(os.getenv("NEWS_SAME_STORY_SIMILARITY", "0.85"))
    NEWS_RANK_TOP_N: int = int(os.getenv("NEWS_RANK_TOP_N", "10"))
    NEWS_RANK_REUSE_TOP_K: int = int(os.getenv("NEWS_RANK_REUSE_TOP_K", "10"))
    NEWS_TAGS_ENABLED: bool = os.getenv("NEWS_TAGS_ENABLED", "true").lower() == "true"
    NEWS_RETENTION_S: int = int(os.getenv("NEWS_RETENTION_S", str(48 * 3600)))
    NEWS_AIRED_TTL_S: int = int(os.getenv("NEWS_AIRED_TTL_S", str(12 * 3600)))
    NEWS_REGION_PREFETCH_ENABLED: bool = os.getenv("NEWS_REGION_PREFETCH_ENABLED", "true").lower() == "true"
    NEWS_REGION_REFRESH_S: int = int(os.getenv("NEWS_REGION_REFRESH_S", "3600"))
    NEWS_PREFETCH_TOPICS: tuple = tuple(
        t.strip().upper() for t in os.getenv("NEWS_PREFETCH_TOPICS", "NATION,WORLD").split(",") if t.strip()
    )
    NEWS_GEO_ENABLED: bool = os.getenv("NEWS_GEO_ENABLED", "true").lower() == "true"
    NEWS_LINK_PER_RUN: int = int(os.getenv("NEWS_LINK_PER_RUN", "50"))
    NEWS_LINK_RETRY_S: int = int(os.getenv("NEWS_LINK_RETRY_S", "1800"))
    NEWS_LINK_MAX_ATTEMPTS: int = int(os.getenv("NEWS_LINK_MAX_ATTEMPTS", "3"))
    NEWS_READ_ENABLED: bool = os.getenv("NEWS_READ_ENABLED", "true").lower() == "true"
    NEWS_READ_PARALLEL: int = int(os.getenv("NEWS_READ_PARALLEL", "3"))
    NEWS_READ_PER_RUN: int = int(os.getenv("NEWS_READ_PER_RUN", "40"))
    NEWS_SUMMARY_CHARS: int = int(os.getenv("NEWS_SUMMARY_CHARS", "900"))
    NEWS_SUMMARY_SENTENCES: int = int(os.getenv("NEWS_SUMMARY_SENTENCES", "5"))
    SEGMENT_DEPTHS: dict = {
        name.strip(): int(seconds) for name, seconds in (
            entry.split(":") for entry in os.getenv(
                "SEGMENT_DEPTHS", "brief:20,standard:45,detailed:100").split(",") if entry.count(":") == 1)}
    TALK_WORDS_PER_SECOND: float = float(os.getenv("TALK_WORDS_PER_SECOND", "2.0"))
    TALK_PACE_ADAPTIVE: bool = os.getenv("TALK_PACE_ADAPTIVE", "true").lower() == "true"
    TALK_PACE_MIN_SAMPLES: int = int(os.getenv("TALK_PACE_MIN_SAMPLES", "8"))
    TALK_PACE_SMOOTHING: float = float(os.getenv("TALK_PACE_SMOOTHING", "0.1"))
    TALK_PACE_SAVE_S: int = int(os.getenv("TALK_PACE_SAVE_S", "300"))
    TALK_INTRO_MIN_S: float = float(os.getenv("TALK_INTRO_MIN_S", "4"))
    TALK_ALIGNMENT_MIN: float = float(os.getenv("TALK_ALIGNMENT_MIN", "0.5"))
    NEWS_REPORT_DEPTHS: dict = {
        name.strip(): (int(stories), int(summaries)) for name, stories, summaries in (
            entry.split(":") for entry in os.getenv(
                "NEWS_REPORT_DEPTHS", "brief:4:1,standard:8:3,detailed:12:6").split(",") if entry.count(":") == 2)}
    PULSE_TOOL_PER_KIND: int = int(os.getenv("PULSE_TOOL_PER_KIND", "3"))
    PULSE_TOOL_MAX_PER_KIND: int = int(os.getenv("PULSE_TOOL_MAX_PER_KIND", "8"))
    NEWS_REPORT_SUMMARY_CHARS: int = int(os.getenv("NEWS_REPORT_SUMMARY_CHARS", "320"))
    NEWS_ANALYSE_BATCH: int = int(os.getenv("NEWS_ANALYSE_BATCH", "12"))
    NEWS_ANALYSE_PER_RUN: int = int(os.getenv("NEWS_ANALYSE_PER_RUN", "120"))
    NEWS_ANALYSE_WAIT_S: int = int(os.getenv("NEWS_ANALYSE_WAIT_S", "900"))
    WEB_PAGE_GAP_S: float = float(os.getenv("WEB_PAGE_GAP_S", "1.0"))
    WEB_PAGE_JITTER_S: float = float(os.getenv("WEB_PAGE_JITTER_S", "1.0"))
    WEB_PAGE_PARALLEL: int = int(os.getenv("WEB_PAGE_PARALLEL", "2"))
    WEB_GOOGLE_NEWS_GAP_S: float = float(os.getenv("WEB_GOOGLE_NEWS_GAP_S", "2.5"))
    WEB_GOOGLE_NEWS_JITTER_S: float = float(os.getenv("WEB_GOOGLE_NEWS_JITTER_S", "2.0"))
    WEB_GOOGLE_NEWS_DAILY_CAP: int = int(os.getenv("WEB_GOOGLE_NEWS_DAILY_CAP", "1500"))
    WEB_REST_AFTER_FAILURES: int = int(os.getenv("WEB_REST_AFTER_FAILURES", "5"))
    WEB_REST_S: int = int(os.getenv("WEB_REST_S", str(6 * 3600)))
    WEB_RATE_LIMIT_REST_S: int = int(os.getenv("WEB_RATE_LIMIT_REST_S", "900"))
    WEB_ROBOTS_TTL_S: int = int(os.getenv("WEB_ROBOTS_TTL_S", str(24 * 3600)))
    WEB_IDENTITY_REQUESTS: int = int(os.getenv("WEB_IDENTITY_REQUESTS", "150"))
    WEB_USER_AGENTS: tuple = tuple(a.strip() for a in os.getenv("WEB_USER_AGENTS", "").split("|") if a.strip())
    WEATHER_CACHE_S: int = int(os.getenv("WEATHER_CACHE_S", "1800"))
    WEATHER_PERSIST_ENABLED: bool = os.getenv("WEATHER_PERSIST_ENABLED", "true").lower() == "true"
    BIOGRAPHY_PERSIST_ENABLED: bool = os.getenv("BIOGRAPHY_PERSIST_ENABLED", "true").lower() == "true"
    BIOGRAPHY_TTL_DAYS: int = int(os.getenv("BIOGRAPHY_TTL_DAYS", "30"))
    BIOGRAPHY_NOT_FOUND_TTL_S: int = int(os.getenv("BIOGRAPHY_NOT_FOUND_TTL_S", str(24 * 3600)))
    AREA_BLOCKED_BACKOFF_S: int = int(os.getenv("AREA_BLOCKED_BACKOFF_S", str(12 * 3600)))
    AREA_RATE_BACKOFF_S: int = int(os.getenv("AREA_RATE_BACKOFF_S", "600"))
    AREA_ERROR_RETRY_S: int = int(os.getenv("AREA_ERROR_RETRY_S", "900"))
    AREA_EMPTY_TTL_S: int = int(os.getenv("AREA_EMPTY_TTL_S", "86400"))
    AREA_CACHE_RETENTION_DAYS: int = int(os.getenv("AREA_CACHE_RETENTION_DAYS", "7"))
    AREA_GEOCODE_ENABLED: bool = os.getenv("AREA_GEOCODE_ENABLED", "true").lower() == "true"
    AREA_GEOCODE_CELL_M: int = int(os.getenv("AREA_GEOCODE_CELL_M", "120"))
    AREA_GEOCODE_TTL_DAYS: int = int(os.getenv("AREA_GEOCODE_TTL_DAYS", "30"))
    AREA_GEOCODE_CONTEXT_WAIT_S: float = float(os.getenv("AREA_GEOCODE_CONTEXT_WAIT_S", "1.0"))
    AREA_GEOCODE_DAILY_CAP: int = int(os.getenv("AREA_GEOCODE_DAILY_CAP", "330"))
    AREA_GEOCODE_CUES_ENABLED: bool = os.getenv("AREA_GEOCODE_CUES_ENABLED", "true").lower() == "true"
    AREA_AIR_QUALITY_ENABLED: bool = os.getenv("AREA_AIR_QUALITY_ENABLED", "true").lower() == "true"
    AREA_AIR_QUALITY_CELL_M: int = int(os.getenv("AREA_AIR_QUALITY_CELL_M", "5000"))
    AREA_AIR_QUALITY_REFRESH_S: int = int(os.getenv("AREA_AIR_QUALITY_REFRESH_S", "3600"))
    AREA_AIR_QUALITY_DAILY_CAP: int = int(os.getenv("AREA_AIR_QUALITY_DAILY_CAP", "330"))
    AREA_AIR_QUALITY_CUE_BELOW_UAQI: int = int(os.getenv("AREA_AIR_QUALITY_CUE_BELOW_UAQI", "40"))
    AREA_AIR_QUALITY_CUE_DELTA: int = int(os.getenv("AREA_AIR_QUALITY_CUE_DELTA", "20"))
    AREA_POLLEN_ENABLED: bool = os.getenv("AREA_POLLEN_ENABLED", "true").lower() == "true"
    AREA_POLLEN_CELL_M: int = int(os.getenv("AREA_POLLEN_CELL_M", "8000"))
    AREA_POLLEN_REFRESH_S: int = int(os.getenv("AREA_POLLEN_REFRESH_S", "86400"))
    AREA_POLLEN_DAILY_CAP: int = int(os.getenv("AREA_POLLEN_DAILY_CAP", "165"))
    AREA_POLLEN_CUE_MIN_INDEX: int = int(os.getenv("AREA_POLLEN_CUE_MIN_INDEX", "4"))
    SUBSCRIPTION_PRICE_USD: float = float(os.getenv("SUBSCRIPTION_PRICE_USD", "4.99"))
    STRIPE_FEE_PERCENT: float = float(os.getenv("STRIPE_FEE_PERCENT", "2.9"))
    STRIPE_FEE_FIXED_USD: float = float(os.getenv("STRIPE_FEE_FIXED_USD", "0.30"))

    WHISPER_FAST_MODEL: str = os.getenv("WHISPER_FAST_MODEL", "base.en")
    WHISPER_QUALITY_MODEL: str = os.getenv("WHISPER_QUALITY_MODEL", "large-v3-turbo")
    WHISPER_DEVICE: str = os.getenv("WHISPER_DEVICE", "cuda")
    WHISPER_COMPUTE_TYPE: str = os.getenv("WHISPER_COMPUTE_TYPE", "float16")
    WHISPER_TIMEOUT: int = int(os.getenv("WHISPER_TIMEOUT", "30"))
    WHISPER_MAX_FILE_SIZE_MB: int = int(os.getenv("WHISPER_MAX_FILE_SIZE_MB", "10"))
    LYRIC_WHISPER_MODEL: str = os.getenv("LYRIC_WHISPER_MODEL", "medium.en")
    LYRIC_PREFER_VOCAL_STEM: bool = os.getenv("LYRIC_PREFER_VOCAL_STEM", "true").lower() == "true"

    ASSET_DOCTOR_ENABLED: bool = os.getenv("ASSET_DOCTOR_ENABLED", "true").lower() == "true"
    ASSET_DOCTOR_REPAIR_ENABLED: bool = os.getenv("ASSET_DOCTOR_REPAIR_ENABLED", "true").lower() == "true"
    ASSET_DOCTOR_STARTUP_DELAY_S: float = float(os.getenv("ASSET_DOCTOR_STARTUP_DELAY_S", "120"))
    ASSET_DOCTOR_INTERVAL_HOURS: float = float(os.getenv("ASSET_DOCTOR_INTERVAL_HOURS", "6"))
    ASSET_DOCTOR_EVENT_DELAY_S: float = float(os.getenv("ASSET_DOCTOR_EVENT_DELAY_S", "300"))
    ASSET_DOCTOR_MAX_REPAIRS_PER_HOUR: int = int(os.getenv("ASSET_DOCTOR_MAX_REPAIRS_PER_HOUR", "12"))
    ASSET_DOCTOR_MAX_ATTEMPTS: int = int(os.getenv("ASSET_DOCTOR_MAX_ATTEMPTS", "4"))
    ASSET_DOCTOR_RETRY_BASE_MINUTES: float = float(os.getenv("ASSET_DOCTOR_RETRY_BASE_MINUTES", "30"))
    ASSET_DOCTOR_RETRY_MAX_HOURS: float = float(os.getenv("ASSET_DOCTOR_RETRY_MAX_HOURS", "24"))
    ASSET_DOCTOR_DEFERRED_ARTWORK_GRACE_MINUTES: float = float(os.getenv("ASSET_DOCTOR_DEFERRED_ARTWORK_GRACE_MINUTES", "30"))
    ASSET_DOCTOR_BACKFILL_METADATA: bool = os.getenv("ASSET_DOCTOR_BACKFILL_METADATA", "false").lower() == "true"
    ASSET_DOCTOR_PROBE_AUDIO: bool = os.getenv("ASSET_DOCTOR_PROBE_AUDIO", "true").lower() == "true"
    ASSET_DOCTOR_BUSY_POLL_S: float = float(os.getenv("ASSET_DOCTOR_BUSY_POLL_S", "30"))
    ASSET_DOCTOR_MAX_BUSY_WAIT_S: float = float(os.getenv("ASSET_DOCTOR_MAX_BUSY_WAIT_S", "1800"))
    ASSET_DOCTOR_SCAN_PACE_MS: float = float(os.getenv("ASSET_DOCTOR_SCAN_PACE_MS", "5"))
    ASSET_DOCTOR_STATE_PATH: Path = Path(os.getenv("ASSET_DOCTOR_STATE_PATH", str(BASE_DIR / "data" / "asset_doctor_state.json")))
    ASSET_DOCTOR_CACHE_PATH: Path = Path(os.getenv("ASSET_DOCTOR_CACHE_PATH", str(BASE_DIR / "data" / "asset_doctor_cache.json")))
    ASSET_DOCTOR_QUARANTINE_DIR: Path = Path(os.getenv("ASSET_DOCTOR_QUARANTINE_DIR", str(CATALOG_DIR / "_asset_doctor_quarantine")))
    ASSET_DOCTOR_SHOUTOUT_REENHANCE_ENABLED: bool = os.getenv("ASSET_DOCTOR_SHOUTOUT_REENHANCE_ENABLED", "true").lower() == "true"
    ASSET_DOCTOR_SHOUTOUT_REENHANCE_PER_HOUR: int = int(os.getenv("ASSET_DOCTOR_SHOUTOUT_REENHANCE_PER_HOUR", "6"))
    SHOUTOUT_ENHANCEMENT_VERSION: int = 3

    SUNO_MODEL_VERSION: str = os.getenv("SUNO_MODEL_VERSION", "V5")

    ENABLE_VOCAL_ENHANCEMENT: bool = os.getenv("ENABLE_VOCAL_ENHANCEMENT", "True").lower() == "true"
    DEMUCS_MODEL: str = os.getenv("DEMUCS_MODEL", "htdemucs_ft")

    FLASHSR_DIR: Path = Path(os.getenv("FLASHSR_DIR", str(BASE_DIR / "models" / "flashsr" / "repo")))
    FLASHSR_INPUT_SR: int = int(os.getenv("FLASHSR_INPUT_SR", "32000"))
    FLASHSR_OVERLAP_SECONDS: float = float(os.getenv("FLASHSR_OVERLAP_SECONDS", "1.0"))
    FLASHSR_SKIP_ABOVE_HZ: float = float(os.getenv("FLASHSR_SKIP_ABOVE_HZ", "19500"))
    FLASHSR_BAND_REPLACE: bool = os.getenv("FLASHSR_BAND_REPLACE", "true").lower() == "true"
    FLASHSR_CROSSOVER_WIDTH_HZ: float = float(os.getenv("FLASHSR_CROSSOVER_WIDTH_HZ", "1000"))
    FLASHSR_HF_MAX_REL_DB: float = float(os.getenv("FLASHSR_HF_MAX_REL_DB", "0"))

    ROFORMER_MODEL_DIR: Path = Path(os.getenv("ROFORMER_MODEL_DIR", str(BASE_DIR / "models" / "roformer")))
    ROFORMER_MODEL_FILENAME: str = os.getenv("ROFORMER_MODEL_FILENAME", "vocals_mel_band_roformer.ckpt")
    ROFORMER_CONFIG_FILENAME: str = os.getenv("ROFORMER_CONFIG_FILENAME", "vocals_mel_band_roformer.yaml")
    ROFORMER_NUM_OVERLAP: int = int(os.getenv("ROFORMER_NUM_OVERLAP", "4"))

    AUDIOBOX_AESTHETICS_DIR: Path = Path(os.getenv("AUDIOBOX_AESTHETICS_DIR", str(BASE_DIR / "models" / "audiobox_aesthetics")))
    AUDIOBOX_DEVICE: str = os.getenv("AUDIOBOX_DEVICE", "cpu").lower()
    AUDIOBOX_BATCH_SIZE: int = int(os.getenv("AUDIOBOX_BATCH_SIZE", "4"))

    SONIC_MASTER_PRECISION: str = os.getenv("SONIC_MASTER_PRECISION", "fp32").lower()
    SONIC_MASTER_STEPS: int = int(os.getenv("SONIC_MASTER_STEPS", "20"))
    SONIC_MASTER_SUNO_PROMPT: str = os.getenv("SONIC_MASTER_SUNO_PROMPT", "Give the mix more shine and sparkle.")
    SONIC_MASTER_ALIGN_CHUNKS: bool = os.getenv("SONIC_MASTER_ALIGN_CHUNKS", "true").lower() == "true"
    SONIC_MASTER_CHUNK_CONDITIONING: bool = os.getenv("SONIC_MASTER_CHUNK_CONDITIONING", "true").lower() == "true"
    SONIC_MASTER_CHUNK_RMS_MATCH: bool = os.getenv("SONIC_MASTER_CHUNK_RMS_MATCH", "false").lower() == "true"
    SONIC_MASTER_BLEND_COMPENSATION: bool = os.getenv("SONIC_MASTER_BLEND_COMPENSATION", "true").lower() == "true"
    SONIC_MASTER_TEMPLATE_PROMPTS: bool = os.getenv("SONIC_MASTER_TEMPLATE_PROMPTS", "true").lower() == "true"
    MASTER_SAFETY_ROLLOFF_HZ: float = float(os.getenv("MASTER_SAFETY_ROLLOFF_HZ", "20000"))
    BANDWIDTH_STAGE: str = os.getenv("BANDWIDTH_STAGE", "apollo").lower()
    SEPARATION_MODEL: str = os.getenv("SEPARATION_MODEL", "demucs").lower()
    QUALITY_SCORER: str = os.getenv("QUALITY_SCORER", "").lower()

    MAX_PARALLEL_CPU_WORKERS: int = 16

    ANNOUNCER_MIN_SAFE_ZONE_DURATION: float = float(os.getenv("ANNOUNCER_MIN_SAFE_ZONE_DURATION", "3.0"))
    ANNOUNCER_COOLDOWN_SECONDS: int = int(os.getenv("ANNOUNCER_COOLDOWN_SECONDS", "0"))
    ANNOUNCER_TRIGGER_PROBABILITY: float = float(os.getenv("ANNOUNCER_TRIGGER_PROBABILITY", "1.0"))
    ANNOUNCER_TRIGGER_EARLY_MS: int = int(os.getenv("ANNOUNCER_TRIGGER_EARLY_MS", "5000"))
    ANNOUNCER_PENDING_THRESHOLD_MS: int = int(os.getenv("ANNOUNCER_PENDING_THRESHOLD_MS", "60000"))

    DJ_AIRED_MEMORY_ENABLED: bool = os.getenv("DJ_AIRED_MEMORY_ENABLED", "false").lower() == "true"
    DJ_AIRED_MEMORY_ITEMS: int = int(os.getenv("DJ_AIRED_MEMORY_ITEMS", "4"))
    DJ_AIRED_MEMORY_TTL_S: int = int(os.getenv("DJ_AIRED_MEMORY_TTL_S", str(3 * 3600)))
    DJ_TRIVIA_PREFETCH_ENABLED: bool = os.getenv("DJ_TRIVIA_PREFETCH_ENABLED", "false").lower() == "true"
    DJ_TRIVIA_MAX_CHARS: int = int(os.getenv("DJ_TRIVIA_MAX_CHARS", "180"))
    DJ_TRIVIA_ARTIST_COOLDOWN_S: int = int(os.getenv("DJ_TRIVIA_ARTIST_COOLDOWN_S", "1800"))
    DJ_LISTENER_NOTES_ENABLED: bool = os.getenv("DJ_LISTENER_NOTES_ENABLED", "true").lower() == "true"
    DJ_LISTENER_NOTES_MAX_CHARS: int = int(os.getenv("DJ_LISTENER_NOTES_MAX_CHARS", "200"))
    DJ_WEATHER_CUES_ENABLED: bool = os.getenv("DJ_WEATHER_CUES_ENABLED", "true").lower() == "true"
    DJ_WEATHER_CUE_TTL_S: int = int(os.getenv("DJ_WEATHER_CUE_TTL_S", str(3 * 3600)))
    DJ_WEATHER_CUE_TEMP_DELTA_C: int = int(os.getenv("DJ_WEATHER_CUE_TEMP_DELTA_C", "4"))
    DJ_SKY_CUES_ENABLED: bool = os.getenv("DJ_SKY_CUES_ENABLED", "true").lower() == "true"
    DJ_SKY_CUE_LEAD_MIN: int = int(os.getenv("DJ_SKY_CUE_LEAD_MIN", "45"))
    DJ_LISTENER_STATS_ENABLED: bool = os.getenv("DJ_LISTENER_STATS_ENABLED", "true").lower() == "true"
    DJ_STATION_STATS_ENABLED: bool = os.getenv("DJ_STATION_STATS_ENABLED", "true").lower() == "true"
    DJ_STATS_CACHE_S: int = int(os.getenv("DJ_STATS_CACHE_S", "900"))
    DJ_GUEST_TIMEZONE_ENABLED: bool = os.getenv("DJ_GUEST_TIMEZONE_ENABLED", "true").lower() == "true"
    GUEST_LOCATION_ENABLED: bool = os.getenv("GUEST_LOCATION_ENABLED", "true").lower() == "true"
    GUEST_LOCATION_TTL_S: int = int(os.getenv("GUEST_LOCATION_TTL_S", str(6 * 3600)))
    GUEST_LOCATION_MAX_SESSIONS: int = int(os.getenv("GUEST_LOCATION_MAX_SESSIONS", "5000"))
    GUEST_LOCATION_MIN_INTERVAL_S: float = float(os.getenv("GUEST_LOCATION_MIN_INTERVAL_S", "20"))
    GUEST_LOCATION_MAX_UPDATES_PER_HOUR: int = int(os.getenv("GUEST_LOCATION_MAX_UPDATES_PER_HOUR", "30"))
    GUEST_LOCATION_MAX_SPEED_KMH: float = float(os.getenv("GUEST_LOCATION_MAX_SPEED_KMH", "1000"))
    GUEST_LOCATION_JUMP_CONFIRM_KM: float = float(os.getenv("GUEST_LOCATION_JUMP_CONFIRM_KM", "10"))
    GUEST_LOCATION_MAX_ACCURACY_M: float = float(os.getenv("GUEST_LOCATION_MAX_ACCURACY_M", "100000"))
    DJ_BANK_REPEAT_S: int = int(os.getenv("DJ_BANK_REPEAT_S", str(2 * 3600)))
    DJ_BANK_SHORT_WINDOW_S: float = float(os.getenv("DJ_BANK_SHORT_WINDOW_S", "8"))
    DJ_BANK_MEDIUM_WINDOW_S: float = float(os.getenv("DJ_BANK_MEDIUM_WINDOW_S", "20"))
    PULSE_AGENT_TIMEOUT_S: float = float(os.getenv("PULSE_AGENT_TIMEOUT_S", "20"))
    PULSE_AGENT_MAX_ROUNDS: int = int(os.getenv("PULSE_AGENT_MAX_ROUNDS", "3"))
    RADIO_FOR_YOU_INTERVAL_S: float = float(os.getenv("RADIO_FOR_YOU_INTERVAL_S", "7200"))
    RADIO_FOR_YOU_MAX_ROUNDS: int = int(os.getenv("RADIO_FOR_YOU_MAX_ROUNDS", "8"))
    RADIO_FOR_YOU_TIMEOUT_S: float = float(os.getenv("RADIO_FOR_YOU_TIMEOUT_S", "45"))
    DJ_ANNOUNCER_MENU_ENABLED: bool = os.getenv("DJ_ANNOUNCER_MENU_ENABLED", "true").lower() == "true"
    DJ_ANNOUNCER_PULSE_KINDS: list = [k.strip() for k in os.getenv(
        "DJ_ANNOUNCER_PULSE_KINDS", "event,place,community,news,chart,trend").split(",") if k.strip()]

    RADIO_MODE_ENABLED: bool = os.getenv("RADIO_MODE_ENABLED", "true").lower() == "true"
    RADIO_SEGMENTS_DISABLED: frozenset = frozenset(
        s.strip().lower() for s in os.getenv("RADIO_SEGMENTS_DISABLED", "").split(",") if s.strip()
    )
    RADIO_TICK_S: float = float(os.getenv("RADIO_TICK_S", "2"))
    RADIO_PREPARE_LEAD_S: float = float(os.getenv("RADIO_PREPARE_LEAD_S", "120"))
    RADIO_NEWS_MINUTE: int = int(os.getenv("RADIO_NEWS_MINUTE", "0"))
    RADIO_CITY_MINUTE: int = int(os.getenv("RADIO_CITY_MINUTE", "30"))
    RADIO_CLOCK_EARLY_S: float = float(os.getenv("RADIO_CLOCK_EARLY_S", "90"))
    RADIO_CLOCK_LATE_S: float = float(os.getenv("RADIO_CLOCK_LATE_S", "900"))
    RADIO_FEATURE_EARLY_S: float = float(os.getenv("RADIO_FEATURE_EARLY_S", "60"))
    RADIO_FEATURE_YIELD_S: float = float(os.getenv("RADIO_FEATURE_YIELD_S", "600"))
    RADIO_FEATURE_INTERVALS_MIN: tuple = tuple(
        int(v) for v in os.getenv("RADIO_FEATURE_INTERVALS_MIN", "15,20,30").split(",") if v.strip().isdigit()
    )
    RADIO_DEFAULT_FEATURE_INTERVAL_MIN: int = int(os.getenv("RADIO_DEFAULT_FEATURE_INTERVAL_MIN", "20"))
    RADIO_MIN_GAP_S: float = float(os.getenv("RADIO_MIN_GAP_S", "300"))
    RADIO_CONVERSATION_QUIET_S: float = float(os.getenv("RADIO_CONVERSATION_QUIET_S", "60"))
    RADIO_READY_TIMEOUT_S: float = float(os.getenv("RADIO_READY_TIMEOUT_S", "240"))
    RADIO_MAX_RENDER_ATTEMPTS: int = int(os.getenv("RADIO_MAX_RENDER_ATTEMPTS", "2"))
    RADIO_ON_AIR_MAX_S: float = float(os.getenv("RADIO_ON_AIR_MAX_S", "180"))
    RADIO_WRITE_TIMEOUT_S: float = float(os.getenv("RADIO_WRITE_TIMEOUT_S", "60"))
    RADIO_SEGMENT_MAX_TOKENS: int = int(os.getenv("RADIO_SEGMENT_MAX_TOKENS", "8192"))
    RADIO_MAX_SCRIPTS_PER_HOUR: int = int(os.getenv("RADIO_MAX_SCRIPTS_PER_HOUR", "300"))
    RADIO_SHARED_SCRIPT_TTL_S: float = float(os.getenv("RADIO_SHARED_SCRIPT_TTL_S", "1500"))
    RADIO_AIRED_MEMORY_S: float = float(os.getenv("RADIO_AIRED_MEMORY_S", str(6 * 3600)))
    RADIO_ANNOUNCER_QUIET_AFTER_S: float = float(os.getenv("RADIO_ANNOUNCER_QUIET_AFTER_S", "90"))
    RADIO_IDLE_EVICT_S: float = float(os.getenv("RADIO_IDLE_EVICT_S", "900"))
    RADIO_BEDS_ENABLED: bool = os.getenv("RADIO_BEDS_ENABLED", "true").lower() == "true"
    RADIO_BED_TARGET_LUFS: float = float(os.getenv("RADIO_BED_TARGET_LUFS", "-20"))
    RADIO_BED_MAX_BYTES: int = int(os.getenv("RADIO_BED_MAX_BYTES", str(25 * 1024 * 1024)))

    STINGS_ENABLED: bool = os.getenv("STINGS_ENABLED", "true").lower() == "true"
    STINGS_OUTSIDE_RADIO_MODE: bool = os.getenv("STINGS_OUTSIDE_RADIO_MODE", "true").lower() == "true"
    STINGS_TYPES_DISABLED: frozenset = frozenset(
        s.strip().lower() for s in os.getenv("STINGS_TYPES_DISABLED", "").split(",") if s.strip()
    )
    STINGS_DIR: Path = Path(os.getenv("STINGS_DIR") or str(CATALOG_DIR / "stings"))
    STATION_AUDIO_DIR: Path = Path(os.getenv("STATION_AUDIO_DIR") or str(TTS_ENGINE_DATA_DIR / "station_audio"))
    STATION_NAME_SPOKEN: str = os.getenv("STATION_NAME_SPOKEN", "Play Air")
    STATION_VOICE_VERSION: str = os.getenv("STATION_VOICE_VERSION", "1")
    STATION_VOICE_SEED: int = int(os.getenv("STATION_VOICE_SEED", "7100"))
    STATION_VOICE_TARGET_LUFS: float = float(os.getenv("STATION_VOICE_TARGET_LUFS", "-18"))
    STATION_PROCESS_MIX: float = float(os.getenv("STATION_PROCESS_MIX", "0.1"))
    STINGS_TARGET_LUFS: float = float(os.getenv("STINGS_TARGET_LUFS", "-16"))
    STINGS_BED_UNDER_VOICE_DB: float = float(os.getenv("STINGS_BED_UNDER_VOICE_DB", "-9"))
    STINGS_SHORT_WINDOW_S: float = float(os.getenv("STINGS_SHORT_WINDOW_S", "6"))
    STINGS_MIN_WINDOW_S: float = float(os.getenv("STINGS_MIN_WINDOW_S", "1.5"))
    STINGS_ROTATION_N: int = int(os.getenv("STINGS_ROTATION_N", "4"))
    STINGS_MIN_GAP_S: float = float(os.getenv("STINGS_MIN_GAP_S", "600"))
    STINGS_MAX_GAP_S: float = float(os.getenv("STINGS_MAX_GAP_S", "900"))
    STINGS_FIRST_DELAY_S: float = float(os.getenv("STINGS_FIRST_DELAY_S", "240"))
    STINGS_CONVERSATION_QUIET_S: float = float(os.getenv("STINGS_CONVERSATION_QUIET_S", "30"))
    STINGS_TIME_CHECK_MIN_INTERVAL_S: float = float(os.getenv("STINGS_TIME_CHECK_MIN_INTERVAL_S", "900"))
    STINGS_TIME_CHECK_MAX_INTERVAL_S: float = float(os.getenv("STINGS_TIME_CHECK_MAX_INTERVAL_S", "1800"))
    STINGS_TIME_CHECK_ROUND_MINUTES: int = int(os.getenv("STINGS_TIME_CHECK_ROUND_MINUTES", "5"))
    STINGS_TRIGGER_EARLY_MS: int = int(os.getenv("STINGS_TRIGGER_EARLY_MS", "300"))
    STINGS_BUILD_LEAD_MS: int = int(os.getenv("STINGS_BUILD_LEAD_MS", "1500"))
    STINGS_ID_NO_REPEAT: int = int(os.getenv("STINGS_ID_NO_REPEAT", "8"))
    STINGS_BREAK_LEAD_IN_PROBABILITY: float = float(os.getenv("STINGS_BREAK_LEAD_IN_PROBABILITY", "0.5"))
    STINGS_MIDTRACK_ENABLED: bool = os.getenv("STINGS_MIDTRACK_ENABLED", "true").lower() == "true"
    STINGS_MIDTRACK_MIN_INTERVAL_S: float = float(os.getenv("STINGS_MIDTRACK_MIN_INTERVAL_S", "1200"))
    STINGS_MIDTRACK_PROBABILITY: float = float(os.getenv("STINGS_MIDTRACK_PROBABILITY", "0.35"))
    STINGS_MIDTRACK_MIN_WINDOW_S: float = float(os.getenv("STINGS_MIDTRACK_MIN_WINDOW_S", "4"))
    STINGS_MIDTRACK_MAX_LEN_S: float = float(os.getenv("STINGS_MIDTRACK_MAX_LEN_S", "2.5"))
    STINGS_MIDTRACK_CLEAR_S: float = float(os.getenv("STINGS_MIDTRACK_CLEAR_S", "120"))
    STINGS_MIDTRACK_VOICE_MAX_LEN_S: float = float(os.getenv("STINGS_MIDTRACK_VOICE_MAX_LEN_S", "4.5"))
    REVIEW_STINGS_ENABLED: bool = os.getenv("REVIEW_STINGS_ENABLED", "true").lower() == "true"
    REVIEW_STINGS_PROBABILITY: float = float(os.getenv("REVIEW_STINGS_PROBABILITY", "0.7"))
    REVIEW_STINGS_MIN_INTERVAL_S: float = float(os.getenv("REVIEW_STINGS_MIN_INTERVAL_S", "240"))
    REVIEW_STINGS_REPEAT_S: float = float(os.getenv("REVIEW_STINGS_REPEAT_S", "21600"))
    REVIEW_STINGS_MAX_LEN_S: float = float(os.getenv("REVIEW_STINGS_MAX_LEN_S", "6"))
    REVIEW_STINGS_DUCK_S: float = float(os.getenv("REVIEW_STINGS_DUCK_S", "1.5"))
    STINGS_PRERENDER_ENABLED: bool = os.getenv("STINGS_PRERENDER_ENABLED", "true").lower() == "true"
    STINGS_PRERENDER_DELAY_S: float = float(os.getenv("STINGS_PRERENDER_DELAY_S", "90"))
    STINGS_PRERENDER_SPACING_S: float = float(os.getenv("STINGS_PRERENDER_SPACING_S", "2"))
    STINGS_VERIFY_NUMBERS: bool = os.getenv("STINGS_VERIFY_NUMBERS", "true").lower() == "true"
    STINGS_RENDER_MAX_ATTEMPTS: int = int(os.getenv("STINGS_RENDER_MAX_ATTEMPTS", "3"))
    STINGS_CITY_LINES_ENABLED: bool = os.getenv("STINGS_CITY_LINES_ENABLED", "true").lower() == "true"
    STINGS_CITY_RENDERS_PER_HOUR: int = int(os.getenv("STINGS_CITY_RENDERS_PER_HOUR", "12"))
    STINGS_SFX_TITLES: tuple = tuple(
        t.strip().lower() for t in os.getenv(
            "STINGS_SFX_TITLES",
            "music transition sound effect,DJ transitions sound,transitioning to new track,"
            "traffic report whoosh,tuning knobs"
        ).split(",") if t.strip()
    )
    STINGS_SFX_PIPS_TITLE: str = os.getenv("STINGS_SFX_PIPS_TITLE", "top of the hour beeps").strip().lower()

    ENABLE_FILE_LOGGING: bool = os.getenv("ENABLE_FILE_LOGGING", "True").lower() == "true"
    LOG_VERBOSE: str = os.getenv("LOG_VERBOSE", "")


    STRIPE_SECRET_KEY: str = os.getenv("STRIPE_SECRET_KEY", "")
    STRIPE_WEBHOOK_SECRET: str = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    STRIPE_PRICE_ID: str = os.getenv("STRIPE_PRICE_ID", "")
    STRIPE_ADDITIONAL_PRICE_IDS: frozenset = frozenset(
        p.strip() for p in os.getenv("STRIPE_ADDITIONAL_PRICE_IDS", "").split(",") if p.strip()
    )
    STRIPE_PORTAL_CONFIGURATION_ID: str = os.getenv("STRIPE_PORTAL_CONFIGURATION_ID", "")
    STRIPE_APP_TAG: str = "plair"
    STRIPE_PRICE_LABEL: str = os.getenv("STRIPE_PRICE_LABEL", "US$4.99/mo")
    STRIPE_WEBHOOK_TOLERANCE_S: int = 300
    PREMIUM_MONTHLY_GENERATIONS: int = int(os.getenv("PREMIUM_MONTHLY_GENERATIONS", "50"))
    FREE_GENERATION_LIMIT: int = int(os.getenv("FREE_GENERATION_LIMIT", "5"))
    FREE_GENERATION_PERIOD: str = os.getenv("FREE_GENERATION_PERIOD", "month").strip().lower()
    GENERATION_USAGE_PATH: Path = Path(os.getenv("GENERATION_USAGE_PATH", str(BASE_DIR / "data" / "generation_usage.json")))

    YOUTUBE_CLIENT_ID: str = os.getenv("YOUTUBE_CLIENT_ID", "")
    YOUTUBE_CLIENT_SECRET: str = os.getenv("YOUTUBE_CLIENT_SECRET", "")
    YOUTUBE_API_KEY: str = os.getenv("YOUTUBE_API_KEY", "")

    GOOGLE_PLACES_API_KEY: str = os.getenv("GOOGLE_PLACES_API_KEY", "")
    WEATHER_API_KEY: str = os.getenv("WEATHER_API_KEY", "")
    TICKETMASTER_API_KEY: str = os.getenv("TICKETMASTER_API_KEY", "")

    SUNO_API_KEY: str = os.getenv("SUNO_API_KEY", "")
    SUNO_BASE_URL: str = os.getenv("SUNO_BASE_URL", "https://api.sunoapi.org/api/v1")
    SUNO_CALLBACK_URL: str = os.getenv("SUNO_CALLBACK_URL", "https://example.com/callback")
    SUNO_POLL_INTERVAL: int = int(os.getenv("SUNO_POLL_INTERVAL", "5"))
    SUNO_MAX_WAIT: int = int(os.getenv("SUNO_MAX_WAIT", "600"))

    @classmethod
    def load_api_key_from_file(cls, key_name: str) -> str:
        """Returns the named API key from environment (loaded via .env at startup)."""
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

        # TTS engine directories
        cls.TTS_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        cls.PARALANGUAGE_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        cls.BREATH_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        cls.AUDIO_EFFECT_DIR.mkdir(parents=True, exist_ok=True)
        cls.STUDIO_AUDIO_DIR.mkdir(parents=True, exist_ok=True)

        # Cache directories
        cls.QUERY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cls.CONTEXT_ROUTING_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cls.USER_CONTENT_QUERY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cls.PROMPT_DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        cls.LLM_RESULT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cls.YOUTUBE_CLIPS_DIR.mkdir(parents=True, exist_ok=True)

settings = Settings()
settings.ensure_directories()

if len(settings.JWT_SECRET_KEY) < 32 or settings.JWT_SECRET_KEY.startswith(("dev-secret", "generate")):
    raise RuntimeError("JWT_SECRET_KEY must be set in .env to a random value of at least 32 characters")