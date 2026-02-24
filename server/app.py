import asyncio
import platform
import aiofiles
import json
import time

if platform.system() == "Windows":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Depends, Header, File, UploadFile, Request, \
    Response, Form
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from contextlib import asynccontextmanager
from typing import Optional, List
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from services.log_service import start_log_worker
from services.suno_service import SunoService
from services.suno_metadata_service import MusicMetadata
from services.suno_prompt_service import MusicPromptService
from services.suno_service_orchestrator import SunoServiceOrchestrator
from services.audio_transcoding_service import AudioTranscodingService
from services.catalog_database_service import CatalogDatabaseService
from services.user_content_database_service import UserContentDatabaseService
from services.playback_service import PlaybackService
from services.catalog_vector_database_service import CatalogVectorDatabaseService
from services.catalog_vector_search_service import CatalogVectorSearchService
from services.catalog_vector_search_prompt_cache_service import CatalogVectorSearchPromptCacheService
from services.user_content_vector_database_service import UserContentVectorDatabaseService
from services.user_content_vector_search_service import UserContentVectorSearchService
from services.user_content_vector_search_prompt_cache_service import UserContentVectorSearchPromptCacheService
from services.user_content_speech_enhancement_service import UserContentSpeechEnhancementService
from services.suno_generation_queue_service import SunoGenerationQueueService
from services.suno_enriched_metadata_service import EnrichedMetadataService
from services.whisper_dual_service import whisper_dual_service
from services.user_data_cache_service import user_data_cache
from services.rate_limit_service import rate_limit_service
from services.analytics_service import analytics_service
from services.profile_picture_service import profile_picture_service
from services.preferences_service import preferences_service
from services.youtube_clip_service import get_youtube_clip_service

from services_radio.tts_vector_db_service import VectorDBService
from services_radio.tts_processing_service import AudioProcessingService
from services_radio.tts_generation_service import TTSGenerationService
from services_radio.tts_stream_planner import TTSStreamPlanner
from services_radio.tts_queue_manager import TTSQueueManager
from services_radio.tts_broadcast_service import AudioBroadcastService
from services_radio.dj_prompt_service import DJPromptService
from services_radio.dj_prompt_system_service import DJPromptSystemService
from services_radio.dj_command_executor import CommandExecutorService
from services_radio.background_tasks_service import BackgroundTasksService
from services_radio.announcer_service import AnnouncerService
from services_radio.stripe_service import stripe_router
from services_radio.external_web_service import WebService
from services_radio.external_news_service import NewsService
from services_radio.external_location_service import LocationService
from services_radio.external_events_service import EventsService
from services.ai_service import AIService
from services.websocket_service import WebSocketService
from services.device_management_service import DeviceManagementService
from services.media_streaming_service import MediaStreamingService
from services.user_profile_service import UserProfileService
from services.opengraph_service import OpenGraphService
from services.human_metadata_extraction_service import HumanMetadataExtractionService
from services.human_music_upload_service import HumanMusicUploadService
from services.source_quality_analysis_service import SourceQualityAnalysisService
from services.embedded_artwork_service import EmbeddedArtworkService
from services.audio_master_service import AudioMasterService
from services.audio_features_service import AudioFeaturesService
from services.audio_lyrical_timestamp_service import LyricalTimestampService
from services.audio_sonic_master_service import SonicMasterService
from services.suno_artwork_enrichment_service import ArtworkEnrichmentService
from services.track_artwork_service import track_artwork_service
from services.artwork_generation_service import ArtworkGenerationService
import models_global
from services import log_service
from services import auth_service
from database import get_db, init_db, User, WeatherData, engine, AsyncSessionLocal
from services_radio.conversation_service import conversation_service, get_conversation_history, \
    save_conversation_to_database, \
    save_temp_conversation
from sqlalchemy import select
from config import settings

ai_service: Optional[AIService] = None
suno_service: Optional[SunoService] = None
metadata_service: Optional[MusicMetadata] = None
prompt_service: Optional[MusicPromptService] = None
orchestrator: Optional[SunoServiceOrchestrator] = None
transcoding_service: Optional[AudioTranscodingService] = None
catalog_service: Optional[CatalogDatabaseService] = None
user_content_service: Optional[UserContentDatabaseService] = None
playback_service: Optional[PlaybackService] = None
vector_search_service: Optional[CatalogVectorSearchService] = None
vector_search_prompt_cache_service: Optional[CatalogVectorSearchPromptCacheService] = None
suno_generation_queue_service: Optional[SunoGenerationQueueService] = None

tts_vector_db_service: Optional[VectorDBService] = None
tts_audio_processing_service: Optional[AudioProcessingService] = None
tts_generation_service: Optional[TTSGenerationService] = None
tts_stream_planner: Optional[TTSStreamPlanner] = None
tts_queue_manager: Optional[TTSQueueManager] = None
tts_broadcast_service: Optional[AudioBroadcastService] = None
dj_prompt_service: Optional[DJPromptService] = None
dj_prompt_system_service: Optional[DJPromptSystemService] = None
command_executor: Optional[CommandExecutorService] = None
user_content_speech_enhancement_service: Optional[UserContentSpeechEnhancementService] = None
user_content_vector_search_service: Optional[UserContentVectorSearchService] = None
user_content_prompt_cache_service: Optional[UserContentVectorSearchPromptCacheService] = None
background_tasks_service: Optional[BackgroundTasksService] = None
announcer_service: Optional[AnnouncerService] = None

web_service: Optional[WebService] = None
news_service: Optional[NewsService] = None
location_service: Optional[LocationService] = None
events_service: Optional[EventsService] = None
websocket_service: Optional[WebSocketService] = None
device_management_service: Optional[DeviceManagementService] = None
media_streaming_service: Optional[MediaStreamingService] = None
user_profile_service: Optional[UserProfileService] = None
opengraph_service: Optional[OpenGraphService] = None
human_metadata_extraction_service: Optional[HumanMetadataExtractionService] = None
human_music_upload_service: Optional[HumanMusicUploadService] = None

async def safe_background_task(coro, task_name="background_task"):
    try:
        await coro
    except Exception as e:
        log_service.error(f"{task_name} failed with exception: {e}")
        import traceback
        log_service.error(f"Traceback: {traceback.format_exc()}")

