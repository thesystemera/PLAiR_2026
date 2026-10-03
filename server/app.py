import asyncio
import platform

if platform.system() == "Windows":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from security_middleware import RequestGuardMiddleware, MediaAwareGZipMiddleware, install_log_redaction
from usage_middleware import UsageAttributionMiddleware
from contextlib import asynccontextmanager
from typing import Optional

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
from services.youtube_clip_service import get_youtube_clip_service

from services_radio.tts_vector_db_service import VectorDBService
from services_radio.tts_processing_service import AudioProcessingService
from services_radio.tts_generation_service import TTSGenerationService
from services_radio.tts_stream_planner import TTSStreamPlanner
from services_radio.tts_queue_manager import TTSQueueManager
from services_radio.tts_broadcast_service import AudioBroadcastService
from services_radio.tts_engine_bootstrap import tts_engine_bootstrap
from services.http_client import close_http_client
from database.pg_pool import close_all_pools
from services_radio.dj_prompt_service import DJPromptService
from services_radio.dj_prompt_system_service import DJPromptSystemService
from services_radio.dj_command_executor import CommandExecutorService
from services_radio.background_tasks_service import BackgroundTasksService
from services_radio.announcer_service import AnnouncerService
from services_radio.radio_mode_service import RadioModeService
from services_radio.sting_service import StingService
from services_radio.stripe_service import stripe_router
from services_radio.external_web_service import WebService
from services_radio.external_news_service import NewsService
from services_radio.news_store import NewsStore
from services_radio.external_location_service import LocationService
from services_radio.external_events_service import EventsService
from services_radio import regional_knowledge as regional_kb
from services_radio import pulse as pulse_kb
from services.listener_request_service import ListenerRequestPromptCache, ListenerRequestStore,     ListenerRequestVectorDatabaseService
from services.semantic_source import SemanticSearch
from services_radio import local_knowledge
from services_radio import event_harvest
from services_radio import area_signals
from services_radio import talk_clock
from services_radio.area_geocode import ReverseGeocodeSignal
from services_radio.area_air_quality import AirQualitySignal
from services_radio.area_pollen import PollenSignal
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
from services.asset_integrity_service import asset_integrity_service
import models_global
from services import log_service
from services import usage_tracking
from database import init_db, engine, AsyncSessionLocal
from services_radio.conversation_service import conversation_service
from services_radio.filler_scripts import FillerScripts
from config import settings

