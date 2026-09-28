from sqlalchemy import Column, Integer, BigInteger, String, Date, DateTime, ForeignKey, Enum, UniqueConstraint, Index, Boolean, Text, Float, LargeBinary
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime, timezone
import enum

# Use timezone-aware timestamps for PostgreSQL
def utc_now():
    return datetime.now(timezone.utc)

Base = declarative_base()

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    audio_quality = Column(String, default="auto", nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: utc_now())

    persona = Column(Text, nullable=True)
    profile = Column(Text, nullable=True)
    shoutout_interests = Column(Text, nullable=True)
    profile_picture = Column(String, nullable=True)  # filename of profile picture (e.g., "profile.jpg")
    engagements_since_last_update = Column(Integer, default=0)

    location = Column(String, nullable=True)
    latitude = Column(String, nullable=True)
    longitude = Column(String, nullable=True)
    timezone = Column(String, nullable=True)

    tts_muted = Column(Boolean, default=False)
    notifications_muted = Column(Boolean, default=False)
    dark_mode = Column(Boolean, default=True)
    fps_enabled = Column(Boolean, default=False)
    video_clips_enabled = Column(Boolean, default=False)  # Enable video clips in visuals and shared videos
    visual_quality = Column(String, default="high")  # high, medium, low - controls AudioReactiveCanvas shader complexity

    subscribed = Column(Boolean, default=False, nullable=False)
    tier = Column(String, default="basic", nullable=False)  # basic, premium
    stripe_customer_id = Column(String, unique=True, nullable=True)
    stripe_subscription_id = Column(String, nullable=True)
    subscription_status = Column(String, nullable=True)
    current_period_end = Column(DateTime(timezone=True), nullable=True)

    last_login = Column(DateTime(timezone=True), nullable=True)

class StripeWebhookEvent(Base):
    __tablename__ = "stripe_webhook_events"

    id = Column(String, primary_key=True)
    event_type = Column(String, nullable=False)
    received_at = Column(DateTime(timezone=True), default=lambda: utc_now(), nullable=False)

class UserDevice(Base):
    __tablename__ = "user_devices"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    device_id = Column(String, nullable=False, index=True)
    auto_name = Column(String, nullable=False)
    display_name = Column(String, nullable=True)
    device_type = Column(String, nullable=False)
    is_active = Column(Boolean, default=False, nullable=False)
    last_active = Column(DateTime(timezone=True), default=lambda: utc_now(), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: utc_now())

    __table_args__ = (
        UniqueConstraint('user_id', 'device_id', name='unique_user_device'),
    )

class PreferenceType(enum.Enum):
    LIKE = "like"
    SUPER_LIKE = "super_like"
    BAN = "ban"

class TrackPreference(Base):
    __tablename__ = "track_preferences"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    track_id = Column(String, nullable=False)
    preference_type = Column(Enum(PreferenceType), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: utc_now())

    __table_args__ = (
        UniqueConstraint('user_id', 'track_id', name='unique_user_track'),
    )

class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    user_input = Column(Text, nullable=True)
    bot_response = Column(Text, nullable=True)
    commands = Column(Text, nullable=True)
    info_message = Column(Text, nullable=True)
    warning_message = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    audio_file_path = Column(String, nullable=True)
    message_type = Column(String, nullable=False, default='interactive', server_default='interactive')
    timestamp = Column(DateTime(timezone=True), default=lambda: utc_now(), nullable=False, index=True)

class WeatherData(Base):
    __tablename__ = "weather_data"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True, index=True)
    description = Column(Text, nullable=False)  # Weather description for AI prompts
    timestamp = Column(DateTime(timezone=True), default=lambda: utc_now(), nullable=False)

class PlayEvent(Base):
    __tablename__ = "play_events"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    track_id = Column(String, nullable=False, index=True)
    device_id = Column(String, nullable=True)

    started_at = Column(DateTime(timezone=True), nullable=False, index=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    duration_ms = Column(Integer, nullable=True)
    completion_pct = Column(Float, nullable=True)

    event_type = Column(String, nullable=False)
    skip_reason = Column(String, nullable=True)
    session_id = Column(String, nullable=True)

class TrackAnalytics(Base):
    __tablename__ = "track_analytics"

    track_id = Column(String, primary_key=True)

    total_plays = Column(Integer, default=0, nullable=False)
    unique_listeners = Column(Integer, default=0, nullable=False)
    last_played = Column(DateTime(timezone=True), nullable=True)

    like_count = Column(Integer, default=0, nullable=False)
    superlike_count = Column(Integer, default=0, nullable=False)
    ban_count = Column(Integer, default=0, nullable=False)

    avg_completion_pct = Column(Float, default=0.0, nullable=False)
    skip_count = Column(Integer, default=0, nullable=False)
    skip_rate = Column(Float, default=0.0, nullable=False)

    daily_plays = Column(Text, nullable=True)
    weekly_plays = Column(Text, nullable=True)

    popularity_score = Column(Float, default=0.0, nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), default=lambda: utc_now(), nullable=False)

