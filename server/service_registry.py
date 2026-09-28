from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from services.ai_service import AIService
    from services.suno_service import SunoService
    from services.suno_metadata_service import MusicMetadata
    from services.suno_prompt_service import MusicPromptService
    from services.suno_service_orchestrator import SunoServiceOrchestrator
    from services.audio_transcoding_service import AudioTranscodingService
    from services.catalog_database_service import CatalogDatabaseService
    from services.user_content_database_service import UserContentDatabaseService
    from services.listener_request_service import ListenerRequestStore, ListenerRequestVectorDatabaseService
    from services.semantic_source import SemanticSearch
    from services.playback_service import PlaybackService
    from services.catalog_vector_search_service import CatalogVectorSearchService
    from services.catalog_vector_search_prompt_cache_service import CatalogVectorSearchPromptCacheService
    from services.suno_generation_queue_service import SunoGenerationQueueService
    from services_radio.tts_vector_db_service import VectorDBService
    from services_radio.tts_processing_service import AudioProcessingService
    from services_radio.tts_generation_service import TTSGenerationService
    from services_radio.tts_stream_planner import TTSStreamPlanner
    from services_radio.tts_queue_manager import TTSQueueManager
    from services_radio.tts_broadcast_service import AudioBroadcastService
    from services_radio.dj_prompt_service import DJPromptService
    from services_radio.dj_prompt_system_service import DJPromptSystemService
    from services_radio.dj_command_executor import CommandExecutorService
    from services.user_content_speech_enhancement_service import UserContentSpeechEnhancementService
    from services.user_content_vector_search_service import UserContentVectorSearchService
    from services.user_content_vector_search_prompt_cache_service import UserContentVectorSearchPromptCacheService
    from services_radio.background_tasks_service import BackgroundTasksService
    from services_radio.announcer_service import AnnouncerService
    from services_radio.radio_mode_service import RadioModeService
    from services_radio.sting_service import StingService
    from services_radio.external_web_service import WebService
    from services_radio.external_news_service import NewsService
    from services_radio.external_location_service import LocationService
    from services_radio.external_events_service import EventsService
    from services.websocket_service import WebSocketService
    from services.device_management_service import DeviceManagementService
    from services.media_streaming_service import MediaStreamingService
    from services.user_profile_service import UserProfileService
    from services.opengraph_service import OpenGraphService
    from services.human_metadata_extraction_service import HumanMetadataExtractionService
    from services.human_music_upload_service import HumanMusicUploadService
    from services.asset_integrity_service import AssetIntegrityService


class ServiceRegistry:
    def __init__(self) -> None:
        self.ai_service: Optional["AIService"] = None
        self.suno_service: Optional["SunoService"] = None
        self.metadata_service: Optional["MusicMetadata"] = None
        self.prompt_service: Optional["MusicPromptService"] = None
        self.orchestrator: Optional["SunoServiceOrchestrator"] = None
        self.transcoding_service: Optional["AudioTranscodingService"] = None
        self.catalog_service: Optional["CatalogDatabaseService"] = None
        self.user_content_service: Optional["UserContentDatabaseService"] = None
        self.request_store: Optional["ListenerRequestStore"] = None
        self.request_vector_db_service: Optional["ListenerRequestVectorDatabaseService"] = None
        self.request_search: Optional["SemanticSearch"] = None
        self.playback_service: Optional["PlaybackService"] = None
        self.vector_search_service: Optional["CatalogVectorSearchService"] = None
        self.vector_search_prompt_cache_service: Optional["CatalogVectorSearchPromptCacheService"] = None
        self.suno_generation_queue_service: Optional["SunoGenerationQueueService"] = None
        self.tts_vector_db_service: Optional["VectorDBService"] = None
        self.tts_audio_processing_service: Optional["AudioProcessingService"] = None
        self.tts_generation_service: Optional["TTSGenerationService"] = None
        self.tts_stream_planner: Optional["TTSStreamPlanner"] = None
        self.tts_queue_manager: Optional["TTSQueueManager"] = None
        self.tts_broadcast_service: Optional["AudioBroadcastService"] = None
        self.dj_prompt_service: Optional["DJPromptService"] = None
        self.dj_prompt_system_service: Optional["DJPromptSystemService"] = None
        self.command_executor: Optional["CommandExecutorService"] = None
        self.user_content_speech_enhancement_service: Optional["UserContentSpeechEnhancementService"] = None
        self.user_content_vector_search_service: Optional["UserContentVectorSearchService"] = None
        self.user_content_prompt_cache_service: Optional["UserContentVectorSearchPromptCacheService"] = None
        self.background_tasks_service: Optional["BackgroundTasksService"] = None
        self.announcer_service: Optional["AnnouncerService"] = None
        self.radio_mode_service: Optional["RadioModeService"] = None
        self.sting_service: Optional["StingService"] = None
        self.web_service: Optional["WebService"] = None
        self.news_service: Optional["NewsService"] = None
        self.location_service: Optional["LocationService"] = None
        self.events_service: Optional["EventsService"] = None
        self.websocket_service: Optional["WebSocketService"] = None
        self.device_management_service: Optional["DeviceManagementService"] = None
        self.media_streaming_service: Optional["MediaStreamingService"] = None
        self.user_profile_service: Optional["UserProfileService"] = None
        self.opengraph_service: Optional["OpenGraphService"] = None
        self.human_metadata_extraction_service: Optional["HumanMetadataExtractionService"] = None
        self.human_music_upload_service: Optional["HumanMusicUploadService"] = None
        self.asset_integrity_service: Optional["AssetIntegrityService"] = None


services = ServiceRegistry()
