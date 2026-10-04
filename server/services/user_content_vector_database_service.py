from typing import Any, Dict

from services.category_store import CategoryStore


class UserContentVectorDatabaseService(CategoryStore):
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
    display_name = "User Content"
    source_table = "shoutouts"
    source_id_column = "content_id"

    @staticmethod
    def _extract_category_texts(item: Dict[str, Any]) -> Dict[str, str]:
        metadata = item.get("transcription_metadata", {}) or {}
        user_data = item.get("user_data", {}) or {}

        transcription = item.get('full_transcription', '')
        category = metadata.get("category", "")
        urgency_label = metadata.get("urgency_label", "")
        importance_label = metadata.get("importance_label", "")
        tags = metadata.get("tags") or []
        track = item.get("track") or {}
        about_track = [f"song review of {track.get('title')}", track.get("artist"), track.get("genre")] if track else []
        tags_text = ', '.join([t for t in (tags if isinstance(tags, list) else []) + about_track if t])
        username = user_data.get("username", "")
        location = ", ".join(dict.fromkeys(p for p in (metadata.get("about_place"), user_data.get("location")) if p))
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