class ShoutoutPreferenceType(enum.Enum):
    SUPER_LIKE = "super_like"
    LIKE = "like"
    BAN = "ban"

class ShoutoutPreference(Base):
    __tablename__ = "shoutout_preferences"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    shoutout_id = Column(String, nullable=False)  # Format: "user_id_timestamp"
    preference_type = Column(Enum(ShoutoutPreferenceType), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: utc_now())

    __table_args__ = (
        UniqueConstraint('user_id', 'shoutout_id', name='unique_user_shoutout'),
    )

class ShoutoutAnalytics(Base):
    __tablename__ = "shoutout_analytics"

    shoutout_id = Column(String, primary_key=True)  # Format: "user_id_timestamp"

    total_plays = Column(Integer, default=0, nullable=False)
    unique_listeners = Column(Integer, default=0, nullable=False)
    last_played = Column(DateTime(timezone=True), nullable=True)

    super_like_count = Column(Integer, default=0, nullable=False)
    like_count = Column(Integer, default=0, nullable=False)
    ban_count = Column(Integer, default=0, nullable=False)

    avg_completion_pct = Column(Float, default=0.0, nullable=False)
    skip_count = Column(Integer, default=0, nullable=False)
    skip_rate = Column(Float, default=0.0, nullable=False)

    daily_plays = Column(Text, nullable=True)
    weekly_plays = Column(Text, nullable=True)

    popularity_score = Column(Float, default=0.0, nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), default=lambda: utc_now(), nullable=False)


class AIUsageEvent(Base):
    __tablename__ = "ai_usage_events"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    created_at = Column(DateTime(timezone=True), default=lambda: utc_now(), nullable=False, index=True)
    subject_key = Column(String, nullable=False)
    subject_kind = Column(String, nullable=False)
    user_id = Column(Integer, nullable=True)
    session_id = Column(String, nullable=False, default="")
    category = Column(String, nullable=False)
    feature = Column(String, nullable=False)
    role = Column(String, nullable=False, default="")
    provider = Column(String, nullable=False, default="")
    model = Column(String, nullable=False, default="")
    prompt_tokens = Column(Integer, nullable=False, default=0)
    cached_tokens = Column(Integer, nullable=False, default=0)
    output_tokens = Column(Integer, nullable=False, default=0)
    reasoning_tokens = Column(Integer, nullable=False, default=0)
    cache_hit = Column(Boolean, nullable=False, default=False)
    error = Column(Boolean, nullable=False, default=False)
    cost_usd = Column(Float, nullable=False, default=0.0)
    saved_usd = Column(Float, nullable=False, default=0.0)
    gpu_seconds = Column(Float, nullable=False, default=0.0)
    audio_seconds = Column(Float, nullable=False, default=0.0)
    units = Column(Float, nullable=False, default=0.0)
    latency_ms = Column(Float, nullable=False, default=0.0)

    __table_args__ = (
        Index("ix_ai_usage_events_user_created", "user_id", "created_at"),
        Index("ix_ai_usage_events_session_created", "session_id", "created_at"),
    )


class AIUsageDaily(Base):
    __tablename__ = "ai_usage_daily"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    day = Column(Date, nullable=False, index=True)
    subject_key = Column(String, nullable=False)
    subject_kind = Column(String, nullable=False)
    user_id = Column(Integer, nullable=True, index=True)
    session_id = Column(String, nullable=False, default="")
    category = Column(String, nullable=False)
    feature = Column(String, nullable=False)
    role = Column(String, nullable=False, default="")
    provider = Column(String, nullable=False, default="")
    model = Column(String, nullable=False, default="")
    calls = Column(Integer, nullable=False, default=0)
    cache_hits = Column(Integer, nullable=False, default=0)
    errors = Column(Integer, nullable=False, default=0)
    prompt_tokens = Column(BigInteger, nullable=False, default=0)
    cached_tokens = Column(BigInteger, nullable=False, default=0)
    output_tokens = Column(BigInteger, nullable=False, default=0)
    reasoning_tokens = Column(BigInteger, nullable=False, default=0)
    cost_usd = Column(Float, nullable=False, default=0.0)
    saved_usd = Column(Float, nullable=False, default=0.0)
    gpu_seconds = Column(Float, nullable=False, default=0.0)
    audio_seconds = Column(Float, nullable=False, default=0.0)
    units = Column(Float, nullable=False, default=0.0)
    updated_at = Column(DateTime(timezone=True), default=lambda: utc_now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("day", "subject_key", "category", "feature", "role", "provider", "model",
                         name="uq_ai_usage_daily_bucket"),
        Index("ix_ai_usage_daily_subject_day", "subject_key", "day"),
    )

class RegionalRegion(Base):
    __tablename__ = "regional_regions"

    key = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    country = Column(String, nullable=True)
    last_active_at = Column(DateTime(timezone=True), nullable=True)