@asynccontextmanager
async def lifespan(_app: FastAPI):
    global ai_service, suno_service
    global metadata_service, prompt_service, orchestrator, transcoding_service
    global catalog_service, user_content_service, playback_service, vector_search_service, vector_search_prompt_cache_service, suno_generation_queue_service
    global tts_vector_db_service, tts_audio_processing_service, tts_generation_service
    global tts_stream_planner, tts_queue_manager, tts_broadcast_service, command_executor, announcer_service
    global web_service, news_service, location_service, events_service, background_tasks_service
    global user_content_speech_enhancement_service, user_content_vector_search_service, user_content_prompt_cache_service, dj_prompt_service, dj_prompt_system_service
    global websocket_service, device_management_service, media_streaming_service, user_profile_service, opengraph_service

    await start_log_worker()

    init_db()
    log_service.success("✓ Database initialized")

    websocket_service = WebSocketService()
    await websocket_service.initialize()

    device_management_service = DeviceManagementService()
    await device_management_service.initialize()

    media_streaming_service = MediaStreamingService()
    await media_streaming_service.initialize()

    user_profile_service = UserProfileService()
    await user_profile_service.initialize()
    user_profile_service.set_websocket_service(websocket_service)

    opengraph_service = OpenGraphService(base_url=settings.BASE_URL if hasattr(settings, 'BASE_URL') else "https://plair.live")
    log_service.success("✓ OpenGraph service initialized")

    await rate_limit_service.initialize()
    log_service.success("✓ Rate limiting initialized")

    transcoding_service = AudioTranscodingService()
    await transcoding_service.initialize()
    log_service.success("✓ FFmpeg initialized")

    await whisper_dual_service.initialize()

    metadata_service = MusicMetadata()
    await metadata_service.initialize()

    catalog_service = CatalogDatabaseService()
    await catalog_service.initialize()

    user_content_service = UserContentDatabaseService()
    await user_content_service.initialize()
    log_service.success("✓ User content database service initialized")

    ai_service = AIService()
    await ai_service.initialize()

    enriched_metadata_service = EnrichedMetadataService(ai_service)  # type: ignore
    await enriched_metadata_service.initialize()

    global human_metadata_extraction_service, human_music_upload_service
    human_metadata_extraction_service = HumanMetadataExtractionService()
    await human_metadata_extraction_service.initialize()

    source_quality_service = SourceQualityAnalysisService()
    await source_quality_service.initialize()

    embedded_artwork_service = EmbeddedArtworkService()
    await embedded_artwork_service.initialize()

    master_service = AudioMasterService()
    await master_service.initialize()

    features_service = AudioFeaturesService()
    await features_service.initialize()

    artwork_enrichment_service = ArtworkEnrichmentService()
    await artwork_enrichment_service.initialize()

    artwork_generation_service = ArtworkGenerationService()
    await artwork_generation_service.initialize()

    lyric_timestamp_service = LyricalTimestampService()
    await lyric_timestamp_service.initialize()

    sonic_master_service = SonicMasterService()
    await sonic_master_service.initialize()

    human_music_upload_service = HumanMusicUploadService()
    await human_music_upload_service.initialize(
        metadata_extraction_service=human_metadata_extraction_service,
        transcoding_service=transcoding_service,
        apollo_service=None,
        catalog_db_service=catalog_service,
        vector_db_service=None,
        quality_analysis_service=source_quality_service,
        embedded_artwork_service=embedded_artwork_service,
        master_service=master_service,
        features_service=features_service,
        artwork_enrichment_service=artwork_enrichment_service,
        artwork_generation_service=artwork_generation_service,
        lyric_timestamp_service=lyric_timestamp_service,
        sonic_master_service=sonic_master_service
    )

    await track_artwork_service.initialize(
        artwork_enrichment_service=artwork_enrichment_service,
        catalog_db_service=catalog_service,
        artwork_generation_service=artwork_generation_service
    )

    log_service.success("✓ Human music upload services initialized (full pipeline enabled)")

    suno_service = SunoService()
    await suno_service.initialize()

    orchestrator = SunoServiceOrchestrator()
    await orchestrator.initialize()

    prompt_service = MusicPromptService(ai_service)
    await prompt_service.initialize()

    log_service.system("Initializing shared T5 encoder models...")
    await models_global.initialize_tts_models()
    log_service.system("✓ Shared T5 encoder loaded (used by music search & TTS)")

    log_service.system("Initializing catalog vector database service...")
    catalog_vector_db_service = CatalogVectorDatabaseService(catalog_service)
    await asyncio.to_thread(catalog_vector_db_service.load_initial_data)
    log_service.success("✓ Catalog vector database service initialized")

    log_service.system("Initializing catalog vector search prompt cache service...")
    try:
        vector_search_prompt_cache_service = CatalogVectorSearchPromptCacheService()
        await vector_search_prompt_cache_service.initialize(ai_service, catalog_vector_db_service)
        log_service.success("✓ Catalog vector search prompt cache service initialized")
    except Exception as e:
        log_service.error(f"❌ FATAL: Prompt cache service initialization failed: {e}")
        import traceback
        log_service.error(f"Traceback: {traceback.format_exc()}")
        raise

    log_service.system("Initializing context router service (Producer AI)...")
    try:
        from services_radio.context_router_service import context_router_service
        await context_router_service.initialize(ai_service, catalog_vector_db_service)  # type: ignore
        log_service.success("✓ Context router service initialized (Node system ready)")
    except Exception as e:
        log_service.error(f"❌ WARNING: Context router service initialization failed: {e}")
        log_service.error("Node-based DJ system will not be available")

    log_service.system("Initializing catalog vector search service...")
    try:
        vector_search_service = CatalogVectorSearchService(
            catalog_vector_db_service,
            catalog_service,
            vector_search_prompt_cache_service
        )
        log_service.success("✓ Catalog vector search service initialized")
    except Exception as e:
        log_service.error(f"❌ FATAL: Vector search service initialization failed: {e}")
        import traceback
        log_service.error(f"Traceback: {traceback.format_exc()}")
        raise

    catalog_service.set_vector_services(
        vector_db_service=catalog_vector_db_service,
        broadcast_callback=websocket_service.broadcast_content_updated
    )
    log_service.success("✓ Catalog service configured for instant vector updates")

    log_service.system("Initializing user content vector database service...")
    user_content_vector_db_service = None
    try:
        user_content_vector_db_service = UserContentVectorDatabaseService(user_content_service)
        await asyncio.to_thread(user_content_vector_db_service.load_initial_data)
        log_service.success("✓ User content vector database service initialized")

        log_service.system("Initializing user content prompt cache service...")
        user_content_prompt_cache_service = UserContentVectorSearchPromptCacheService()
        await user_content_prompt_cache_service.initialize(ai_service, user_content_vector_db_service)
        log_service.success("✓ User content prompt cache service initialized")

        log_service.system("Initializing user content vector search service...")
        user_content_vector_search_service = UserContentVectorSearchService(
            user_content_vector_db_service,
            user_content_service,
            user_content_prompt_cache_service
        )
        log_service.success("✓ User content vector search service initialized")

    except Exception as e:
        log_service.warning(f"⚠️  User content vector DB initialization failed: {e}")
        log_service.warning("User content vector search will not be available")
        user_content_vector_search_service = None

    log_service.system("Initializing TTS Vector DB service...")
    try:
        tts_vector_db_service = VectorDBService()
        log_service.success("✓ TTS Vector DB initialized")
    except Exception as e:
        log_service.error(f"❌ FATAL: TTS Vector DB initialization failed: {e}")
        import traceback
        log_service.error(f"Traceback: {traceback.format_exc()}")
        raise

    log_service.system("Initializing playback service...")
    try:
        playback_service = PlaybackService(catalog_service, vector_search_service)
        await playback_service.initialize()
        log_service.success("✓ Playback service initialized")
    except Exception as e:
        log_service.error(f"❌ FATAL: Playback service initialization failed: {e}")
        import traceback
        log_service.error(f"Traceback: {traceback.format_exc()}")
        raise

    suno_generation_queue_service = SunoGenerationQueueService()
    await suno_generation_queue_service.initialize(
        suno_service=suno_service,
        prompt_service=prompt_service,
        metadata_service=metadata_service,
        catalog_service=catalog_service,
        playback_service=playback_service,
        vector_search_service=vector_search_service,
        orchestrator=orchestrator,
        enriched_metadata_service=enriched_metadata_service
    )

    async def generation_notification_callback(sess_id: str, message: dict):
        assert websocket_service is not None
        await websocket_service.broadcast_to_session(sess_id, message)

    suno_generation_queue_service.set_notification_callback(generation_notification_callback)

    await user_data_cache.initialize()
    log_service.success("✓ Preferences cache initialized")

    await analytics_service.initialize()
    await analytics_service.start_background_tasks()
    await analytics_service.initialize_from_catalog()
    log_service.success("✓ Analytics service initialized")

    log_service.system("Loading vector embeddings and building Annoy indexes...")
    assert tts_vector_db_service is not None
    db_results = await asyncio.to_thread(tts_vector_db_service.load_initial_data)

    empty_databases = [db_type for db_type, (data, _) in db_results.items() if len(data) == 0]

    if empty_databases:
        log_service.warning(f"Empty databases detected: {', '.join(empty_databases)}")
        log_service.system("🔨 Migrating TTS embeddings from disk (this will block startup)...")

        from services_radio.tts_database_migration_service import TTSDatabaseMigrationService
        migration_service = TTSDatabaseMigrationService()

        try:
            await asyncio.to_thread(
                migration_service.migrate_all_databases,
                validate_only=False,
                force_rebuild=False
            )
            log_service.success("✓ TTS database migration completed")

            log_service.system("Reloading vector embeddings after migration...")
            db_results = await asyncio.to_thread(tts_vector_db_service.load_initial_data)
        except Exception as e:
            log_service.error(f"❌ FATAL: TTS database migration failed: {e}")
            import traceback
            log_service.error(f"Traceback: {traceback.format_exc()}")
            raise

    for db_type, (data, voice_counts) in db_results.items():
        total_items = len(data)
        log_service.success(f"  ✓ {db_type}: {total_items} embeddings loaded")
        for voice, count in voice_counts.items():
            log_service.system(f"    - {voice}: {count} items")
    log_service.success("✓ Vector data loaded and Annoy indexes built")

    tts_audio_processing_service = AudioProcessingService()
    log_service.success("✓ TTS Audio Processing initialized")

    dj_prompt_system_service = DJPromptSystemService(gemini_service=ai_service)
    log_service.success("✓ DJ Prompt System Service initialized")

    tts_generation_service = TTSGenerationService(
        tts_vector_db_service,
        tts_audio_processing_service,
        dj_prompt_system_service
    )
    log_service.success("✓ TTS Generation service initialized")

    await tts_generation_service.warm_breath_caches()

    tts_stream_planner = TTSStreamPlanner()
    log_service.success("✓ TTS Stream Planner initialized")

    class WebSocketAdapter:
        async def emit(self, event: str, msg_data: dict, room: Optional[str] = None):
            assert websocket_service is not None
            message = {"type": event, "data": msg_data}
            if room:
                await websocket_service.broadcast_to_session(room, message)

    tts_broadcast_service = AudioBroadcastService(WebSocketAdapter())
    log_service.success("✓ TTS Broadcast service initialized")

    tts_queue_manager = TTSQueueManager(
        tts_vector_db_service,
        tts_audio_processing_service,
        tts_generation_service,
        tts_broadcast_service,
        tts_stream_planner
    )
    await tts_queue_manager.start()
    log_service.success("✓ TTS Queue Manager started")

    web_service = WebService()
    news_service = NewsService(ai_service)
    location_service = LocationService()
    events_service = EventsService()
    log_service.success("✓ External services initialized")

    user_content_speech_enhancement_service = UserContentSpeechEnhancementService()
    await user_content_speech_enhancement_service.initialize()
    log_service.success("✓ User Content Enhancement Service initialized")

    dj_prompt_service = DJPromptService(
        gemini_service=ai_service,
        vector_db_service=tts_vector_db_service,
        async_session_maker=AsyncSessionLocal,
        user_content_speech_enhancement_service=user_content_speech_enhancement_service,
        user_content_vector_search_service=user_content_vector_search_service,
        catalog_service=catalog_service,
        playback_service=playback_service,
        orchestrator=orchestrator,
        web_service=web_service,
        news_service=news_service,
        location_service=location_service,
        events_service=events_service
    )
    log_service.success("✓ DJ Prompt Service initialized")

    command_executor = CommandExecutorService(
        dj_prompt_service=dj_prompt_service,
        news_service=news_service,
        location_service=location_service,
        events_service=events_service,
        web_service=web_service,
        user_content_speech_enhancement_service=user_content_speech_enhancement_service,
        user_content_service=user_content_service,
        user_content_vector_search_service=user_content_vector_search_service,
        tts_queue_manager=tts_queue_manager,
        sio=WebSocketAdapter(),
        async_session_maker=AsyncSessionLocal,
        vector_search_service=vector_search_service,
        playback_service=playback_service,
        catalog_service=catalog_service,
        gemini_ai_service=ai_service,
        user_content_vector_db_service=user_content_vector_db_service,
        broadcast_content_func=websocket_service.broadcast_content_updated,
        broadcast_playback_state_callback=websocket_service.broadcast_playback_state
    )
    log_service.success("✓ Command Executor initialized")

    log_service.system("Initializing conversation service...")
    conversation_service.initialize(
        dj_prompt_service=dj_prompt_service,
        tts_queue_manager=tts_queue_manager,
        command_executor=command_executor,
        user_content_service=user_content_service,
        whisper_service=whisper_dual_service,
        ai_service=ai_service,
        broadcast_func=websocket_service.broadcast_to_session,
        broadcast_all_func=websocket_service.broadcast_to_all_users
    )
    log_service.success("✓ Conversation service initialized")

    background_tasks_service = BackgroundTasksService(
        web_service=web_service,
        tts_vector_db_service=tts_vector_db_service,
        async_session_maker=AsyncSessionLocal,
        catalog_vector_db_service=catalog_vector_db_service,
        catalog_service=catalog_service,
        user_content_vector_db_service=user_content_vector_db_service,
        user_content_service=user_content_service,
        broadcast_content_func=websocket_service.broadcast_content_updated,
        youtube_clip_service=get_youtube_clip_service()
    )
    weather_task_handle = asyncio.create_task(background_tasks_service.weather_updater())
    vector_db_task_handle = asyncio.create_task(background_tasks_service.vector_database_rebuilder())
    catalog_index_task_handle = asyncio.create_task(background_tasks_service.catalog_index_updater())
    user_content_index_task_handle = asyncio.create_task(background_tasks_service.user_content_index_updater())
    video_clip_task_handle = asyncio.create_task(background_tasks_service.video_clip_pre_downloader())
    await websocket_service.start_background_tasks()
    log_service.success("✓ Background tasks started (weather, TTS vector DB, catalog indexes, user content indexes, video clips, websocket cleanup)")

    announcer_service = AnnouncerService(
        playback_service=playback_service,
        orchestrator=orchestrator,
        dj_prompt_service=dj_prompt_service,
        tts_queue_manager=tts_queue_manager,
        sio=WebSocketAdapter()
    )
    await announcer_service.start()
    log_service.success("✓ Announcer service started (monitoring for safe zones)")

    log_service.success("🚀 All services initialized - Suno Playback Engine + DJ ready!")

    yield

    weather_task_handle.cancel()
    vector_db_task_handle.cancel()
    catalog_index_task_handle.cancel()
    user_content_index_task_handle.cancel()
    video_clip_task_handle.cancel()

    log_service.system("Shutting down...")

    await analytics_service.stop_background_tasks()
    log_service.system("Analytics service stopped")

    await websocket_service.stop_background_tasks()
    await websocket_service.close_all_connections()

    await engine.dispose()
    log_service.system("Database connections closed")

    await log_service.stop_worker()

