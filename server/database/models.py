from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Enum, UniqueConstraint, Boolean, Text, Float
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

    last_login = Column(DateTime(timezone=True), nullable=True)

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