class RegionalRefresh(Base):
    __tablename__ = "regional_refreshes"

    region_key = Column(String, primary_key=True)
    collector = Column(String, primary_key=True)
    refreshed_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String, nullable=True)
    item_count = Column(Integer, nullable=False, default=0)

class RegionalItem(Base):
    __tablename__ = "regional_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    region_key = Column(String, nullable=False, index=True)
    source = Column(String, nullable=False)
    kind = Column(String, nullable=False, index=True)
    external_id = Column(String, nullable=False)
    title = Column(String, nullable=False, default="")
    text = Column(Text, nullable=False, default="")
    tags = Column(Text, nullable=False, default="[]")
    starts_at = Column(DateTime(timezone=True), nullable=True, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    url = Column(String, nullable=True)
    attribution = Column(String, nullable=True)
    fetched_at = Column(DateTime(timezone=True), default=lambda: utc_now(), nullable=False)

    __table_args__ = (UniqueConstraint("region_key", "source", "external_id", name="uq_regional_items_source_id"),)


class PlaceCache(Base):
    __tablename__ = "place_cache"

    place_id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    type = Column(String, nullable=True)
    address = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    website = Column(String, nullable=True)
    rating = Column(Float, nullable=True)
    rating_count = Column(Integer, nullable=True)
    price_level = Column(String, nullable=True)
    opening_hours = Column(Text, nullable=True)
    latitude = Column(Float, nullable=False, index=True)
    longitude = Column(Float, nullable=False, index=True)
    tags = Column(Text, nullable=False, default="[]")
    fetched_at = Column(DateTime(timezone=True), default=lambda: utc_now(), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)


class AreaCache(Base):
    __tablename__ = "area_cache"

    namespace = Column(String, primary_key=True)
    cell = Column(String, primary_key=True)
    payload = Column(Text, nullable=False, default="{}")
    status = Column(String, nullable=False, default="ok")
    fetched_at = Column(DateTime(timezone=True), default=lambda: utc_now(), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)


class PlaceSearch(Base):
    __tablename__ = "place_searches"

    id = Column(Integer, primary_key=True, autoincrement=True)
    query_norm = Column(String, nullable=False, index=True)
    latitude = Column(Float, nullable=False, index=True)
    longitude = Column(Float, nullable=False, index=True)
    radius_m = Column(Integer, nullable=False)
    place_ids = Column(Text, nullable=False, default="[]")
    created_at = Column(DateTime(timezone=True), default=lambda: utc_now(), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)


class NewsItem(Base):
    __tablename__ = "news_items"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    item_key = Column(String, nullable=False, unique=True)
    title = Column(String, nullable=False)
    source = Column(String, nullable=False, default="")
    url = Column(String, nullable=False, default="")
    description = Column(Text, nullable=False, default="")
    published_at = Column(DateTime(timezone=True), nullable=True)
    country = Column(String, nullable=True, index=True)
    region_key = Column(String, nullable=True)
    tags = Column(Text, nullable=False, default="[]")
    embedding = Column(LargeBinary, nullable=True)
    first_seen_at = Column(DateTime(timezone=True), default=lambda: utc_now(), nullable=False)
    last_seen_at = Column(DateTime(timezone=True), default=lambda: utc_now(), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)


class NewsPull(Base):
    __tablename__ = "news_pulls"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    kind = Column(String, nullable=False)
    country = Column(String, nullable=False)
    region_key = Column(String, nullable=True)
    query = Column(String, nullable=False, default="")
    query_norm = Column(String, nullable=False, default="")
    period = Column(String, nullable=False, default="")
    embedding = Column(LargeBinary, nullable=True)
    item_ids = Column(Text, nullable=False, default="[]")
    ranked_ids = Column(Text, nullable=True)
    rank_source = Column(String, nullable=False, default="")
    ranked_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String, nullable=False, default="ok")
    fetched_at = Column(DateTime(timezone=True), default=lambda: utc_now(), nullable=False, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)

    __table_args__ = (Index("ix_news_pulls_lookup", "country", "kind", "query_norm", "fetched_at"),)


class NewsAired(Base):
    __tablename__ = "news_aired"

    subject = Column(String, primary_key=True)
    item_id = Column(BigInteger, primary_key=True)
    aired_at = Column(DateTime(timezone=True), default=lambda: utc_now(), nullable=False, index=True)


class ArtistBiography(Base):
    __tablename__ = "artist_biographies"

    name_key = Column(String, primary_key=True)
    artist_name = Column(String, nullable=False, default="")
    biography = Column(Text, nullable=False, default="")
    status = Column(String, nullable=False, default="found")
    fetched_at = Column(DateTime(timezone=True), default=lambda: utc_now(), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)


class UserRadioSettings(Base):
    __tablename__ = "user_radio_settings"

    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    settings = Column(Text, nullable=False, default="{}")
    updated_at = Column(DateTime(timezone=True), default=lambda: utc_now(), nullable=False)
