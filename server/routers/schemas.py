from typing import Optional, List
from pydantic import BaseModel, Field

from config import settings


class PlayRequest(BaseModel):
    track_id: Optional[str] = None

class SeekRequest(BaseModel):
    position_ms: int

class QueueAddRequest(BaseModel):
    track_ids: List[str]
    position: Optional[int] = None

class SearchRequest(BaseModel):
    query: str = Field(..., max_length=1000)
    n_results: Optional[int] = Field(10, ge=1, le=100)
    instrumental: Optional[bool] = None
    vocal_gender: Optional[str] = None
    use_ai_analysis: Optional[bool] = False

class ShoutoutSearchRequest(BaseModel):
    query: str = Field(..., max_length=1000)
    n_results: Optional[int] = Field(20, ge=1, le=100)
    use_ai_analysis: Optional[bool] = False

class GenerateRequest(BaseModel):
    user_request: Optional[str] = Field(None, max_length=5000)
    generation_type: str = "new"
    source_track_id: Optional[str] = Field(None, max_length=128)
    batch_count: int = Field(3, ge=1, le=5)

class RegisterRequest(BaseModel):
    username: str = Field(..., max_length=100)
    password: str = Field(..., max_length=256)

class LoginRequest(BaseModel):
    username: str = Field(..., max_length=100)
    password: str = Field(..., max_length=256)

class PreferenceRequest(BaseModel):
    preference_type: str

class AudioQualityRequest(BaseModel):
    audio_quality: str

class UsernameUpdateRequest(BaseModel):
    username: str

class ManageUserDataRequest(BaseModel):
    action: str

class UserProfileUpdate(BaseModel):
    location: Optional[str] = None
    latitude: Optional[str] = None
    longitude: Optional[str] = None
    timezone: Optional[str] = None
    tts_muted: Optional[bool] = None
    dark_mode: Optional[bool] = None
    fps_enabled: Optional[bool] = None
    video_clips_enabled: Optional[bool] = None
    visual_quality: Optional[str] = None

class DirectReplyUploadRequest(BaseModel):
    audio: str = Field(..., max_length=settings.MAX_BASE64_AUDIO_CHARS)

class ActivateDeviceRequest(BaseModel):
    device_id: Optional[str] = None

class RenameDeviceRequest(BaseModel):
    new_name: str

class DJTalkRequest(BaseModel):
    audio: Optional[str] = Field(None, max_length=settings.MAX_BASE64_AUDIO_CHARS)
    text: Optional[str] = Field(None, max_length=4000)
    context: Optional[str] = "generic_talk"
    voice_name: Optional[str] = None