app = FastAPI(title="Suno Playback Engine", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(GZipMiddleware, minimum_size=1000)

app.include_router(stripe_router, prefix="/api/stripe", tags=["stripe"])

class PlayRequest(BaseModel):
    track_id: Optional[str] = None

class SeekRequest(BaseModel):
    position_ms: int

class QueueAddRequest(BaseModel):
    track_ids: List[str]
    position: Optional[int] = None

class SearchRequest(BaseModel):
    query: str
    n_results: Optional[int] = 10
    instrumental: Optional[bool] = None
    vocal_gender: Optional[str] = None
    use_ai_analysis: Optional[bool] = False

class ShoutoutSearchRequest(BaseModel):
    query: str
    n_results: Optional[int] = 20
    use_ai_analysis: Optional[bool] = False

class GenerateRequest(BaseModel):
    user_request: Optional[str] = None
    generation_type: str = "new"
    source_track_id: Optional[str] = None
    batch_count: int = 3

class RegisterRequest(BaseModel):
    username: str
    password: str

class LoginRequest(BaseModel):
    username: str
    password: str

class PreferenceRequest(BaseModel):
    preference_type: str

class AudioQualityRequest(BaseModel):
    audio_quality: str

async def get_session_info(
        x_guest_id: Optional[str] = Header(None),
        x_device_id: Optional[str] = Header(None),
        x_device_name: Optional[str] = Header(None),
        x_device_type: Optional[str] = Header(None),
        authorization: Optional[str] = Header(None),
        token: Optional[str] = None,
        guest_id: Optional[str] = None,
        device_id: Optional[str] = None,
        db: AsyncSession = Depends(get_db)
) -> dict:
    session_guest_id = x_guest_id or guest_id
    session_device_id = x_device_id or device_id
    session_device_name = x_device_name or "Unknown Device"
    session_device_type = x_device_type or "desktop"

    jwt_token: Optional[str] = None
    if authorization and authorization.startswith("Bearer "):
        jwt_token = authorization.replace("Bearer ", "")
    elif token:
        jwt_token = token

    user: Optional[User] = None
    if jwt_token:
        payload = auth_service.decode_token(jwt_token)
        if payload:
            user_id = payload.get("sub")
            if user_id:
                user = await auth_service.get_user_by_id(db, int(user_id))

    session_id = str(user.id) if user else session_guest_id

    if not session_id:
        raise HTTPException(
            status_code=400,
            detail="Missing session headers. Please include X-Guest-ID, X-Device-ID headers."
        )

    if not session_device_id:
        raise HTTPException(
            status_code=400,
            detail="Missing device ID. Please include X-Device-ID header."
        )

    result: dict = {
        "user": user,
        "session_id": session_id,
        "device_id": session_device_id,
        "device_name": session_device_name,
        "device_type": session_device_type,
        "is_authenticated": user is not None
    }
    return result

async def get_current_user(
        authorization: Optional[str] = Header(None),
        token: Optional[str] = None,
        db: AsyncSession = Depends(get_db)
) -> Optional[User]:
    jwt_token = None
    if authorization and authorization.startswith("Bearer "):
        jwt_token = authorization.replace("Bearer ", "")
    elif token:
        jwt_token = token

    if not jwt_token:
        return None

    payload = auth_service.decode_token(jwt_token)

    if not payload:
        return None

    user_id = payload.get("sub")
    if not user_id:
        return None

    return await auth_service.get_user_by_id(db, int(user_id))  # type: ignore

@app.post("/api/auth/register")
async def register(request: RegisterRequest, db: AsyncSession = Depends(get_db)):
    try:
        user = await auth_service.register_user(db, request.username, request.password)
        if not user:
            log_service.error(f"Registration failed: Username '{request.username}' already exists")
            raise HTTPException(status_code=400, detail="Username already exists")

        token = auth_service.create_access_token({"sub": str(user.id)})
        log_service.api(f"User registered: {user.username}")
        return {
            "user": {"id": user.id, "username": user.username},
            "token": token
        }
    except ValueError as e:
        log_service.error(f"Registration validation failed: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        log_service.error(f"Registration failed: {str(e)}")
        raise HTTPException(status_code=500, detail="Registration failed. Please try again.")

@app.post("/api/auth/login")
async def login(request: LoginRequest, db: AsyncSession = Depends(get_db)):
    try:
        user = await auth_service.authenticate_user(db, request.username, request.password)
        if not user:
            log_service.error(f"Login failed: Invalid credentials for '{request.username}'")
            raise HTTPException(status_code=401, detail="Invalid username or password")

        token = auth_service.create_access_token({"sub": str(user.id)})
        log_service.api(f"User logged in: {user.username}")
        return {
            "user": {"id": user.id, "username": user.username},
            "token": token
        }
    except HTTPException:
        raise
    except Exception as e:
        log_service.error(f"Login failed: {str(e)}")
        raise HTTPException(status_code=500, detail="Login failed. Please try again.")

@app.get("/api/health")
async def health_check():
    return {
        "status": "ok",
        "timestamp": time.time()
    }

@app.get("/api/auth/me")
async def get_me(
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    result = await db.execute(select(User).where(User.id == current_user.id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    weather_result = await db.execute(
        select(WeatherData)
        .where(WeatherData.user_id == user.id)
        .order_by(WeatherData.timestamp.desc())
    )
    weather_data = weather_result.scalar_one_or_none()

    return {
        "id": user.id,
        "username": user.username,
        "audio_quality": getattr(user, "audio_quality", "auto"),
        "tier": getattr(user, "tier", "basic"),
        "subscribed": getattr(user, "subscribed", False),
        "tts_muted": getattr(user, "tts_muted", False),
        "fps_enabled": getattr(user, "fps_enabled", False),
        "video_clips_enabled": getattr(user, "video_clips_enabled", False),
        "visual_quality": getattr(user, "visual_quality", "high"),
        "persona": user.persona,
        "profile": user.profile,
        "shoutout_interests": user.shoutout_interests,
        "profile_picture": user.profile_picture,
        "location": user.location,
        "timezone": user.timezone,
        "weather_description": weather_data.description if weather_data else None,
        "weather_timestamp": weather_data.timestamp.isoformat() if weather_data else None
    }

@app.put("/api/auth/audio-quality")
async def update_audio_quality(
        request: AudioQualityRequest,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        assert user_profile_service is not None
        return await user_profile_service.update_audio_quality(
            int(current_user.id),  # type: ignore
            request.audio_quality,
            db
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

class UsernameUpdateRequest(BaseModel):
    username: str


class ManageUserDataRequest(BaseModel):
    action: str

@app.post("/api/auth/update-username")
async def update_username(
        request: UsernameUpdateRequest,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        assert user_profile_service is not None
        result = await user_profile_service.update_username(
            int(current_user.id),  # type: ignore
            request.username.strip(),
            db
        )
        return {"status": "success", **result}
    except ValueError as e:
        if "already exists" in str(e):
            raise HTTPException(status_code=409, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/manage_user_data")
async def manage_user_data(
        request: ManageUserDataRequest,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    action = request.action

    if action == "delete_conversations":
        assert user_profile_service is not None
        await user_profile_service.delete_conversations(int(current_user.id), db)  # type: ignore
        log_service.api(f"User {current_user.username} deleted conversation history")

        assert websocket_service is not None
        await websocket_service.broadcast_to_session(str(int(current_user.id)), {  # type: ignore
            "type": "conversation_cleared",
            "data": {}
        })

        return {"success": True, "message": "Conversation history deleted successfully"}

    elif action == "reset_persona":
        assert user_profile_service is not None
        await user_profile_service.reset_persona(int(current_user.id), db)  # type: ignore
        log_service.api(f"User {current_user.username} reset persona, profile, and shoutout interests")
        return {"success": True, "message": "Persona, profile, and shoutout interests reset successfully"}

    else:
        raise HTTPException(status_code=400, detail="Invalid action")

@app.post("/api/playback/play")
async def play(
        request: PlayRequest,
        session: dict = Depends(get_session_info),
):
    session_id = session["session_id"]
    user = session["user"]
    user_id = int(user.id) if user else None

    if user and request.track_id:
        banned_ids = await user_data_cache.get_banned_ids(int(user.id))
        if request.track_id in banned_ids:
            log_service.api(f"Blocked attempt to play banned track: {request.track_id}")
            raise HTTPException(status_code=403, detail="Cannot play banned track")

    assert playback_service is not None
    playback_service.set_active_device(session_id, session["device_id"])

    success = await playback_service.play(session_id, request.track_id, user_id=user_id)  # type: ignore
    if not success:
        log_service.error("Playback failed to start")
        raise HTTPException(status_code=400, detail="Failed to start playback")

    if announcer_service:
        announcer_service.monitor_session(session_id)  # type: ignore

    state = playback_service.get_state(session_id)  # type: ignore
    track_title = state.get("current_track", {}).get("generation_params", {}).get("title", "Unknown")
    log_service.api(f"[{session_id}/{session['device_id']}] Now playing: {track_title}")
    return {"status": "playing", "state": state}

@app.post("/api/playback/pause")
async def pause(session: dict = Depends(get_session_info)):
    session_id = session["session_id"]
    assert playback_service is not None
    await playback_service.pause(session_id)
    log_service.api(f"[{session_id}] Playback paused (acknowledged)")
    state = playback_service.get_state(session_id)  # type: ignore
    return {"status": "paused", "state": state}

@app.post("/api/playback/stop")
async def stop(session: dict = Depends(get_session_info)):
    session_id = session["session_id"]
    assert playback_service is not None
    await playback_service.stop(session_id)
    log_service.api(f"[{session_id}] Playback stopped")
    state = playback_service.get_state(session_id)  # type: ignore
    return {"status": "stopped", "state": state}

@app.post("/api/playback/seek")
async def seek(request: SeekRequest, session: dict = Depends(get_session_info)):
    session_id = session["session_id"]
    assert playback_service is not None
    success = await playback_service.seek(session_id, request.position_ms)
    if not success:
        log_service.error(f"[{session_id}] Seek failed: No track playing")
        raise HTTPException(status_code=400, detail="No track playing")
    log_service.api(f"[{session_id}] Seeked to {request.position_ms}ms")
    return {"status": "seeked", "position_ms": request.position_ms}

@app.post("/api/queue/add")
async def add_to_queue(
        request: QueueAddRequest,
        session: dict = Depends(get_session_info),
):
    session_id = session["session_id"]
    user = session["user"]
    user_id = int(user.id) if user else None
    track_ids_to_add = request.track_ids

    if user:
        banned_ids = await user_data_cache.get_banned_ids(int(user.id))
        track_ids_to_add = [tid for tid in track_ids_to_add if tid not in banned_ids]

        if len(track_ids_to_add) < len(request.track_ids):
            banned_count = len(request.track_ids) - len(track_ids_to_add)
            log_service.api(f"[{session_id}] Filtered out {banned_count} banned track(s) from queue")

    assert playback_service is not None
    added = await playback_service.add_to_queue(
        session_id,
        track_ids_to_add,
        request.position,
        user_id=user_id
    )  # type: ignore
    log_service.api(f"[{session_id}] Added {len(added)} track(s) to queue")
    state = playback_service.get_state(session_id)  # type: ignore
    return {"added": added, "queue": state["queue"]}

@app.delete("/api/queue/remove/{track_id}")
async def remove_from_queue(
        track_id: str,
        session: dict = Depends(get_session_info)
):
    session_id = session["session_id"]
    user = session["user"]
    user_id = int(user.id) if user else None  # type: ignore
    assert playback_service is not None
    success = await playback_service.remove_from_queue(session_id, track_id, user_id=user_id)
    if not success:
        log_service.error(f"[{session_id}] Remove from queue failed: Track {track_id} not in queue")
        raise HTTPException(status_code=404, detail="Track not in queue")
    log_service.api(f"[{session_id}] Removed track from queue: {track_id}")
    state = playback_service.get_state(session_id)  # type: ignore
    return {"removed": track_id, "queue": state["queue"]}

@app.post("/api/queue/seed")
async def seed_radio(
        request: dict,
        session: dict = Depends(get_session_info)
):
    session_id = session["session_id"]
    user = session["user"]
    user_id = int(user.id) if user else None

    category = request.get("category", "all")
    track_id = request.get("track_id")

    if category in ["favorites", "discovery"] and not user:
        raise HTTPException(
            status_code=401,
            detail="You must be logged in to use personalized radio modes."
        )

    assert playback_service is not None
    await playback_service.seed_radio(
        session_id=session_id,
        category=category,
        track_id=track_id,
        user_id=user_id
    )

    log_service.api(f"[{session_id}] Seeded radio with category: {category}")
    state = playback_service.get_state(session_id)  # type: ignore
    return {"status": "seeded", "category": category, "queue": state["queue"], "activeSeedMode": state.get("activeSeedMode")}

@app.get("/api/catalog/tracks")
async def get_catalog_tracks(
        skip: int = 0,
        limit: int = 100,
        sort_by: str = "created_at",
        order: str = "desc",
        genre: Optional[str] = None,
        current_user: User = Depends(get_current_user),
):
    banned_ids = None
    if current_user:
        banned_ids = await user_data_cache.get_banned_ids(int(current_user.id))  # type: ignore

    assert catalog_service is not None
    tracks, filtered_total = await asyncio.to_thread(
        catalog_service.get_all_tracks,
        skip,
        limit,
        sort_by,
        order,
        genre,
        banned_ids
    )

    for track in tracks:
        track["has_artwork"] = catalog_service.has_artwork(track["id"])  # type: ignore

    log_service.debug(
        f"[API] /catalog/tracks: skip={skip}, limit={limit}, genre={genre}, returned={len(tracks)}, total={filtered_total}")

    return {
        "tracks": tracks,
        "total_tracks": filtered_total,
        "skip": skip,
        "limit": limit
    }

@app.get("/api/catalog/stats")
async def get_catalog_stats(genre: Optional[str] = None):
    assert catalog_service is not None
    return catalog_service.get_stats(genre)

@app.get("/api/user_content/stats")
async def get_user_content_stats(category: Optional[str] = None):
    assert user_content_service is not None
    return user_content_service.get_stats(category)

@app.get("/api/catalog/genres")
async def get_catalog_genres():
    assert catalog_service is not None
    return {"genres": catalog_service.get_all_genres()}

@app.get("/api/track/{track_id}")
async def get_track(track_id: str):
    assert catalog_service is not None
    track = catalog_service.get_track(track_id)
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    track["has_artwork"] = catalog_service.has_artwork(track_id)  # type: ignore
    return track

@app.get("/track/{track_id}", response_class=HTMLResponse)
async def get_track_page(track_id: str):
    assert catalog_service is not None
    assert opengraph_service is not None
    track = catalog_service.get_track(track_id)
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    gen_params = track.get("generation_params", {})
    derived_tags = track.get("derived_tags", {})

    title = gen_params.get("title", "Unknown Track")
    artist = gen_params.get("artist_name") or derived_tags.get("inspired_artist", "Unknown Artist")
    genre = derived_tags.get("primary_genre")

    html = opengraph_service.render_track_page(
        track_id=track_id,
        title=title,
        artist=artist,
        genre=genre,
    )

    return HTMLResponse(content=html)

@app.get("/api/analytics/top-hits")
async def get_top_hits(period: str = "all", limit: int = 50):
    assert catalog_service is not None
    if period not in ["all", "week", "day"]:
        raise HTTPException(status_code=400, detail="Invalid period. Use 'all', 'week', or 'day'")
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="Limit must be between 1 and 200")

    hits = await analytics_service.get_top_hits(period=period, limit=limit)

    enriched_hits = []
    for hit in hits:
        track = catalog_service.get_track(hit["track_id"])  # type: ignore
        if track:
            enriched_hits.append({
                **hit,
                "track": track
            })

    return {"top_hits": enriched_hits, "period": period, "count": len(enriched_hits)}

@app.get("/api/analytics/track/{track_id}")
async def get_track_analytics(track_id: str):
    stats = await analytics_service.get_track_stats(track_id)
    if not stats:
        return {
            "track_id": track_id,
            "total_plays": 0,
            "unique_listeners": 0,
            "likes": 0,
            "superlikes": 0,
            "bans": 0,
            "popularity_score": 0
        }
    return stats

@app.get("/api/analytics/shoutout/{shoutout_id}")
async def get_shoutout_analytics(shoutout_id: str):
    stats = await analytics_service.get_shoutout_stats(shoutout_id)
    if not stats:
        return {
            "shoutout_id": shoutout_id,
            "total_plays": 0,
            "unique_listeners": 0,
            "likes": 0,
            "superlikes": 0,
            "bans": 0,
            "popularity_score": 0
        }
    return stats

@app.post("/api/analytics/shoutout/play")
async def log_shoutout_play(request: dict, current_user: Optional[User] = Depends(get_current_user)):
    shoutout_id = request.get("shoutout_id")
    user_id = request.get("user_id") or (int(current_user.id) if current_user else None)  # type: ignore
    event_type = request.get("event_type", "play")
    duration_ms = request.get("duration_ms")
    completion_pct = request.get("completion_pct")
    skip_reason = request.get("skip_reason")

    if not shoutout_id:
        raise HTTPException(status_code=400, detail="shoutout_id required")

    await analytics_service.log_play_event(
        user_id=user_id,
        track_id=shoutout_id,
        session_id=None,
        device_id=None,
        event_type=event_type,
        skip_reason=skip_reason,
        duration_ms=duration_ms,
        completion_pct=completion_pct
    )

    return {"status": "logged", "shoutout_id": shoutout_id}

@app.post("/api/tracks/{track_id}/preference")
async def set_track_preference(
        track_id: str,
        request: PreferenceRequest,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        assert preferences_service is not None
        assert websocket_service is not None
        result = await preferences_service.set_track_preference(
            int(current_user.id),  # type: ignore
            track_id,
            request.preference_type,
            db,
            playback_service=playback_service,  # type: ignore
            broadcast_callback=websocket_service.broadcast_preference_change
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/api/tracks/{track_id}/preference")
async def remove_track_preference(
        track_id: str,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    assert preferences_service is not None
    assert websocket_service is not None
    result = await preferences_service.remove_track_preference(
        int(current_user.id),  # type: ignore
        track_id,
        db,
        playback_service=playback_service,  # type: ignore
        broadcast_callback=websocket_service.broadcast_preference_change
    )
    return result

@app.get("/api/user/preferences")
async def get_user_preferences(
        current_user: User = Depends(get_current_user),
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    assert preferences_service is not None
    return await preferences_service.get_enriched_track_preferences(
        int(current_user.id),  # type: ignore
        catalog_service
    )

@app.get("/api/user/profile")
async def get_user_profile(
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        assert user_profile_service is not None
        return await user_profile_service.get_profile(int(current_user.id), db)  # type: ignore
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

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

@app.put("/api/user/profile")
async def update_user_profile(
        updates: UserProfileUpdate,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        assert user_profile_service is not None
        return await user_profile_service.update_profile(
            int(current_user.id),  # type: ignore
            updates.model_dump(exclude_unset=True),
            db
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@app.post("/api/user/profile-picture")
async def upload_profile_picture(
        file: UploadFile = File(...),
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        assert profile_picture_service is not None
        contents = await file.read()
        result = await profile_picture_service.upload_profile_picture(
            int(current_user.id), contents, file.filename or "upload.jpg", db  # type: ignore
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/user/{user_id}/profile-picture")
async def get_profile_picture(
        user_id: int,
        db: AsyncSession = Depends(get_db)
):
    assert profile_picture_service is not None
    file_path = await profile_picture_service.get_profile_picture_path(user_id, db)

    if not file_path:
        raise HTTPException(status_code=404, detail="Profile picture not found")

    return FileResponse(file_path, media_type="image/jpeg")

@app.delete("/api/user/profile-picture")
async def delete_profile_picture(
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        assert profile_picture_service is not None
        result = await profile_picture_service.delete_profile_picture(int(current_user.id),  db)  # type: ignore
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@app.delete("/api/user_content/shoutouts/{shoutout_id}")
async def delete_shoutout(
        shoutout_id: str,
        current_user: User = Depends(get_current_user)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    assert user_content_service is not None
    if '_' not in shoutout_id:
        timestamp = shoutout_id.replace('.json', '').replace('.mp3', '')
        shoutout_id = f"{current_user.id}_{timestamp}"
    else:
        parts = shoutout_id.split('_', 1)
        if str(parts[0]) != str(current_user.id):
            raise HTTPException(status_code=403, detail="You can only delete your own shoutouts")

    success = user_content_service.delete_shoutout(shoutout_id)

    if not success:
        raise HTTPException(status_code=404, detail="Shoutout not found")

    return {"status": "success", "deleted": [shoutout_id]}

@app.get("/api/user_content/shoutouts/audio/{user_id}/{filename}")
async def get_shoutout_audio(
        user_id: int,
        filename: str,
        range_header: Optional[str] = Header(None, alias="range")
):
    assert media_streaming_service is not None
    user_shoutouts_dir = settings.get_user_shoutouts_dir(user_id)
    audio_path = user_shoutouts_dir / filename

    return await media_streaming_service.stream_file(
        file_path=audio_path,
        range_header=range_header,
        media_type="audio/mpeg",
        extra_headers={
            "X-Content-Type": "shoutout",
            "X-Audio-Format": "mp3"
        }
    )

@app.get("/api/user_content/shoutouts/{shoutout_id}")
async def get_shoutout(
        shoutout_id: str,
        db: AsyncSession = Depends(get_db)
):
    assert user_content_service is not None
    shoutout = user_content_service.get_enriched_shoutout(shoutout_id)
    if not shoutout:
        raise HTTPException(status_code=404, detail="Shoutout not found")

    enriched = await user_content_service.enrich_shoutout_results([shoutout], db)  # type: ignore
    return enriched[0] if enriched else shoutout

@app.post("/api/user_content/shoutouts/search")
async def search_shoutouts(
        request: ShoutoutSearchRequest,
        current_user: Optional[User] = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    assert user_content_vector_search_service is not None
    assert user_content_service is not None
    user_id = int(current_user.id) if current_user else "guest"  # type: ignore

    log_service.user_content(f"🔍 Shoutout search request from user {user_id}: '{request.query}'")

    try:
        user_location = None
        if current_user and hasattr(current_user, 'latitude') and hasattr(current_user, 'longitude'):
            lat = current_user.latitude
            lon = current_user.longitude
            if lat is not None and lon is not None:
                user_location = (float(str(lat)), float(str(lon)))

        results = await user_content_vector_search_service.search(
            query=request.query,
            n_results=request.n_results or 20,
            content_type='shoutout',
            user_location=user_location,
            use_ai_analysis=request.use_ai_analysis or False
        )

        results = await user_content_service.enrich_shoutout_results(results, db)  # type: ignore

        log_service.user_content(f"✓ Found {len(results)} shoutout results for query: '{request.query}'")

        return {
            'results': results,
            'count': len(results)
        }

    except Exception as e:
        log_service.error(f"Shoutout search error: {str(e)}")
        import traceback
        log_service.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")

@app.get("/api/user_content/shoutouts/{shoutout_id}/replies")
async def get_shoutout_replies(
        shoutout_id: str,
        sort_by: str = "popularity",
        db: AsyncSession = Depends(get_db)
):
    assert user_content_service is not None
    parent = user_content_service.get_shoutout(shoutout_id)
    if not parent:
        raise HTTPException(status_code=404, detail="Shoutout not found")

    if not user_content_service.is_root_shoutout(shoutout_id):
        raise HTTPException(status_code=400, detail="Cannot get replies of a reply")

    replies = user_content_service.get_replies(shoutout_id, sort_by=sort_by)

    if replies:
        replies = await user_content_service.enrich_shoutout_results(replies, db)  # type: ignore

    return {
        "replies": replies,
        "count": len(replies),
        "parent_id": shoutout_id
    }

class CreateReplyRequest(BaseModel):
    pass

class DirectReplyUploadRequest(BaseModel):
    audio: str

@app.post("/api/user_content/shoutouts/{parent_id}/reply")
async def create_shoutout_reply(
        parent_id: str,
        current_user: User = Depends(get_current_user)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    assert user_content_service is not None
    parent = user_content_service.get_shoutout(parent_id)
    if not parent:
        raise HTTPException(status_code=404, detail="Parent shoutout not found")

    if not user_content_service.is_root_shoutout(parent_id):
        raise HTTPException(status_code=400, detail="Cannot reply to a reply - only root shoutouts can receive replies")

    assert websocket_service is not None
    success, transcription = await user_content_service.process_shoutout_upload(
        user_id=int(current_user.id),  # type: ignore
        enhancement_service=user_content_speech_enhancement_service,
        gemini_service=ai_service,
        vector_db_service=None,
        broadcast_callback=websocket_service.broadcast_content_updated,
        parent_id=parent_id
    )

    if not success:
        raise HTTPException(status_code=400, detail="Failed to process reply. Make sure you've recorded audio first.")

    log_service.user_content(f"✅ Reply created to shoutout {parent_id} by user {current_user.id}")

    return {
        "status": "success",
        "parent_id": parent_id,
        "transcription": transcription
    }

@app.post("/api/user_content/shoutouts/{parent_id}/reply/upload")
async def upload_shoutout_reply(
        parent_id: str,
        request: DirectReplyUploadRequest,
        current_user: User = Depends(get_current_user)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    assert user_content_service is not None
    parent = user_content_service.get_shoutout(parent_id)
    if not parent:
        raise HTTPException(status_code=404, detail="Parent shoutout not found")

    if not user_content_service.is_root_shoutout(parent_id):
        raise HTTPException(status_code=400, detail="Cannot reply to a reply - only root shoutouts can receive replies")

    import base64
    import time

    try:
        audio_bytes = base64.b64decode(request.audio)
    except Exception as e:
        log_service.error(f"Failed to decode audio: {e}")
        raise HTTPException(status_code=400, detail="Invalid audio data")

    timestamp = str(int(time.time()))

    webm_path = await user_content_service.save_audio_file(int(current_user.id), timestamp, audio_bytes)  # type: ignore
    if not webm_path:
        raise HTTPException(status_code=500, detail="Failed to save audio file")

    transcription_result = None
    try:
        transcription_result = await whisper_dual_service.transcribe_quality(audio_bytes)
        full_transcription = transcription_result.get("text", "").strip() if transcription_result else ""
        words = transcription_result.get("words", []) if transcription_result else []
        duration = transcription_result.get("duration", 0) if transcription_result else 0
    except Exception as e:
        log_service.error(f"Transcription failed: {e}")
        full_transcription = ""
        words = []
        duration = 0

    from datetime import datetime, timezone
    metadata = {
        "full_transcription": full_transcription,
        "word_level_transcription": words,
        "transcription_metadata": {
            "language": transcription_result.get("language", "en") if transcription_result else "en",
            "language_probability": transcription_result.get("language_probability", 1.0) if transcription_result else 1.0,
            "duration": duration
        },
        "user_data": {
            "user_id": int(current_user.id),  # type: ignore
            "username": current_user.username,
            "location": current_user.location if hasattr(current_user, 'location') else "Unknown",
            "latitude": float(str(current_user.latitude)) if hasattr(current_user, 'latitude') and current_user.latitude is not None else None,
            "longitude": float(str(current_user.longitude)) if hasattr(current_user, 'longitude') and current_user.longitude is not None else None,
        },
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    await user_content_service.save_metadata_file(int(current_user.id), timestamp, metadata)  # type: ignore

    assert websocket_service is not None
    success, transcription = await user_content_service.process_shoutout_upload(
        user_id=int(current_user.id),  # type: ignore
        enhancement_service=user_content_speech_enhancement_service,
        gemini_service=ai_service,
        vector_db_service=None,
        broadcast_callback=websocket_service.broadcast_content_updated,
        parent_id=parent_id
    )

    if not success:
        raise HTTPException(status_code=400, detail="Failed to process reply audio")

    log_service.user_content(f"✅ Direct reply uploaded to shoutout {parent_id} by user {current_user.id}")

    return {
        "status": "success",
        "parent_id": parent_id,
        "transcription": transcription
    }

@app.post("/api/shoutouts/{shoutout_id}/preference")
async def set_shoutout_preference(
        shoutout_id: str,
        request: PreferenceRequest,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        assert preferences_service is not None
        assert websocket_service is not None
        result = await preferences_service.set_shoutout_preference(
            int(current_user.id),  # type: ignore
            shoutout_id,
            request.preference_type,
            db,
            broadcast_callback=websocket_service.broadcast_preference_change
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/api/shoutouts/{shoutout_id}/preference")
async def remove_shoutout_preference(
        shoutout_id: str,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    assert preferences_service is not None
    assert websocket_service is not None
    result = await preferences_service.remove_shoutout_preference(
        int(current_user.id),  # type: ignore
        shoutout_id,
        db,
        broadcast_callback=websocket_service.broadcast_preference_change
    )
    return result

@app.get("/api/user/shoutout-preferences")
async def get_user_shoutout_preferences(
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    assert preferences_service is not None
    assert user_content_service is not None
    return await preferences_service.get_enriched_shoutout_preferences(
        int(current_user.id),  # type: ignore
        user_content_service,
        db
    )

@app.get("/api/conversation")
async def get_conversation_history_endpoint(
        session: dict = Depends(get_session_info),
        db: AsyncSession = Depends(get_db),
        format_type: str = 'json',
        limit: int = 10
):
    user = session["user"]
    user_id = user.id if user else None
    session_id = session["session_id"]

    temp_user_id = None if user else session_id

    history = await get_conversation_history(
        user_id=user_id,
        temp_user_id=temp_user_id,
        db=db if user else None,
        format_type=format_type,
        limit=limit
    )

    return {
        "conversations": history if format_type == 'json' else [],
        "text": history if format_type == 'text' else "",
        "count": len(history) if format_type == 'json' else 0
    }

@app.post("/api/conversation")
async def save_conversation_endpoint(
        user_input: Optional[str] = None,
        bot_response: Optional[str] = None,
        commands: Optional[str] = None,
        info: Optional[str] = None,
        warning: Optional[str] = None,
        error: Optional[str] = None,
        audio_file_path: Optional[str] = None,
        message_type: str = 'interactive',
        session: dict = Depends(get_session_info),
        db: AsyncSession = Depends(get_db)
):
    user = session["user"]
    user_id = user.id if user else None
    session_id = session["session_id"]

    if user_id:
        conversation = await save_conversation_to_database(
            user_id=user_id,
            db=db,
            user_input=user_input,
            bot_response=bot_response,
            commands=commands,
            info=info,
            warning=warning,
            error=error,
            audio_file_path=audio_file_path,
            message_type=message_type
        )

        history = await get_conversation_history(user_id=user_id, db=db, format_type='json', limit=1)
        if history:
            assert websocket_service is not None
            await websocket_service.broadcast_to_session(session_id, {
                "type": "conversation_update",
                "data": {"latest": history[-1] if history else None}
            })

        return {"status": "success", "id": conversation.id if conversation else None}
    else:
        if user_input and bot_response:
            save_temp_conversation(session_id, user_input, bot_response)

        return {"status": "success", "id": None, "note": "Saved to temp storage"}

@app.get("/api/devices")
async def get_user_devices(
        current_user: User = Depends(get_current_user),
        session: dict = Depends(get_session_info),
        db: AsyncSession = Depends(get_db)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    current_device_id = session["device_id"]
    device_name = session["device_name"]
    device_type = session["device_type"]
    session_id = str(int(current_user.id))  # type: ignore

    try:
        assert device_management_service is not None
        await device_management_service.register_or_update_device(
            db=db,
            user_id=int(current_user.id),  # type: ignore
            device_id=current_device_id,
            device_name=device_name,
            device_type=device_type,
            set_active=False
        )
        log_service.api(f"Auto-registered/updated device: {device_name} ({current_device_id})")
    except Exception as e:
        log_service.warning(f"Failed to register device (will retry): {e}")

    try:
        removed = await device_management_service.cleanup_duplicate_devices(int(current_user.id),  db)  # type: ignore
        if removed > 0:
            log_service.system(f"Auto-cleaned {removed} duplicate devices for user {current_user.username}")
    except Exception as e:
        log_service.warning(f"Failed to cleanup duplicate devices: {e}")

    assert websocket_service is not None
    online_device_ids = websocket_service.get_online_device_ids(session_id)

    try:
        devices = await device_management_service.get_user_devices(
            user_id=int(current_user.id),  # type: ignore
            db=db,
            online_device_ids=online_device_ids,
            only_online=True
        )
    except Exception as e:
        log_service.warning(f"Failed to get devices: {e}")
        devices = []

    for device in devices:
        device["is_current"] = device["device_id"] == current_device_id

    return {
        "devices": devices,
        "current_device_id": current_device_id
    }

class ActivateDeviceRequest(BaseModel):
    device_id: Optional[str] = None


class ManageUserDataRequest(BaseModel):
    action: str

class RenameDeviceRequest(BaseModel):
    new_name: str

@app.post("/api/devices/activate")
async def activate_device(
        request: Optional[ActivateDeviceRequest] = None,
        current_user: User = Depends(get_current_user),
        session: dict = Depends(get_session_info),
        db: AsyncSession = Depends(get_db)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    session_id = str(int(current_user.id))  # type: ignore
    target_device_id = request.device_id if request and request.device_id else session["device_id"]

    assert device_management_service is not None
    device = await device_management_service.register_or_update_device(
        db=db,
        user_id=int(current_user.id),  # type: ignore
        device_id=target_device_id,
        device_name=session["device_name"],
        device_type=session["device_type"],
        set_active=True
    )

    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    device_name = str(device.display_name) if device.display_name else str(device.auto_name)
    device_id = str(device.device_id)

    assert playback_service is not None
    playback_service.set_active_device(session_id, device_id)

    updated_state = playback_service.get_state(session_id)
    assert websocket_service is not None
    await websocket_service.broadcast_playback_state(session_id, updated_state)

    log_service.api(f"Device activated: {device_name} ({device_id})")

    return {
        "status": "activated",
        "device_id": device_id,
        "device_name": device_name
    }

@app.put("/api/devices/{device_id}/name")
async def rename_device(
        device_id: str,
        request: RenameDeviceRequest,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    assert device_management_service is not None
    device = await device_management_service.rename_device(
        user_id=int(current_user.id),  # type: ignore
        device_id=device_id,
        new_name=request.new_name,
        db=db
    )

    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    log_service.api(f"Device renamed: {device_id} -> {request.new_name}")

    return {
        "status": "renamed",
        "device_id": device_id,
        "device_name": request.new_name
    }

@app.delete("/api/devices/{device_id}")
async def remove_device(
        device_id: str,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    assert device_management_service is not None
    removed = await device_management_service.remove_device(
        user_id=int(current_user.id),  # type: ignore
        device_id=device_id,
        db=db
    )

    if not removed:
        raise HTTPException(status_code=404, detail="Device not found")

    session_id = str(int(current_user.id))  # type: ignore
    assert websocket_service is not None
    ws_connection = websocket_service.get_connection(session_id, device_id)
    if ws_connection:
        try:
            await ws_connection.close()
        except (ConnectionError, OSError):
            pass
        websocket_service.unregister_connection(session_id, device_id)  # type: ignore

    log_service.api(f"Device removed: {device_id}")

    return {"status": "removed", "device_id": device_id}

@app.post("/api/search/semantic")
async def semantic_search(
        request: SearchRequest,
        current_user: User = Depends(get_current_user),
        session: dict = Depends(get_session_info)
):
    session_id = session["session_id"]
    user_id = int(current_user.id) if current_user else None  # type: ignore

    banned_ids = set()
    if current_user:
        banned_ids = await user_data_cache.get_banned_ids(int(current_user.id))  # type: ignore

    assert vector_search_service is not None
    results = await vector_search_service.search(
        query=request.query,
        n_results=request.n_results or 10,
        instrumental=request.instrumental,
        vocal_gender=request.vocal_gender,
        use_ai_analysis=request.use_ai_analysis or False,
        banned_ids=banned_ids if banned_ids else None
    )

    if results:
        track_ids = [track["id"] for track in results[:request.n_results or 10]]
        assert playback_service is not None
        await playback_service.add_to_queue(session_id, track_ids, user_id=user_id)
        if track_ids:
            await playback_service.play(session_id, track_ids[0], user_id=user_id)
        log_service.api(f"Search → Queue: '{request.query}' - {len(track_ids)} tracks added, playing first")

    results = results[:request.n_results]

    assert catalog_service is not None
    for track in results:
        track["has_artwork"] = catalog_service.has_artwork(track["id"])  # type: ignore

    return {"results": results, "count": len(results)}

@app.post("/api/transcribe")
async def transcribe_audio(
        audio: UploadFile = File(...),
        session: dict = Depends(get_session_info)
):
    if not audio:
        raise HTTPException(status_code=400, detail="No audio file provided")

    if not whisper_dual_service or not whisper_dual_service.models_loaded:
        log_service.error("Whisper transcription service not available")
        raise HTTPException(status_code=503, detail="Voice transcription service unavailable")

    session_id = session["session_id"]

    audio_bytes = await audio.read()

    if len(audio_bytes) == 0:
        raise HTTPException(status_code=400, detail="Empty audio file")

    if len(audio_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Audio file too large (max 10MB)")

    log_service.api(f"[{session_id}] Voice transcription request ({len(audio_bytes)} bytes)")

    result = await whisper_dual_service.transcribe_quality(audio_bytes)

    if not result:
        raise HTTPException(status_code=500, detail="Transcription failed")

    return {
        "text": result["text"],
        "language": result["language"],
        "confidence": result["language_probability"],
        "duration": result["duration"]
    }

class DJTalkRequest(BaseModel):
    audio: Optional[str] = None
    text: Optional[str] = None
    context: Optional[str] = "generic_talk"
    voice_name: Optional[str] = None

@app.post("/api/dj/talk")
async def dj_talk(request: DJTalkRequest, session: dict = Depends(get_session_info)):
    if not tts_queue_manager:
        raise HTTPException(status_code=503, detail="DJ service unavailable")

    session_id = session["session_id"]
    user = session["user"]

    if request.audio:
        import base64
        try:
            audio_bytes = await asyncio.to_thread(base64.b64decode, request.audio)
            fast_text = await conversation_service.handle_audio_interaction(
                audio_bytes, session_id, user, is_guest=(user is None)
            )
            return {"status": "processing", "transcription": fast_text}
        except Exception as e:
            log_service.error(f"Audio handler failed: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    elif request.text:
        await conversation_service.handle_text_interaction(
            request.text, session_id, user, is_guest=(user is None)
        )
        return {"status": "processing", "transcription": request.text}

    else:
        raise HTTPException(status_code=400, detail="No input provided")

@app.get("/api/stream/{track_id}")
async def stream_track(
        track_id: str,
        range_header: Optional[str] = Header(None, alias="range")
):
    assert catalog_service is not None
    assert media_streaming_service is not None
    audio_path = catalog_service.get_audio_path(track_id)
    if audio_path is None:
        raise HTTPException(status_code=404, detail="Audio file not found")

    return await media_streaming_service.stream_file(
        file_path=audio_path,
        range_header=range_header,
        media_type="audio/mpeg",
        extra_headers={
            "X-Audio-Format": "mp3",
            "X-Audio-Bitrate": "192k"
        }
    )

@app.get("/api/stream/{track_id}/opus")
async def stream_opus_track(
        track_id: str,
        range_header: Optional[str] = Header(None, alias="range"),
        current_user: Optional[User] = Depends(get_current_user),
):
    assert media_streaming_service is not None
    bitrate = media_streaming_service.resolve_bitrate(current_user)

    if current_user:
        log_service.api(f"Streaming Opus {track_id} → {bitrate} for {current_user.username}")
    else:
        log_service.api(f"Streaming Opus {track_id} → {bitrate} for guest")

    assert catalog_service is not None
    assert transcoding_service is not None
    wav_path = settings.WAV_DIR / f"{track_id}.wav"
    mp3_path = catalog_service.get_audio_path(track_id)

    opus_path = await transcoding_service.get_or_create_opus(
        track_id=track_id,
        wav_path=wav_path if wav_path.exists() else None,
        mp3_path=mp3_path,
        bitrate=bitrate
    )

    if not opus_path or not opus_path.exists():
        log_service.error(f"Failed to get Opus file at {bitrate} for {track_id}")
        raise HTTPException(status_code=404, detail="Audio file not available")

    return await media_streaming_service.stream_file(  # type: ignore
        file_path=opus_path,
        range_header=range_header,
        media_type="audio/opus",
        extra_headers={
            "X-Audio-Format": "opus",
            "X-Audio-Bitrate": bitrate
        }
    )

@app.api_route("/api/stream/{track_id}/webm", methods=["GET", "HEAD"])
async def stream_webm_track(
        request: Request,
        track_id: str,
        range_header: Optional[str] = Header(None, alias="range"),
        current_user: Optional[User] = Depends(get_current_user),

):
    assert media_streaming_service is not None
    bitrate = media_streaming_service.resolve_bitrate(current_user)

    if current_user:
        log_service.api(f"Streaming WebM {track_id} → {bitrate} for {current_user.username}")
    else:
        log_service.api(f"Streaming WebM {track_id} → {bitrate} for guest")

    assert catalog_service is not None
    assert transcoding_service is not None
    wav_path = settings.WAV_DIR / f"{track_id}.wav"
    mp3_path = catalog_service.get_audio_path(track_id)

    webm_path = await transcoding_service.get_or_create_webm(
        track_id=track_id,
        wav_path=wav_path if wav_path.exists() else None,
        mp3_path=mp3_path,
        bitrate=bitrate
    )

    if not webm_path or not webm_path.exists():
        log_service.error(f"Failed to get WebM file at {bitrate} for {track_id}")
        raise HTTPException(status_code=404, detail="Audio file not available")

    if request.method == "HEAD":
        file_size = webm_path.stat().st_size
        return Response(
            content=None,
            headers={
                "Content-Length": str(file_size),
                "Content-Type": "audio/webm",
                "Accept-Ranges": "bytes",
                "X-Audio-Format": "webm",
                "X-Audio-Bitrate": bitrate
            }
        )

    return await media_streaming_service.stream_file(  # type: ignore
        file_path=webm_path,
        range_header=range_header,
        media_type="audio/webm",
        extra_headers={
            "X-Audio-Format": "webm",
            "X-Audio-Bitrate": bitrate
        }
    )

@app.get("/api/artwork/{track_id}")
async def get_artwork(track_id: str):
    assert catalog_service is not None
    artwork_path = catalog_service.get_artwork_path(track_id)

    if not artwork_path or not artwork_path.exists():
        raise HTTPException(status_code=404, detail="Artwork not found")

    return FileResponse(
        artwork_path,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "public, max-age=31536000"
        }
    )

@app.api_route("/api/artwork/{track_id}/enriched", methods=["GET", "HEAD"])
async def get_enriched_artwork(track_id: str):
    assert catalog_service is not None
    enriched_path = settings.ARTWORK_ENRICHED_DIR / f"{track_id}.jpeg"

    if not enriched_path.exists():
        artwork_path = catalog_service.get_artwork_path(track_id)  # type: ignore
        if not artwork_path or not artwork_path.exists():
            raise HTTPException(status_code=404, detail="Artwork not found")
        enriched_path = artwork_path

    return FileResponse(
        enriched_path,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "public, max-age=31536000",
            "X-Artwork-Type": "enriched" if enriched_path.parent.name == "artwork_enriched" else "standard"
        }
    )

@app.get("/api/audio-features/{track_id}")
async def get_audio_features(track_id: str):
    features_path = settings.AUDIOFEATURES_DIR / f"{track_id}.json"

    if not features_path.exists():
        raise HTTPException(status_code=404, detail="Audio features not found")

    async with aiofiles.open(features_path, 'r', encoding='utf-8') as f:
        content = await f.read()
        features = json.loads(content)

    return features

@app.get("/api/video-clips/{track_id}")
async def get_video_clips(track_id: str):
    youtube_service = get_youtube_clip_service()
    return await youtube_service.get_clips_for_track(track_id)  # type: ignore

@app.get("/api/video-clip-file/{filename}")
async def get_video_clip_file(filename: str):
    clip_path = settings.YOUTUBE_CLIPS_DIR / filename

    if not clip_path.exists():
        raise HTTPException(status_code=404, detail="Clip not found")

    return FileResponse(clip_path, media_type="video/mp4")

@app.get("/api/lyric-timestamps/{track_id}")
async def get_lyric_timestamps(track_id: str):
    timestamps_path = settings.LYRIC_TIMESTAMPS_DIR / f"{track_id}.json"

    if not timestamps_path.exists():
        raise HTTPException(status_code=404, detail="Lyric timestamps not found")

    async with aiofiles.open(timestamps_path, 'r', encoding='utf-8') as f:
        content = await f.read()
        timestamps = json.loads(content)

    return timestamps

@app.post("/api/lyric-timestamps/{track_id}/generate")
async def generate_lyric_timestamps(track_id: str):
    assert catalog_service is not None
    assert orchestrator is not None
    track = catalog_service.get_track(track_id)
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    metadata_path = settings.METADATA_DIR / f"{track_id}.json"
    if not metadata_path.exists():
        raise HTTPException(status_code=404, detail="Track metadata not found")

    with open(metadata_path, 'r', encoding='utf-8') as f:
        metadata = json.load(f)

    lyrics = metadata.get("generation_params", {}).get("prompt", "")
    if not lyrics:
        raise HTTPException(status_code=400, detail="Track has no lyrics")

    mp3_path = settings.AUDIO_DIR / f"{track_id}.mp3"
    if not mp3_path.exists():
        raise HTTPException(status_code=404, detail="MP3 file not found")

    log_service.api(f"Generating lyric timestamps for {track_id} via orchestrator")

    result = await orchestrator.lyrics.generate_timestamps(  # type: ignore
        track_id=track_id,
        audio_path=mp3_path,
        metadata=metadata,
        save=True
    )

    if not result:
        raise HTTPException(status_code=500, detail="Failed to generate lyric timestamps")

    return {
        "status": "success",
        "track_id": track_id,
        "timestamps": result
    }

@app.post("/api/generate")
async def generate_music(
        request: GenerateRequest,
        session: dict = Depends(get_session_info)
):
    session_id = session["session_id"]
    user = session["user"]
    user_id = int(user.id) if user else None

    if not user:
        log_service.warning(f"[{session_id}] Guest user attempted to generate music")
        raise HTTPException(
            status_code=401,
            detail="You must be logged in to generate music. Please create an account or sign in."
        )

    allowed, error_msg = await rate_limit_service.check_rate_limit(
        user_id=user_id,
        session_id=session_id,
        user=user
    )

    if not allowed:
        log_service.warning(f"[{session_id}] Rate limit exceeded: {error_msg}")
        raise HTTPException(status_code=429, detail=error_msg)

    generation_type = getattr(request, 'generation_type', 'new')
    batch_count = getattr(request, 'batch_count', 3)
    source_track_id = getattr(request, 'source_track_id', None)

    assert catalog_service is not None
    assert suno_generation_queue_service is not None
    if generation_type == 'similar':
        if not source_track_id:
            raise HTTPException(status_code=400, detail="source_track_id required for similar generation")

        track = catalog_service.get_track(source_track_id)
        if not track:
            raise HTTPException(status_code=404, detail="Source track not found")

        original_params = track.get("generation_params")
        if not original_params:
            raise HTTPException(status_code=400, detail="Source track has no generation parameters")

        log_service.api(
            f"Starting 'Similar' generation from track {source_track_id}: "
            f"{batch_count} batches (~{batch_count * 2} tracks)"
        )

        job_ids, _ = await suno_generation_queue_service.start_generation_job(
            session_id=session_id,
            original_params=original_params,
            batch_count=batch_count,
            user_id=user_id,  # type: ignore
            source_track_id=source_track_id
        )

        await rate_limit_service.record_generation(user_id=user_id, session_id=session_id)

        return {
            "status": "started",
            "job_ids": job_ids,
            "batch_count": batch_count,
            "expected_tracks": batch_count * 2,
            "generation_type": "similar",
            "source_track": {
                "id": source_track_id,
                "title": original_params.get("title")
            }
        }

    else:
        user_request = request.user_request
        if not user_request:
            raise HTTPException(status_code=400, detail="user_request required for new generation")

        log_service.system("=" * 60)
        log_service.system(f"USER REQUEST: {user_request}")
        log_service.system("=" * 60)

        log_service.api(
            f"Starting 'New' generation: {batch_count} batches (~{batch_count * 2} tracks)"
        )

        job_ids, _ = await suno_generation_queue_service.start_generation_job(
            session_id=session_id,
            original_params={},
            batch_count=batch_count,
            user_id=user_id,  # type: ignore
            source_track_id=None,
            user_request=user_request
        )

        await rate_limit_service.record_generation(user_id=user_id, session_id=session_id)

        return {
            "status": "started",
            "job_ids": job_ids,
            "batch_count": batch_count,
            "expected_tracks": batch_count * 2,
            "generation_type": "new"
        }

@app.get("/api/generation-jobs/{job_id}")
async def get_generation_job_status(
        job_id: str,
):
    assert suno_generation_queue_service is not None
    status = suno_generation_queue_service.get_job_status(job_id)
    if not status:
        raise HTTPException(status_code=404, detail="Job not found")

    return status

@app.get("/api/generation-jobs")
async def get_all_generation_jobs(
        session: dict = Depends(get_session_info)
):
    session_id = session["session_id"]
    assert suno_generation_queue_service is not None
    jobs = suno_generation_queue_service.get_all_jobs(session_id)

    return {
        "jobs": jobs,
        "count": len(jobs)
    }

@app.delete("/api/generation-jobs/{job_id}")
async def cancel_generation_job(
        job_id: str,
):
    assert suno_generation_queue_service is not None
    success = await suno_generation_queue_service.cancel_job(job_id)
    if not success:
        raise HTTPException(status_code=404, detail="Job not found")

    return {"status": "cancelled", "job_id": job_id}

@app.post("/api/user/music/upload")
async def upload_user_music(
        file: UploadFile = File(...),
        title: Optional[str] = Form(None),
        artist: Optional[str] = Form(None),
        enable_upscaling: bool = Form(False),
        current_user: User = Depends(get_current_user),
        session: dict = Depends(get_session_info)
):

    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    if not human_music_upload_service:
        raise HTTPException(status_code=503, detail="Upload service not available")

    session_id = session["session_id"]

    async def progress_callback(stage: str, percent: float):
        assert websocket_service is not None
        await websocket_service.broadcast_to_session(session_id, {
            "type": "upload_progress",
            "data": {
                "stage": stage,
                "percent": percent
            }
        })

    try:
        contents = await file.read()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to read file: {e}")

    assert human_music_upload_service is not None
    success, message, metadata = await human_music_upload_service.process_upload(
        user_id=int(current_user.id),  # type: ignore
        filename=file.filename or "upload.mp3",
        audio_bytes=contents,
        user_title=title,
        user_artist=artist,
        enable_upscaling=enable_upscaling,
        progress_callback=progress_callback
    )

    if not success:
        raise HTTPException(status_code=400, detail=message)

    assert metadata is not None
    source_quality = metadata.get("source_quality", {})
    quality_tier = source_quality.get("quality_tier", "unknown") if source_quality else "unknown"

    audio_features = {}
    if metadata.get("audio_features_extracted"):
        features_path = settings.AUDIOFEATURES_DIR / f"{metadata.get('id')}.json"
        if features_path.exists():
            try:
                with open(features_path, 'r') as f:
                    audio_features = json.load(f)
            except (json.JSONDecodeError, IOError):
                pass

    track_id = str(metadata.get("id")) if metadata.get("id") else ""
    return {
        "status": "success",
        "message": message,
        "track_id": track_id,
        "metadata": {
            "title": metadata.get("generation_params", {}).get("title"),
            "artist": metadata.get("track_info", {}).get("artist"),
            "style": metadata.get("generation_params", {}).get("style"),
            "primary_genre": metadata.get("derived_tags", {}).get("primary_genre"),
            "secondary_genres": metadata.get("derived_tags", {}).get("secondary_genres", []),
            "mood_keywords": metadata.get("derived_tags", {}).get("mood_keywords", []),
            "similar_artists": metadata.get("derived_tags", {}).get("similar_artists", []),
            "vocal_style_keywords": metadata.get("derived_tags", {}).get("vocal_style_keywords", []),
            "duration_ms": metadata.get("track_info", {}).get("duration"),
            "has_lyrics": metadata.get("transcribed_lyrics") is not None,
            "transcribed_lyrics": metadata.get("transcribed_lyrics"),
            "lyrical_interpretation": metadata.get("derived_tags", {}).get("lyrical_interpretation"),
            "has_artwork": catalog_service.has_artwork(track_id) if catalog_service else False,
            "artwork_generated": metadata.get("artwork_generated", False),
            "artwork_enriched": metadata.get("artwork_enriched", False),
            "artwork_prompt": metadata.get("artwork_prompt"),
            "source_quality": {
                "tier": quality_tier,
                "sample_rate": source_quality.get("sample_rate"),
                "bit_depth": source_quality.get("bit_depth"),
                "is_lossless": source_quality.get("is_lossless"),
                "bandwidth_utilization": source_quality.get("bandwidth_utilization"),
                "processing_notes": source_quality.get("processing_notes", "")
            } if source_quality else None,
            "audio_features": {
                "tempo": audio_features.get("tempo"),
                "key": audio_features.get("key"),
                "mode": audio_features.get("mode"),
                "energy": audio_features.get("energy"),
                "danceability": audio_features.get("danceability"),
            } if audio_features else None,
            "mastering_applied": metadata.get("mastering_applied", False),
            "mastering_blend": metadata.get("mastering_blend", 70),
            "mastering_blend_used": metadata.get("mastering_blend_used"),
            "enhancement_applied": metadata.get("enhancement_applied", False),
            "mix_analysis": metadata.get("mix_analysis"),
            "sonic_master_prompt": metadata.get("sonic_master_prompt"),
            "sonic_master_blend": metadata.get("sonic_master_blend", 0),
            "sonic_master_applied": metadata.get("sonic_master_applied", False),
            "sonic_master_blend_used": metadata.get("sonic_master_blend_used"),
            "video_search_terms": metadata.get("video_search_terms") or metadata.get("derived_tags", {}).get("video_search_terms", []),
        }
    }

@app.get("/api/user/music/tracks")
async def get_user_tracks(
        skip: int = 0,
        limit: int = 50,
        current_user: User = Depends(get_current_user)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    if not human_music_upload_service:
        raise HTTPException(status_code=503, detail="Upload service not available")

    assert human_music_upload_service is not None
    tracks = await human_music_upload_service.get_user_tracks(
        user_id=int(current_user.id),  # type: ignore
        skip=skip,
        limit=limit
    )

    return {
        "tracks": tracks,
        "count": len(tracks)
    }

@app.put("/api/user/music/tracks/{track_id}")
async def update_user_track(
        track_id: str,
        updates: dict,
        current_user: User = Depends(get_current_user)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    if not human_music_upload_service:
        raise HTTPException(status_code=503, detail="Upload service not available")

    success, message = await human_music_upload_service.update_track_metadata(
        user_id=int(current_user.id),  # type: ignore
        track_id=track_id,
        updates=updates
    )

    if not success:
        raise HTTPException(status_code=400, detail=message)

    return {"status": "success", "message": message}

@app.delete("/api/user/music/tracks/{track_id}")
async def delete_user_track(
        track_id: str,
        current_user: User = Depends(get_current_user)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    if not human_music_upload_service:
        raise HTTPException(status_code=503, detail="Upload service not available")

    success, message = await human_music_upload_service.delete_user_track(
        user_id=int(current_user.id),  # type: ignore
        track_id=track_id
    )

    if not success:
        raise HTTPException(status_code=400, detail=message)

    return {"status": "success", "message": message}

@app.post("/api/user/music/tracks/{track_id}/artwork")
async def upload_track_artwork(
        track_id: str,
        file: UploadFile = File(...),
        current_user: User = Depends(get_current_user)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        contents = await file.read()
        result = await track_artwork_service.upload_artwork(
            track_id=track_id,
            user_id=int(current_user.id),  # type: ignore
            file_contents=contents,
            original_filename=file.filename or "artwork.jpg",
            enrich=True
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log_service.error(f"Artwork upload error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/user/music/tracks/{track_id}/artwork")
async def delete_track_artwork(
        track_id: str,
        current_user: User = Depends(get_current_user)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        result = await track_artwork_service.delete_artwork(
            track_id=track_id,
            user_id=int(current_user.id)  # type: ignore
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        log_service.error(f"Artwork delete error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/user/music/tracks/{track_id}/artwork/generate")
async def generate_track_artwork(
        track_id: str,
        current_user: User = Depends(get_current_user)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        result = await track_artwork_service.generate_artwork(track_id, int(current_user.id))  # type: ignore
        return result
    except ValueError as e:
        error_msg = str(e)
        if "not found" in error_msg.lower():
            raise HTTPException(status_code=404, detail=error_msg)
        elif "only generate artwork for your own" in error_msg.lower():
            raise HTTPException(status_code=403, detail=error_msg)
        elif "already has artwork" in error_msg.lower():
            raise HTTPException(status_code=400, detail=error_msg)
        raise HTTPException(status_code=400, detail=error_msg)
    except Exception as e:
        log_service.error(f"Artwork generation error: {e}")  # noqa: F541
        raise HTTPException(status_code=500, detail=str(e))

@app.websocket("/ws/playback")
async def websocket_endpoint(
        websocket: WebSocket,
        token: Optional[str] = None,
        guest_id: Optional[str] = None,
        device_id: Optional[str] = None,
        device_name: Optional[str] = None,
        device_type: Optional[str] = None,
        db: AsyncSession = Depends(get_db)
):
    assert websocket_service is not None
    assert playback_service is not None
    await websocket.accept()  # noqa: F541

    user = None
    if token:
        payload = auth_service.decode_token(token)
        if payload:
            user_id = payload.get("sub")
            if user_id:
                user = await auth_service.get_user_by_id(db, int(user_id))

    session_id = str(int(user.id)) if user else (guest_id or "unknown")
    session_device_id = device_id or "unknown"
    session_device_name = device_name or "Unknown Device"
    session_device_type = device_type or "desktop"

    if not session_id:
        log_service.error("WebSocket connection rejected: no session_id")
        await websocket.close(code=1008, reason="No session ID provided")
        return

    websocket_service.register_connection(session_id, session_device_id, websocket)

    existing_state = playback_service.get_state(session_id) if playback_service.has_session(session_id) else None  # type: ignore
    has_existing_playback = existing_state and existing_state.get('current_track') is not None

    if user:
        assert device_management_service is not None
        await device_management_service.register_or_update_device(
            db=db,
            user_id=int(user.id),
            device_id=session_device_id,
            device_name=session_device_name,
            device_type=session_device_type,
            set_active=True
        )

        if not has_existing_playback:
            await playback_service.initialize_new_session(session_id, user_id=int(user.id))
        else:
            log_service.info(f"Preserving existing playback state for {session_id}")

        playback_service.set_active_device(session_id, session_device_id)

        updated_state = playback_service.get_state(session_id)
        await websocket_service.broadcast_playback_state(session_id, updated_state)  # type: ignore

        log_service.api(
            f"Auto-activated device on WebSocket connect: {session_device_id}")

    async def session_callback(new_state):
        await websocket_service.broadcast_playback_state(session_id, new_state)  # type: ignore
        if announcer_service:
            await announcer_service.on_playback_state_update(session_id, new_state)  # type: ignore

    if session_id not in playback_service.session_callbacks:  # type: ignore
        playback_service.register_session_callback(session_id, session_callback)  # type: ignore

    try:
        if not user:
            if not has_existing_playback:
                await playback_service.initialize_new_session(session_id, user_id=None)

            playback_service.set_active_device(session_id, session_device_id)
            log_service.api(f"Auto-activated guest device: {session_device_id}")  # type: ignore

        state = playback_service.get_state(session_id)  # type: ignore
        await websocket.send_json({"type": "playback_state", "data": state})

        while True:
            raw_data = await websocket.receive_text()

            websocket_service.update_activity(session_id, session_device_id)  # type: ignore

            try:
                message = json.loads(raw_data)
                message_type = message.get('type')
                message_data = message.get('data', {})

                if message_type == 'playback_command':
                    command = message_data.get('command')
                    user_id = user.id if user else None
                    playback_state = playback_service.get_session_state(session_id)

                    if command == 'play':
                        track_id = message_data.get('track_id')
                        log_service.playback(f"[WS] Play: {track_id[:8] if track_id else 'current'}")
                        await playback_service.play(session_id, track_id, user_id=user_id)  # type: ignore

                    elif command == 'pause':
                        log_service.playback("[WS] Pause")
                        await playback_service.pause(session_id)

                    elif command == 'seek':
                        position_ms = message_data.get('position_ms', 0)
                        log_service.playback(f"[WS] Seek: {position_ms}ms")
                        await playback_state.seek(position_ms, notify_callback=session_callback)  # type: ignore

                    elif command == 'next':
                        skip_reason = message_data.get('skip_reason', 'user_skip')
                        log_service.playback(f"[WS] Next: {skip_reason}")
                        await playback_state.next(user_id=user_id, notify_callback=session_callback,  # type: ignore
                                                  skip_reason=skip_reason)

                    elif command == 'previous':
                        log_service.playback("[WS] Previous")
                        await playback_state.previous(user_id=user_id, notify_callback=session_callback)

                    else:
                        log_service.warning(f"[WS] Unknown command: {command}")

                elif message_type == 'track_transition':
                    from_track_id = message_data.get('from_track_id')
                    to_track_id = message_data.get('to_track_id')
                    transition_type = message_data.get('transition_type')
                    crossfade_info = message_data.get('crossfade_info')

                    log_service.playback(
                        f"[WS] Track transition: {from_track_id[:8] if from_track_id else 'None'} → "
                        f"{to_track_id[:8] if to_track_id else 'None'} ({transition_type})"
                    )

                    playback_state = playback_service.get_session_state(session_id)  # type: ignore
                    await playback_state.handle_track_transition(
                        from_track_id=from_track_id,
                        to_track_id=to_track_id,
                        transition_type=transition_type,
                        user_id=int(user.id) if user else None,
                        notify_callback=session_callback,
                        crossfade_info=crossfade_info
                    )

                elif message_type == 'playback_heartbeat':
                    track_id = message_data.get('track_id')
                    actual_position_ms = message_data.get('actual_position_ms', 0)
                    is_playing = message_data.get('is_playing', False)
                    buffered_ahead_ms = message_data.get('buffered_ahead_ms', 0)
                    timestamp = message_data.get('timestamp')

                    playback_state = playback_service.get_session_state(session_id)  # type: ignore
                    await playback_state.handle_playback_heartbeat(
                        track_id=track_id,
                        actual_position_ms=actual_position_ms,
                        is_playing=is_playing,
                        buffered_ahead_ms=buffered_ahead_ms,
                        timestamp=timestamp
                    )

            except json.JSONDecodeError:
                log_service.warning(f"Failed to parse WebSocket message from {session_id}/{session_device_id}")
            except Exception as e:
                log_service.error(f"Error handling WebSocket message: {str(e)}")
                import traceback
                log_service.error(f"Traceback: {traceback.format_exc()}")

    except WebSocketDisconnect:
        pass
    except Exception as e:
        log_service.error(f"WebSocket error for {session_id}/{session_device_id}: {str(e)}")
        import traceback
        log_service.error(f"Traceback: {traceback.format_exc()}")
    finally:
        playback_service.unregister_session_callback(session_id, session_callback)  # type: ignore
        websocket_service.unregister_connection(session_id, session_device_id)  # type: ignore