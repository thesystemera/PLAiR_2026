import re
from typing import Collection, List, Dict, Any, Optional, Tuple, Union
from services import log_service
from services.semantic_source import SemanticSearch
from services.user_content_database_service import kind_of
from math import radians, sin, cos, sqrt, atan2

class UserContentVectorSearchService:

    def __init__(self, vector_db_service, user_content_service=None, prompt_cache_service=None):
        self.vector_db = vector_db_service
        self.user_content_service = user_content_service
        self.prompt_cache_service = prompt_cache_service
        self.semantic = SemanticSearch(vector_db_service, prompt_cache_service)
        log_service.user_content("UserContentVectorSearchService initialized")

    async def search(
            self,
            query: str,
            n_results: int = 20,
            content_type: Union[str, Collection[str], None] = "shoutout",
            user_location: Optional[Tuple[float, float]] = None,
            use_ai_analysis: bool = False,
            exclude: Optional[Collection[str]] = None,
            track_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:

        if not self.user_content_service or not self.user_content_service.shoutouts:
            log_service.warning("No user content indexed for search")
            return []

        try:
            log_service.detail(f"Searching user content: {query}", "user_content")
            intent_category, query_weights, cleaned_query = await self._intent(query, use_ai_analysis)
            log_service.detail(f"Category weights: {query_weights}", "user_content")

            kinds = {content_type} if isinstance(content_type, str) else set(content_type or ())
            excluded = set(exclude or ())

            def keep(item: Dict[str, Any]) -> bool:
                if kinds and kind_of(item) not in kinds:
                    return False
                if track_id and str((item.get('track') or {}).get('id')) != str(track_id):
                    return False
                return not excluded or item.get('id') not in excluded

            def boost(item: Dict[str, Any]) -> float:
                distance_km = self._distance_to(item, user_location)
                return 0.1 * (1 - distance_km / 50) if distance_km is not None and distance_km < 50 else 0.0

            found = await self.semantic.search(cleaned_query, n=n_results, keep=keep, boost=boost,
                                               weights=query_weights)
            display_category = max(query_weights.items(), key=lambda x: x[1])[0]
            results = []
            for match in found:
                full_data = match.meta
                content_id = self.vector_db._rowid_cache.get(match.rowid) or full_data.get('id')
                user_data = full_data.get('user_data', {})
                distance_km = self._distance_to(full_data, user_location)
                result = {
                    'id': content_id,
                    'transcription': full_data.get('full_transcription', ''),
                    'word_level_transcription': full_data.get('word_level_transcription', []),
                    'transcription_metadata': full_data.get('transcription_metadata', {}),
                    'timestamp': full_data.get('timestamp', ''),
                    'user_data': user_data,
                    'metadata': full_data.get('transcription_metadata', {}),
                    'date': full_data.get('date', ''),
                    'kind': kind_of(full_data),
                    'parent_id': full_data.get('parent_id'),
                    'track': full_data.get('track'),
                    'sting': full_data.get('sting'),
                    'has_audio': not full_data.get('text_only'),
                    'audio_url': None if full_data.get('text_only') else self._construct_audio_url({'id': content_id}),
                    'similarity_score': match.similarity,
                    'final_score': match.score,
                    'intent_category': display_category,
                    'match_weights': query_weights,
                }
                if distance_km is not None:
                    result['distance_km'] = distance_km
                results.append(result)
            log_service.detail(f"Returning {len(results)} results for {query} intent: {intent_category}",
                               "user_content")
            return results

        except Exception as e:
            log_service.error(f"Vector search error: {str(e)}")
            import traceback
            log_service.error(f"Traceback: {traceback.format_exc()}")
            return []

    async def _intent(self, query: str, use_ai_analysis: bool) -> Tuple[str, Dict[str, float], str]:
        if use_ai_analysis and self.prompt_cache_service:
            ai_analysis = await self.prompt_cache_service.analyze_query(query)
            if ai_analysis:
                log_service.detail(f"🤖 AI Intent: {ai_analysis.intent_category} "
                                   f"confidence: {ai_analysis.confidence:.2f}", "user_content")
                return ai_analysis.intent_category, ai_analysis.category_weights.model_dump(), ai_analysis.cleaned_query
            log_service.warning("AI analysis failed, falling back to keyword detection")
        intent, weights, cleaned = self._detect_query_intent(query)
        return intent, weights, cleaned if cleaned.strip() else query

    def _distance_to(self, item: Dict[str, Any], user_location) -> Optional[float]:
        if not user_location:
            return None
        user_data = item.get('user_data', {})
        try:
            return self._calculate_distance(user_location[0], user_location[1],
                                            float(user_data.get('latitude')), float(user_data.get('longitude')))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _construct_audio_url(item: Dict[str, Any]) -> str:
        item_id = item.get('id', '')
        if item_id:
            parts = item_id.split('_', 1)
            if len(parts) == 2:
                user_id, timestamp = parts
                return f"/api/user_content/shoutouts/audio/{user_id}/{timestamp}.mp3"
        return ""

    @staticmethod
    def _calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        R = 6371
        lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
        c = 2 * atan2(sqrt(a), sqrt(1 - a))
        return R * c

    @staticmethod
    def _clean_natural_query(query: str, trigger_patterns: list) -> str:
        cleaned = query.lower()
        for pattern in trigger_patterns:
            cleaned = re.sub(r'\b' + pattern + r'\b', '', cleaned, flags=re.IGNORECASE)
        filler_words = ['the', 'of', 'a', 'an', 'find', 'search', 'show', 'me', 'some', 'about']
        for filler in filler_words:
            cleaned = re.sub(r'^' + filler + r'\s+', '', cleaned)
            cleaned = re.sub(r'\s+' + filler + r'$', '', cleaned)
            cleaned = re.sub(r'\s+' + filler + r'\s+', ' ', cleaned)
        cleaned = ' '.join(cleaned.split())
        return cleaned.strip()

    def _detect_query_intent(self, query: str) -> Tuple[str, Dict[str, float], str]:
        query_lower = query.lower()
        detected_category = ""
        cleaned_query = query

        if query_lower.startswith("message:") or query_lower.startswith("transcription:"):
            detected_category = "transcription"
            cleaned_query = query.split(":", 1)[1].strip()
        elif query_lower.startswith("sentiment:") or query_lower.startswith("feeling:"):
            detected_category = "sentiment"
            cleaned_query = query.split(":", 1)[1].strip()
        elif query_lower.startswith("occasion:") or query_lower.startswith("event:"):
            detected_category = "occasion"
            cleaned_query = query.split(":", 1)[1].strip()
        elif query_lower.startswith("relationship:"):
            detected_category = "relationship"
            cleaned_query = query[13:].strip()
        elif query_lower.startswith("greeting:"):
            detected_category = "greeting"
            cleaned_query = query[9:].strip()
        elif query_lower.startswith("announcement:"):
            detected_category = "announcement"
            cleaned_query = query[13:].strip()
        elif query_lower.startswith("category:"):
            detected_category = "category"
            cleaned_query = query[9:].strip()
        elif query_lower.startswith("urgent:") or query_lower.startswith("urgency:"):
            detected_category = "urgency"
            cleaned_query = query.split(":", 1)[1].strip()
        elif query_lower.startswith("from:") or query_lower.startswith("user:"):
            detected_category = "username"
            cleaned_query = query.split(":", 1)[1].strip()
        elif query_lower.startswith("location:") or query_lower.startswith("place:"):
            detected_category = "location"
            cleaned_query = query.split(":", 1)[1].strip()
        elif query_lower.startswith("tag:") or query_lower.startswith("tags:"):
            detected_category = "tags"
            cleaned_query = query.split(":", 1)[1].strip()

        elif re.search(r'\b(said|message|words|mentioned|talking about|what they said|what was said)\b', query_lower):
            detected_category = "transcription"
            cleaned_query = self._clean_natural_query(query, ['said', 'message', 'words', 'mentioned', 'talking about', 'what they said', 'what was said'])
        elif re.search(r'\b(family|brother|sister|friend|mom|dad|mother|father|parent|sibling|cousin|uncle|aunt|relative)\b', query_lower):
            detected_category = "relationship"
            cleaned_query = self._clean_natural_query(query, ['family', 'brother', 'sister', 'friend', 'mom', 'dad', 'mother', 'father', 'parent', 'sibling', 'cousin', 'uncle', 'aunt', 'relative'])
        elif re.search(r'\b(greeting|greetings|hello|hi|hey|shoutout|shout out|saying hi|say hi)\b', query_lower):
            detected_category = "greeting"
            cleaned_query = self._clean_natural_query(query, ['greeting', 'greetings', 'hello', 'hi', 'hey', 'shoutout', 'shout out', 'saying hi', 'say hi'])
        elif re.search(r'\b(announcement|announce|news|update|notice|alert|bulletin|inform)\b', query_lower):
            detected_category = "announcement"
            cleaned_query = self._clean_natural_query(query, ['announcement', 'announce', 'news', 'update', 'notice', 'alert', 'bulletin', 'inform'])
        elif re.search(r'\b(happy|sad|excited|congratulations|congrats|sorry|love|thank|thanks|grateful)\b', query_lower):
            detected_category = "sentiment"
            cleaned_query = self._clean_natural_query(query, ['happy', 'sad', 'excited', 'congratulations', 'congrats', 'sorry', 'love', 'thank', 'thanks', 'grateful'])
        elif re.search(r'\b(birthday|wedding|graduation|anniversary|celebration|party|event)\b', query_lower):
            detected_category = "occasion"
            cleaned_query = self._clean_natural_query(query, ['birthday', 'wedding', 'graduation', 'anniversary', 'celebration', 'party', 'event'])
        elif re.search(r'\b(urgent|emergency|asap|important|critical)\b', query_lower):
            detected_category = "urgency"
            cleaned_query = self._clean_natural_query(query, ['urgent', 'emergency', 'asap', 'important', 'critical'])
        elif re.search(r'\b(from|by|user|username|posted by)\b', query_lower):
            detected_category = "username"
            cleaned_query = self._clean_natural_query(query, ['from', 'by', 'user', 'username', 'posted by'])
        elif re.search(r'\b(location|place|area|city|near|in)\b', query_lower):
            detected_category = "location"
            cleaned_query = self._clean_natural_query(query, ['location', 'place', 'area', 'city', 'near', 'in'])
        elif re.search(r'\b(tag|tagged|keyword|keywords|about)\b', query_lower):
            detected_category = "tags"
            cleaned_query = self._clean_natural_query(query, ['tag', 'tagged', 'keyword', 'keywords', 'about'])
        elif re.search(r'\b(category|type|kind of)\b', query_lower):
            detected_category = "category"
            cleaned_query = self._clean_natural_query(query, ['category', 'type', 'kind of'])
        elif re.search(r'\b(local|community|neighborhood|citywide|everyone)\b', query_lower):
            detected_category = "target_audience"
            cleaned_query = self._clean_natural_query(query, ['local', 'community', 'neighborhood', 'citywide', 'everyone'])

        if detected_category == "transcription":
            weights = {
                "transcription": 0.60, "tags": 0.20, "category": 0.10,
                "sentiment": 0.05, "content_theme": 0.03, "importance": 0.02,
                "username": 0.00, "location": 0.00, "urgency": 0.00, "target_audience": 0.00
            }
            log_service.detail("🎯 Query intent: TRANSCRIPTION (message content)", "user_content")
        elif detected_category == "relationship":
            weights = {
                "tags": 0.40, "transcription": 0.30, "category": 0.20,
                "sentiment": 0.05, "importance": 0.03, "content_theme": 0.02,
                "username": 0.00, "location": 0.00, "urgency": 0.00, "target_audience": 0.00
            }
            log_service.detail("🎯 Query intent: RELATIONSHIP", "user_content")
        elif detected_category == "greeting":
            weights = {
                "category": 0.40, "transcription": 0.30, "tags": 0.15,
                "sentiment": 0.08, "username": 0.05, "content_theme": 0.02,
                "location": 0.00, "urgency": 0.00, "importance": 0.00, "target_audience": 0.00
            }
            log_service.detail("🎯 Query intent: GREETING", "user_content")
        elif detected_category == "announcement":
            weights = {
                "urgency": 0.30, "importance": 0.30, "category": 0.25,
                "transcription": 0.10, "target_audience": 0.03, "tags": 0.02,
                "location": 0.00, "username": 0.00, "sentiment": 0.00, "content_theme": 0.00
            }
            log_service.detail("🎯 Query intent: ANNOUNCEMENT", "user_content")
        elif detected_category == "sentiment":
            weights = {
                "sentiment": 0.50, "transcription": 0.25, "tags": 0.12,
                "category": 0.08, "content_theme": 0.03, "importance": 0.02,
                "username": 0.00, "location": 0.00, "urgency": 0.00, "target_audience": 0.00
            }
            log_service.detail("🎯 Query intent: SENTIMENT", "user_content")
        elif detected_category == "occasion":
            weights = {
                "category": 0.50, "tags": 0.30, "transcription": 0.10,
                "sentiment": 0.05, "importance": 0.03, "content_theme": 0.02,
                "username": 0.00, "location": 0.00, "urgency": 0.00, "target_audience": 0.00
            }
            log_service.detail("🎯 Query intent: OCCASION", "user_content")
        elif detected_category == "category":
            weights = {
                "category": 0.50, "tags": 0.20, "transcription": 0.15,
                "target_audience": 0.05, "urgency": 0.03, "importance": 0.03,
                "location": 0.02, "username": 0.01, "sentiment": 0.01, "content_theme": 0.00
            }
            log_service.detail("🎯 Query intent: CATEGORY", "user_content")
        elif detected_category == "urgency":
            weights = {
                "urgency": 0.50, "importance": 0.20, "category": 0.15,
                "transcription": 0.08, "tags": 0.04, "target_audience": 0.02,
                "location": 0.01, "username": 0.00, "sentiment": 0.00, "content_theme": 0.00
            }
            log_service.detail("🎯 Query intent: URGENCY", "user_content")
        elif detected_category == "username":
            weights = {
                "username": 0.60, "transcription": 0.20, "tags": 0.10,
                "category": 0.05, "location": 0.03, "sentiment": 0.02,
                "urgency": 0.00, "importance": 0.00, "target_audience": 0.00, "content_theme": 0.00
            }
            log_service.detail("🎯 Query intent: USERNAME", "user_content")
        elif detected_category == "location":
            weights = {
                "location": 0.50, "target_audience": 0.20, "transcription": 0.15,
                "category": 0.08, "tags": 0.04, "importance": 0.02,
                "username": 0.01, "urgency": 0.00, "sentiment": 0.00, "content_theme": 0.00
            }
            log_service.detail("🎯 Query intent: LOCATION", "user_content")
        elif detected_category == "tags":
            weights = {
                "tags": 0.50, "transcription": 0.25, "category": 0.12,
                "content_theme": 0.08, "target_audience": 0.03, "sentiment": 0.02,
                "username": 0.00, "location": 0.00, "urgency": 0.00, "importance": 0.00
            }
            log_service.detail("🎯 Query intent: TAGS", "user_content")
        elif detected_category == "target_audience":
            weights = {
                "target_audience": 0.45, "importance": 0.25, "location": 0.15,
                "category": 0.08, "transcription": 0.04, "tags": 0.02,
                "username": 0.01, "urgency": 0.00, "sentiment": 0.00, "content_theme": 0.00
            }
            log_service.detail("🎯 Query intent: TARGET_AUDIENCE", "user_content")
        else:
            weights = {
                "transcription": 0.30, "category": 0.15, "tags": 0.15,
                "urgency": 0.10, "importance": 0.10, "username": 0.05,
                "location": 0.05, "target_audience": 0.05, "sentiment": 0.03, "content_theme": 0.02
            }
            log_service.detail("🎯 Query intent: GENERAL", "user_content")

        return detected_category or "general", weights, cleaned_query