from service_registry import services
from services.task_utils import spawn
from routers import (system, auth, playback, catalog, share, analytics, preferences, user, shoutouts, artists,
                     conversation, devices, search, dj, media, generation, user_music, ws, usage, radio,
                     client_log, account)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    await start_log_worker()
    install_log_redaction()

    init_db()
    log_service.success("✓ Database initialized")

    usage_tracking.bind(usage_tracking.system_subject("background"))
    await usage_tracking.start()

    await tts_engine_bootstrap.start()

    services.websocket_service = websocket_service = WebSocketService()
    await websocket_service.initialize()

    services.device_management_service = device_management_service = DeviceManagementService()
    await device_management_service.initialize()

    services.media_streaming_service = media_streaming_service = MediaStreamingService()
    await media_streaming_service.initialize()

    services.user_profile_service = user_profile_service = UserProfileService()
    await user_profile_service.initialize()
    user_profile_service.set_websocket_service(websocket_service)

    services.opengraph_service = OpenGraphService(base_url=settings.PUBLIC_BASE_URL)
    log_service.success("✓ OpenGraph service initialized")

    await rate_limit_service.initialize()
    log_service.success("✓ Rate limiting initialized")

    services.transcoding_service = transcoding_service = AudioTranscodingService()
    await transcoding_service.initialize()
    log_service.success("✓ FFmpeg initialized")

    await whisper_dual_service.initialize()
    await asyncio.to_thread(whisper_dual_service.warmup)

    services.metadata_service = metadata_service = MusicMetadata()
    await metadata_service.initialize()

    services.catalog_service = catalog_service = CatalogDatabaseService()
    await catalog_service.initialize()

    services.user_content_service = user_content_service = UserContentDatabaseService()
    await user_content_service.initialize()
    log_service.success("✓ User content database service initialized")

    services.ai_service = ai_service = AIService()
    await ai_service.initialize()

    enriched_metadata_service = EnrichedMetadataService(ai_service)  # type: ignore
    await enriched_metadata_service.initialize()

    services.human_metadata_extraction_service = human_metadata_extraction_service = HumanMetadataExtractionService()
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

    services.human_music_upload_service = human_music_upload_service = HumanMusicUploadService()
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

    services.suno_service = suno_service = SunoService()
    await suno_service.initialize()

    services.orchestrator = orchestrator = SunoServiceOrchestrator()
    await orchestrator.initialize()
    human_music_upload_service.attach_services(
        apollo_service=orchestrator.apollo,
        demucs_service=orchestrator.demucs,
        vocal_enhancer=orchestrator.vocal_enhancer
    )

    services.prompt_service = prompt_service = MusicPromptService(ai_service)
    await prompt_service.initialize()

    log_service.system("Initializing shared T5 encoder models...")
    await models_global.initialize_semantic_encoder()
    log_service.system("✓ Shared T5 encoder loaded (used by music search & TTS)")

    log_service.system("Initializing catalog vector database service...")
    catalog_vector_db_service = CatalogVectorDatabaseService(catalog_service)
    await asyncio.to_thread(catalog_vector_db_service.load_initial_data)
    log_service.success("✓ Catalog vector database service initialized")
    human_music_upload_service.attach_services(vector_db_service=catalog_vector_db_service)
    spawn(human_music_upload_service.backfill_fingerprints(), name="upload_fingerprint_backfill")

    log_service.system("Initializing catalog vector search prompt cache service...")
    try:
        services.vector_search_prompt_cache_service = vector_search_prompt_cache_service = CatalogVectorSearchPromptCacheService()
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
        services.vector_search_service = vector_search_service = CatalogVectorSearchService(
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
        services.user_content_vector_db_service = user_content_vector_db_service = UserContentVectorDatabaseService(user_content_service)
        await asyncio.to_thread(user_content_vector_db_service.load_initial_data)
        log_service.success("✓ User content vector database service initialized")

        log_service.system("Initializing user content prompt cache service...")
        services.user_content_prompt_cache_service = user_content_prompt_cache_service = UserContentVectorSearchPromptCacheService()
        await user_content_prompt_cache_service.initialize(ai_service, user_content_vector_db_service)
        log_service.success("✓ User content prompt cache service initialized")

        log_service.system("Initializing user content vector search service...")
        services.user_content_vector_search_service = user_content_vector_search_service = UserContentVectorSearchService(
            user_content_vector_db_service,
            user_content_service,
            user_content_prompt_cache_service
        )
        log_service.success("✓ User content vector search service initialized")

    except Exception as e:
        log_service.warning(f"⚠️  User content vector DB initialization failed: {e}")
        log_service.warning("User content vector search will not be available")
        services.user_content_vector_search_service = user_content_vector_search_service = None

    try:
        services.request_store = request_store = ListenerRequestStore()
        await asyncio.to_thread(request_store.initialize)
        services.request_vector_db_service = ListenerRequestVectorDatabaseService(request_store)
        await asyncio.to_thread(services.request_vector_db_service.load_initial_data)
        services.request_search = SemanticSearch(services.request_vector_db_service, ListenerRequestPromptCache())
        await services.request_search.prompt_cache.initialize(ai_service, services.request_vector_db_service)
        log_service.success("✓ Listener request vectors initialized")
    except Exception as e:
        log_service.warning(f"⚠️  Listener request vectors unavailable: {e}")
        services.request_store = services.request_vector_db_service = services.request_search = None

    try:
        nugget_source = local_knowledge.LocalNuggetSource()
        await asyncio.to_thread(nugget_source.initialize)
        local_vector_db = local_knowledge.LocalKnowledgeVectorDatabaseService(nugget_source)
        await asyncio.to_thread(local_vector_db.load_initial_data)
        local_search = SemanticSearch(local_vector_db, local_knowledge.LocalKnowledgePromptCache())
        await local_search.prompt_cache.initialize(ai_service, local_vector_db)
        news_vector_db = local_knowledge.NewsVectorDatabaseService(nugget_source)
        await asyncio.to_thread(news_vector_db.load_initial_data)
        news_search = SemanticSearch(news_vector_db, local_knowledge.NewsPromptCache())
        await news_search.prompt_cache.initialize(ai_service, news_vector_db)
        place_vector_db = local_knowledge.PlaceVectorDatabaseService(nugget_source)
        await asyncio.to_thread(place_vector_db.load_initial_data)
        place_search = SemanticSearch(place_vector_db, local_knowledge.PlacePromptCache())
        await place_search.prompt_cache.initialize(ai_service, place_vector_db)
        local_knowledge.install(local_vector_db, local_search, news_vector_db, news_search, place_vector_db,
                                place_search)
        log_service.success("✓ Local knowledge vectors initialized")
    except Exception as e:
        log_service.warning(f"⚠️  Local knowledge vectors unavailable: {e}")

    log_service.system("Initializing TTS Vector DB service...")
    try:
        services.tts_vector_db_service = tts_vector_db_service = VectorDBService()
        log_service.success("✓ TTS Vector DB initialized")
    except Exception as e:
        log_service.error(f"❌ FATAL: TTS Vector DB initialization failed: {e}")
        import traceback
        log_service.error(f"Traceback: {traceback.format_exc()}")
        raise

    log_service.system("Initializing playback service...")
    try:
        services.playback_service = playback_service = PlaybackService(catalog_service, vector_search_service)
        await playback_service.initialize()
        log_service.success("✓ Playback service initialized")
    except Exception as e:
        log_service.error(f"❌ FATAL: Playback service initialization failed: {e}")
        import traceback
        log_service.error(f"Traceback: {traceback.format_exc()}")
        raise

    services.suno_generation_queue_service = suno_generation_queue_service = SunoGenerationQueueService()
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
                migration_service.migrate_databases,
                empty_databases,
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
    await asyncio.to_thread(tts_vector_db_service._generate_embedding, "warm up")

    services.tts_audio_processing_service = tts_audio_processing_service = AudioProcessingService()
    log_service.success("✓ TTS Audio Processing initialized")

    services.dj_prompt_system_service = dj_prompt_system_service = DJPromptSystemService(gemini_service=ai_service)
    log_service.success("✓ DJ Prompt System Service initialized")

    services.tts_generation_service = tts_generation_service = TTSGenerationService(
        tts_vector_db_service,
        tts_audio_processing_service,
        dj_prompt_system_service
    )
    log_service.success("✓ TTS Generation service initialized")
    try:
        await asyncio.to_thread(tts_generation_service.upscaler.load)
    except Exception as e:
        log_service.error(f"Voice upscaler failed to load, takes air at the engine's rate: {e}")

    services.tts_stream_planner = tts_stream_planner = TTSStreamPlanner()
    log_service.success("✓ TTS Stream Planner initialized")

    websocket_service.set_display_renderer(tts_generation_service.paralanguage_emoji.render_message)

    class WebSocketAdapter:
        async def emit(self, event: str, msg_data: dict, room: Optional[str] = None):
            assert websocket_service is not None
            message = {"type": event, "data": msg_data}
            if room:
                await websocket_service.broadcast_to_session(room, message)

    services.tts_broadcast_service = tts_broadcast_service = AudioBroadcastService(WebSocketAdapter())
    log_service.success("✓ TTS Broadcast service initialized")

    services.tts_queue_manager = tts_queue_manager = TTSQueueManager(
        tts_vector_db_service,
        tts_audio_processing_service,
        tts_generation_service,
        tts_broadcast_service,
        tts_stream_planner
    )
    await tts_queue_manager.start()
    log_service.success("✓ TTS Queue Manager started")

    def text_embedder(text):
        return models_global.run_on_gpu_executor(catalog_vector_db_service._generate_embedding, text)

    await talk_clock.meter.load(AsyncSessionLocal)
    area_store = area_signals.AreaCacheStore(AsyncSessionLocal)
    services.web_service = web_service = WebService(area_store=area_store, session_maker=AsyncSessionLocal)
    services.news_service = news_service = NewsService(ai_service, store=NewsStore(AsyncSessionLocal),
                                                       embedder=text_embedder)
    services.location_service = location_service = LocationService()
    services.events_service = events_service = EventsService()
    regional_kb.set_regional_knowledge(regional_kb.RegionalKnowledgeService(
        regional_kb.RegionalKnowledgeStore(AsyncSessionLocal),
        [regional_kb.TicketmasterEventsCollector(events_service), regional_kb.GooglePlacesCollector(location_service),
         regional_kb.GoogleNewsCollector(news_service),
         event_harvest.WebEventsCollector(AsyncSessionLocal, news_service, ai_service)],
        embedder=text_embedder
    ))
    if settings.PULSE_ENABLED:
        pulse_kb.install(pulse_kb.Pulse(pulse_kb.default_nodes()))
        if local_knowledge.local_vector_db is not None:
            await models_global.run_on_gpu_executor(local_knowledge.local_vector_db._generate_embedding, "warm up")
    area_signals.install([ReverseGeocodeSignal(area_store), AirQualitySignal(area_store), PollenSignal(area_store)])
    log_service.success("✓ External services initialized")

    services.user_content_speech_enhancement_service = user_content_speech_enhancement_service = UserContentSpeechEnhancementService()
    await user_content_speech_enhancement_service.initialize()
    log_service.success("✓ User Content Enhancement Service initialized")

    services.dj_prompt_service = dj_prompt_service = DJPromptService(
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

    services.command_executor = command_executor = CommandExecutorService(
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
    services.filler_scripts = filler_scripts = FillerScripts(
        dj_prompt_system_service, tts_generation_service, tts_queue_manager, dj_prompt_service)
    await asyncio.to_thread(filler_scripts.load)

    conversation_service.initialize(
        dj_prompt_service=dj_prompt_service,
        tts_queue_manager=tts_queue_manager,
        command_executor=command_executor,
        user_content_service=user_content_service,
        whisper_service=whisper_dual_service,
        ai_service=ai_service,
        broadcast_func=websocket_service.broadcast_to_session,
        broadcast_all_func=websocket_service.broadcast_to_all_users,
        fillers=filler_scripts
    )
    log_service.success("✓ Conversation service initialized")

    services.background_tasks_service = background_tasks_service = BackgroundTasksService(
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
    regional_task_handle = asyncio.create_task(background_tasks_service.regional_knowledge_refresher())
    request_task_handle = asyncio.create_task(background_tasks_service.listener_request_maintainer())
    await websocket_service.start_background_tasks()
    log_service.success("✓ Background tasks started (weather, TTS vector DB, catalog indexes, user content indexes, video clips, websocket cleanup)")

    services.announcer_service = announcer_service = AnnouncerService(
        playback_service=playback_service,
        orchestrator=orchestrator,
        dj_prompt_service=dj_prompt_service,
        tts_queue_manager=tts_queue_manager,
        sio=WebSocketAdapter()
    )
    await announcer_service.start()
    log_service.success("✓ Announcer service started (monitoring for safe zones)")

    services.radio_mode_service = radio_mode_service = RadioModeService(
        playback_service=playback_service,
        dj_prompt_service=dj_prompt_service,
        tts_queue_manager=tts_queue_manager,
        websocket_service=websocket_service,
        conversation_service=conversation_service,
        async_session_maker=AsyncSessionLocal,
        catalog_service=catalog_service,
        broadcast=websocket_service.broadcast_to_session
    )
    await radio_mode_service.start()
    log_service.success("✓ Radio Mode scheduler started (opt-in talk breaks)")

    services.sting_service = sting_service = StingService(
        tts_queue_manager=tts_queue_manager,
        tts_generation_service=tts_generation_service,
        audio_processing_service=tts_audio_processing_service,
        playback_service=playback_service,
        conversation_service=conversation_service,
        async_session_maker=AsyncSessionLocal
    )
    await sting_service.start()
    log_service.success("✓ Stings & talking clock ready (station voice, IDs, musical stings)")

    asset_integrity_service.bind(
        catalog=catalog_service, user_content=user_content_service, transcoding=transcoding_service,
        features=features_service, lyrics=lyric_timestamp_service, artwork_generation=artwork_generation_service,
        artwork_enrichment=artwork_enrichment_service, embedded_artwork=embedded_artwork_service,
        suno=suno_service, vector_db=catalog_vector_db_service, enriched_metadata=enriched_metadata_service,
        speech_enhancement=user_content_speech_enhancement_service, ai=ai_service,
        upload=human_music_upload_service, orchestrator=orchestrator
    )
    services.asset_integrity_service = asset_integrity_service
    asset_integrity_service.start()

    log_service.success("🚀 All services initialized - Suno Playback Engine + DJ ready!")

    yield

    weather_task_handle.cancel()
    vector_db_task_handle.cancel()
    catalog_index_task_handle.cancel()
    user_content_index_task_handle.cancel()
    video_clip_task_handle.cancel()
    regional_task_handle.cancel()
    request_task_handle.cancel()

    log_service.system("Shutting down...")

    await playback_service.save_snapshots()
    await announcer_service.stop()
    await radio_mode_service.stop()
    await sting_service.stop()
    await asset_integrity_service.stop()

    await analytics_service.stop_background_tasks()
    log_service.system("Analytics service stopped")

    await websocket_service.stop_background_tasks()
    await websocket_service.close_all_connections()

    if tts_generation_service is not None:
        await tts_generation_service.close()
    await tts_engine_bootstrap.stop()
    await talk_clock.meter.save()
    await close_http_client()
    close_all_pools()

    await usage_tracking.stop()

    await engine.dispose()
    log_service.system("Database connections closed")

    await log_service.stop_worker()

app = FastAPI(
    title="Suno Playback Engine",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.ENABLE_API_DOCS else None,
    redoc_url="/redoc" if settings.ENABLE_API_DOCS else None,
    openapi_url="/openapi.json" if settings.ENABLE_API_DOCS else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(MediaAwareGZipMiddleware, minimum_size=1000, compresslevel=6)
app.add_middleware(UsageAttributionMiddleware)
app.add_middleware(RequestGuardMiddleware)

app.include_router(stripe_router, prefix="/api/stripe", tags=["stripe"])
app.include_router(system.router)
app.include_router(auth.router)
app.include_router(account.router)
app.include_router(playback.router)
app.include_router(catalog.router)
app.include_router(share.router)
app.include_router(analytics.router)
app.include_router(preferences.router)
app.include_router(user.router)
app.include_router(shoutouts.router)
app.include_router(conversation.router)
app.include_router(devices.router)
app.include_router(search.router)
app.include_router(dj.router)
app.include_router(media.router)
app.include_router(generation.router)
app.include_router(artists.router)
app.include_router(user_music.router)
app.include_router(ws.router)
app.include_router(usage.router)
app.include_router(radio.router)
app.include_router(client_log.router)
