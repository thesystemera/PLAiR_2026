from typing import List, Dict, Any, Optional
from services import log_service
from services.base_vector_database_service import BaseVectorDatabaseService


class UserContentVectorDatabaseService(BaseVectorDatabaseService):
    categories = (
        "transcription", "category", "urgency", "importance", "tags",
        "username", "location", "target_audience", "sentiment", "content_theme"
    )
    default_weights = {
        "transcription": 0.30,
        "category": 0.15,
        "urgency": 0.10,
        "importance": 0.10,
        "tags": 0.15,
        "username": 0.05,
        "location": 0.05,
        "target_audience": 0.05,
        "sentiment": 0.03,
        "content_theme": 0.02
    }
    log_channel = "user_content"
    service_label = "User content"
    display_name = "User Content"
    tables_setting_name = "USER_CONTENT_EMBEDDING_TABLES"
    index_dir_setting_name = "USER_CONTENT_EMBEDDINGS_DIR"
    index_file_prefix = "user_content"
    source_table = "shoutouts"
    source_id_column = "content_id"
    source_label = "shoutouts"
    source_db_label = "user_content"
    item_noun = "items"
    single_item_noun = "shoutout"

    def __init__(self, user_content_service=None):
        super().__init__(user_content_service)

    @property
    def user_content_service(self):
        return self.source_service

    @user_content_service.setter
    def user_content_service(self, value):
        self.source_service = value

    @property
    def annoy_index_content_1(self):
        return self.annoy_index_1

    @property
    def annoy_index_content_2(self):
        return self.annoy_index_2

    @property
    def _content_rowid_cache(self) -> Dict[int, str]:
        return self._rowid_cache

    @property
    def _content_metadata_cache(self) -> Dict[int, Dict]:
        return self._metadata_cache

    def _trigger_rebuild(self):
        self._rebuild_if_data_available()

    def _rebuild_if_data_available(self):
        if self.user_content_service and hasattr(self.user_content_service, 'shoutouts') and self.user_content_service.shoutouts:
            shoutouts_list = list(self.user_content_service.shoutouts.values())
            self.rebuild_indexes(shoutouts_list)
            log_service.success("  ✓ Rebuild complete")
        else:
            self._log("  ⚠️  No user content available yet - indexes will be built when content is added")

    def add_single_shoutout(self, shoutout_data: Dict[str, Any]) -> bool:
        return self._add_single_item(shoutout_data)

    def rebuild_indexes(self, _shoutouts_data: Optional[List[Dict[str, Any]]] = None):
        self._log_rebuild_header()

        if self.user_content_service is None:
            log_service.error("Cannot rebuild indexes: user_content_service is not available")
            return

        self._rebuild_from_source(self.user_content_service)

    @staticmethod
    def _extract_category_texts(item: Dict[str, Any]) -> Dict[str, str]:
        metadata = item.get("transcription_metadata", {}) or {}
        user_data = item.get("user_data", {}) or {}

        transcription = item.get('full_transcription', '')
        category = metadata.get("category", "")
        urgency_label = metadata.get("urgency_label", "")
        importance_label = metadata.get("importance_label", "")
        tags = metadata.get("tags") or []
        tags_text = ', '.join(tags) if isinstance(tags, list) else ""
        username = user_data.get("username", "")
        location = user_data.get("location", "")
        target_audience = metadata.get("target_audience", "")
        sentiment = metadata.get("sentiment", "")

        content_theme = transcription[:200] if transcription else ""

        return {
            "transcription": transcription,
            "category": category,
            "urgency": urgency_label,
            "importance": importance_label,
            "tags": tags_text,
            "username": username,
            "location": location,
            "target_audience": target_audience,
            "sentiment": sentiment,
            "content_theme": content_theme
        }
