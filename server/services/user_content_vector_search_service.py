import asyncio
import re
import json
import aiofiles
import numpy as np
from typing import List, Dict, Any, Optional, Tuple
from services import log_service
from config.settings import settings
from math import radians, sin, cos, sqrt, atan2

class UserContentVectorSearchService:

    def __init__(self, vector_db_service, user_content_service=None, prompt_cache_service=None):
        self.vector_db = vector_db_service
        self.user_content_service = user_content_service
        self.prompt_cache_service = prompt_cache_service
        log_service.user_content("UserContentVectorSearchService initialized")

    async def search(
            self,
            query: str,
            n_results: int = 20,
            content_type: Optional[str] = None,
            user_location: Optional[Tuple[float, float]] = None,
            use_ai_analysis: bool = False
    ) -> List[Dict[str, Any]]:

        if not self.user_content_service or not self.user_content_service.shoutouts:
            log_service.warning("No user content indexed for search")
            return []

        current_annoy_index = (
            self.vector_db.annoy_index_content_1
            if self.vector_db.current_index == 1
            else self.vector_db.annoy_index_content_2
        )

        def _safe_search(vector_data, num_items):
            with self.vector_db.index_lock:
                if current_annoy_index.get_n_items() == 0:
                    return []
                return current_annoy_index.get_nns_by_vector(vector_data, num_items)

        try:
            log_service.user_content(f"Searching user content: {query}")

            if use_ai_analysis and self.prompt_cache_service:
                ai_analysis = await self.prompt_cache_service.analyze_query(query)
                if ai_analysis:
                    intent_category = ai_analysis.intent_category
                    query_weights = ai_analysis.category_weights.model_dump()
                    cleaned_query = ai_analysis.cleaned_query
                    log_service.user_content(f"🤖 AI Intent: {intent_category} confidence: {ai_analysis.confidence:.2f}")
                else:
                    log_service.warning("AI analysis failed, falling back to keyword detection")
                    intent_category, query_weights, cleaned_query = self._detect_query_intent(query)
            else:
                intent_category, query_weights, cleaned_query = self._detect_query_intent(query)

            log_service.user_content(f"Category weights: {query_weights}")

            query_embedding = self.vector_db._generate_embedding(cleaned_query)

            if cleaned_query != query:
                log_service.user_content(f"Cleaned query: {cleaned_query}")

            nearest_ids = await asyncio.to_thread(_safe_search, query_embedding, n_results * 3)

            if not nearest_ids:
                if current_annoy_index.get_n_items() == 0:
                    log_service.error("Annoy index is empty")
                return []

            log_service.user_content(f"Found {len(nearest_ids)} candidates, re-ranking")

            results = []
            conn = self.user_content_service._get_connection()
            c = conn.cursor()

            for annoy_idx in nearest_ids:
                rowid = annoy_idx + 1
                full_data = None
                content_id = None
                
                if hasattr(self.vector_db, '_content_rowid_cache') and rowid in self.vector_db._content_rowid_cache:
                    content_id = self.vector_db._content_rowid_cache[rowid]
                    full_data = self.vector_db._content_metadata_cache.get(rowid, {}).copy()
                
                if full_data is None:
                    c.execute("SELECT content_id, metadata_json FROM shoutouts WHERE rowid = %s", (rowid,))
                    result = c.fetchone()
                    if not result:
                        continue
                    content_id, metadata_json = result
                    full_data = json.loads(metadata_json)

                if content_type:
                    item_type = full_data.get('content_type', 'shoutout')
                    if item_type != content_type:
                        continue

                category_texts = self.vector_db._extract_category_texts(full_data)

                category_embeddings = {
                    "transcription": self.vector_db.get_or_create_embedding(
                        category_texts["transcription"], "transcription_embeddings",
                        self.vector_db.transcription_embeddings
                    ),
                    "category": self.vector_db.get_or_create_embedding(
                        category_texts["category"], "category_embeddings",
                        self.vector_db.category_embeddings
                    ),
                    "urgency": self.vector_db.get_or_create_embedding(
                        category_texts["urgency"], "urgency_embeddings",
                        self.vector_db.urgency_embeddings
                    ),
                    "importance": self.vector_db.get_or_create_embedding(
                        category_texts["importance"], "importance_embeddings",
                        self.vector_db.importance_embeddings
                    ),
                    "tags": self.vector_db.get_or_create_embedding(
                        category_texts["tags"], "tags_embeddings",
                        self.vector_db.tags_embeddings
                    ),
                    "username": self.vector_db.get_or_create_embedding(
                        category_texts["username"], "username_embeddings",
                        self.vector_db.username_embeddings
                    ),
                    "location": self.vector_db.get_or_create_embedding(
                        category_texts["location"], "location_embeddings",
                        self.vector_db.location_embeddings
                    ),
                    "target_audience": self.vector_db.get_or_create_embedding(
                        category_texts["target_audience"], "target_audience_embeddings",
                        self.vector_db.target_audience_embeddings
                    ),
                    "sentiment": self.vector_db.get_or_create_embedding(
                        category_texts["sentiment"], "sentiment_embeddings",
                        self.vector_db.sentiment_embeddings
                    ),
                    "content_theme": self.vector_db.get_or_create_embedding(
                        category_texts["content_theme"], "content_theme_embeddings",
                        self.vector_db.content_theme_embeddings
                    )
                }

                reweighted_embedding = self.vector_db._create_weighted_embedding(
                    category_embeddings, query_weights
                )
                reranked_similarity = np.dot(query_embedding, reweighted_embedding)

                proximity_boost = 0.0
                distance_km = None

                if user_location:
                    user_data = full_data.get('user_data', {})
                    item_lat = user_data.get('latitude')
                    item_lon = user_data.get('longitude')

                    if item_lat and item_lon:
                        distance_km = self._calculate_distance(
                            user_location[0], user_location[1],
                            float(item_lat), float(item_lon)
                        )
                        if distance_km < 50:
                            proximity_boost = 0.1 * (1 - (distance_km / 50))

                final_score = float(reranked_similarity) + proximity_boost

                user_data = full_data.get('user_data', {})
                display_category = max(query_weights.items(), key=lambda x: x[1])[0]

                search_result = {
                    'id': content_id,
                    'transcription': full_data.get('full_transcription', ''),
                    'word_level_transcription': full_data.get('word_level_transcription', []),
                    'transcription_metadata': full_data.get('transcription_metadata', {}),
                    'timestamp': full_data.get('timestamp', ''),
                    'user_data': user_data,
                    'metadata': full_data.get('transcription_metadata', {}),
                    'date': full_data.get('date', ''),
                    'has_audio': True,
                    'audio_url': self._construct_audio_url({'id': content_id}),
                    'similarity_score': float(reranked_similarity),
                    'final_score': final_score,
                    'intent_category': display_category,
                    'match_weights': query_weights
                }

                if distance_km is not None:
                    search_result['distance_km'] = distance_km

                results.append(search_result)

            conn.close()

            results.sort(key=lambda x: x.get('final_score', 0), reverse=True)

            if results:
                log_service.user_content("Top 5 matches:")
                for i, item in enumerate(results[:5], 1):
                    username = item.get('user_data', {}).get('username', 'Unknown')
                    transcription = item.get('transcription', '')[:50]
                    score = item.get('final_score', 0)
                    log_service.user_content(f"  {i}. {score:.3f} {username}: {transcription}...")

            results = results[:n_results]

            log_service.user_content(f"Returning {len(results)} results for {query} intent: {intent_category}")
            return results

        except Exception as e:
            log_service.error(f"Vector search error: {str(e)}")
            import traceback
            log_service.error(f"Traceback: {traceback.format_exc()}")
            return []

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
            log_service.user_content("🎯 Query intent: TRANSCRIPTION (message content)")
        elif detected_category == "relationship":
            weights = {
                "tags": 0.40, "transcription": 0.30, "category": 0.20,
                "sentiment": 0.05, "importance": 0.03, "content_theme": 0.02,
                "username": 0.00, "location": 0.00, "urgency": 0.00, "target_audience": 0.00
            }
            log_service.user_content("🎯 Query intent: RELATIONSHIP")
        elif detected_category == "greeting":
            weights = {
                "category": 0.40, "transcription": 0.30, "tags": 0.15,
                "sentiment": 0.08, "username": 0.05, "content_theme": 0.02,
                "location": 0.00, "urgency": 0.00, "importance": 0.00, "target_audience": 0.00
            }
            log_service.user_content("🎯 Query intent: GREETING")
        elif detected_category == "announcement":
            weights = {
                "urgency": 0.30, "importance": 0.30, "category": 0.25,
                "transcription": 0.10, "target_audience": 0.03, "tags": 0.02,
                "location": 0.00, "username": 0.00, "sentiment": 0.00, "content_theme": 0.00
            }
            log_service.user_content("🎯 Query intent: ANNOUNCEMENT")
        elif detected_category == "sentiment":
            weights = {
                "sentiment": 0.50, "transcription": 0.25, "tags": 0.12,
                "category": 0.08, "content_theme": 0.03, "importance": 0.02,
                "username": 0.00, "location": 0.00, "urgency": 0.00, "target_audience": 0.00
            }
            log_service.user_content("🎯 Query intent: SENTIMENT")
        elif detected_category == "occasion":
            weights = {
                "category": 0.50, "tags": 0.30, "transcription": 0.10,
                "sentiment": 0.05, "importance": 0.03, "content_theme": 0.02,
                "username": 0.00, "location": 0.00, "urgency": 0.00, "target_audience": 0.00
            }
            log_service.user_content("🎯 Query intent: OCCASION")
        elif detected_category == "category":
            weights = {
                "category": 0.50, "tags": 0.20, "transcription": 0.15,
                "target_audience": 0.05, "urgency": 0.03, "importance": 0.03,
                "location": 0.02, "username": 0.01, "sentiment": 0.01, "content_theme": 0.00
            }
            log_service.user_content("🎯 Query intent: CATEGORY")
        elif detected_category == "urgency":
            weights = {
                "urgency": 0.50, "importance": 0.20, "category": 0.15,
                "transcription": 0.08, "tags": 0.04, "target_audience": 0.02,
                "location": 0.01, "username": 0.00, "sentiment": 0.00, "content_theme": 0.00
            }
            log_service.user_content("🎯 Query intent: URGENCY")
        elif detected_category == "username":
            weights = {
                "username": 0.60, "transcription": 0.20, "tags": 0.10,
                "category": 0.05, "location": 0.03, "sentiment": 0.02,
                "urgency": 0.00, "importance": 0.00, "target_audience": 0.00, "content_theme": 0.00
            }
            log_service.user_content("🎯 Query intent: USERNAME")
        elif detected_category == "location":
            weights = {
                "location": 0.50, "target_audience": 0.20, "transcription": 0.15,
                "category": 0.08, "tags": 0.04, "importance": 0.02,
                "username": 0.01, "urgency": 0.00, "sentiment": 0.00, "content_theme": 0.00
            }
            log_service.user_content("🎯 Query intent: LOCATION")
        elif detected_category == "tags":
            weights = {
                "tags": 0.50, "transcription": 0.25, "category": 0.12,
                "content_theme": 0.08, "target_audience": 0.03, "sentiment": 0.02,
                "username": 0.00, "location": 0.00, "urgency": 0.00, "importance": 0.00
            }
            log_service.user_content("🎯 Query intent: TAGS")
        elif detected_category == "target_audience":
            weights = {
                "target_audience": 0.45, "importance": 0.25, "location": 0.15,
                "category": 0.08, "transcription": 0.04, "tags": 0.02,
                "username": 0.01, "urgency": 0.00, "sentiment": 0.00, "content_theme": 0.00
            }
            log_service.user_content("🎯 Query intent: TARGET_AUDIENCE")
        else:
            weights = {
                "transcription": 0.30, "category": 0.15, "tags": 0.15,
                "urgency": 0.10, "importance": 0.10, "username": 0.05,
                "location": 0.05, "target_audience": 0.05, "sentiment": 0.03, "content_theme": 0.02
            }
            log_service.user_content("🎯 Query intent: GENERAL")

        return detected_category or "general", weights, cleaned_query

    @staticmethod
    async def load_all_shoutouts() -> List[Dict[str, Any]]:
        users_directory = settings.USERS_DIR
        if not users_directory.exists():
            log_service.warning(f"Users directory not found: {users_directory}")
            return []

        all_shoutouts = []

        for user_dir in users_directory.iterdir():
            if not user_dir.is_dir():
                continue

            try:
                user_id = int(user_dir.name)
                shoutouts_dir = settings.get_user_shoutouts_dir(user_id)
            except ValueError:
                continue

            if not shoutouts_dir.exists():
                continue

            for json_file in shoutouts_dir.glob("*.json"):
                try:
                    async with aiofiles.open(json_file, 'r', encoding='utf-8') as f:
                        content = await f.read()
                        shoutout_data = json.loads(content)

                        timestamp = json_file.stem
                        shoutout_data['id'] = f"{user_id}_{timestamp}"
                        shoutout_data['content_type'] = 'shoutout'

                        if 'user_data' in shoutout_data and 'timestamp' in shoutout_data['user_data']:
                            shoutout_data['date'] = shoutout_data['user_data']['timestamp']
                        else:
                            shoutout_data['date'] = ''

                        all_shoutouts.append(shoutout_data)
                except Exception as e:
                    log_service.error(f"Error loading shoutout {json_file.name}: {str(e)}")

        log_service.user_content(f"Loaded {len(all_shoutouts)} shoutouts from filesystem")
        return all_shoutouts