import asyncio
import aiofiles
import aiofiles.os
import uuid
import json
import os
import random
import io
from pydub import AudioSegment
from mutagen.mp3 import MP3
from mutagen.id3 import ID3
from mutagen.id3._frames import TIT2, TIT3
import aiohttp
from typing import Optional, Tuple
import traceback

from config.settings import settings
from services import log_service

class TTSGenerationService:
    def __init__(self, vector_db_service, audio_processing_service, ai_service):
        self.vector_db_service = vector_db_service
        self.audio_processing_service = audio_processing_service
        self.ai_service = ai_service

        self.tts_directory = settings.TTS_AUDIO_DIR
        self.meta_directory = settings.META_AUDIO_DIR
        self.impulse_directory = settings.IMPULSE_AUDIO_DIR
        self.breath_directory = settings.BREATH_AUDIO_DIR
        self.audio_directory = os.path.join(settings.AUDIO_EFFECT_DIR, 'computer')
        self.concurrency_limit = asyncio.Semaphore(settings.TTS_CONCURRENCY_LIMIT)

        self.metrics = {
            'hits': 0,
            'misses': 0
        }

        self._initialize_directories()

    def _initialize_directories(self):
        voices = ['terry', 'shaquille']

        for voice in voices:
            os.makedirs(os.path.join(self.tts_directory, voice), exist_ok=True)
            os.makedirs(os.path.join(self.meta_directory, voice), exist_ok=True)
            os.makedirs(os.path.join(self.impulse_directory, voice), exist_ok=True)
            os.makedirs(os.path.join(self.breath_directory, voice), exist_ok=True)

        os.makedirs(self.audio_directory, exist_ok=True)

        log_service.tts_generation("TTS Generation: Initialized all audio directories")

    async def warm_breath_caches(self):
        voices = ['terry', 'shaquille']

        for voice in voices:
            voice_breath_directory = self.get_voice_directory(str(self.breath_directory), voice)

            if not await aiofiles.os.path.exists(voice_breath_directory):
                log_service.warning(f"Breath directory not found for voice: {voice}")
                continue

            cache_file = os.path.join(voice_breath_directory, f'{voice}_breath_sounds_cache.json')

            breath_files = [f for f in await asyncio.to_thread(os.listdir, voice_breath_directory)
                            if f.endswith('.mp3')]

            async with aiofiles.open(cache_file, 'w') as f:
                await f.write(json.dumps(breath_files))

            log_service.tts_generation(f"✓ Breath cache warmed for {voice}: {len(breath_files)} files")

    def _log_metrics(self, event_type: str):
        total = self.metrics['hits'] + self.metrics['misses']
        ratio = (self.metrics['hits'] / total * 100) if total > 0 else 0

        log_service.tts_generation(
            f"STATS [{event_type.upper()}]: Hits: {self.metrics['hits']} | Misses: {self.metrics['misses']} | Vector Efficiency: {ratio:.1f}%"
        )

    def get_voice_directory(self, base_dir: str, voice: str) -> str:
        return os.path.join(base_dir, voice)

    async def find_random_breath_sound(self, voice_name: str) -> Optional[str]:
        voice_breath_directory = self.get_voice_directory(str(self.breath_directory), voice_name)

        if not await aiofiles.os.path.exists(voice_breath_directory):
            return None

        cache_file = os.path.join(voice_breath_directory, f'{voice_name}_breath_sounds_cache.json')

        if await aiofiles.os.path.exists(cache_file):
            async with aiofiles.open(cache_file, 'r') as f:
                content = await f.read()
                breath_files = json.loads(content)
        else:
            breath_files = [f for f in await asyncio.to_thread(os.listdir, voice_breath_directory) if
                            f.endswith('.mp3')]
            async with aiofiles.open(cache_file, 'w') as f:
                await f.write(json.dumps(breath_files))

        if not breath_files:
            return None

        chosen_file = random.choice(breath_files)
        return os.path.join(voice_breath_directory, chosen_file)

    async def generate_elevenlabs_tts(self, sentence: str, voice_settings: dict) -> Optional[bytes]:
        headers = {
            'accept': 'audio/mpeg',
            'xi-api-key': settings.load_api_key_from_file("ELEVENLABS_API_KEY"),
            'Content-Type': 'application/json'
        }
        json_data = {
            'text': sentence,
            'voice_settings': {
                'stability': voice_settings['stability'],
                'similarity_boost': voice_settings['similarity_boost'],
                'style': voice_settings['style'],
                'use_speaker_boost': voice_settings['use_speaker_boost']
            }
        }

        async with self.concurrency_limit:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                        f'https://api.elevenlabs.io/v1/text-to-speech/{voice_settings["voice_id"]}',
                        headers=headers,
                        json=json_data
                ) as response:
                    if response.status == 200:
                        return await response.read()
                    else:
                        log_service.error(
                            f"Error generating TTS response, status code: {response.status}, response: {await response.text()}")
                        return None

    async def generate_elevenlabs_sound_effect(
            self,
            text: str,
            duration_seconds: Optional[float] = None,
            prompt_influence: float = 0.8
    ) -> Optional[AudioSegment]:
        api_key = settings.load_api_key_from_file("ELEVENLABS_API_KEY")
        url = "https://api.elevenlabs.io/v1/sound-generation"

        headers = {
            "Accept": "audio/mpeg",
            "xi-api-key": api_key,
            "Content-Type": "application/json"
        }

        json_data = {
            "text": text,
            "duration_seconds": duration_seconds,
            "prompt_influence": prompt_influence
        }

        try:
            async with self.concurrency_limit:
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, headers=headers, json=json_data) as response:
                        if response.status == 200:
                            audio_data = await response.read()
                            audio = await asyncio.to_thread(AudioSegment.from_mp3, io.BytesIO(audio_data))
                            return audio
                        else:
                            error_message = await response.text()
                            log_service.error(
                                f"Failed to generate sound effect. Status: {response.status}, Response: {error_message}")
                            return None
        except Exception as e:
            log_service.error(f"An error occurred while generating sound effect: {str(e)}")
            return None

    def _tag_audio_sync(self, audio_path, tag, audio_description):
        try:
            audio = MP3(audio_path, ID3=ID3)
            if audio.tags is None:
                audio.add_tags()
            if audio.tags is not None:
                audio.tags.add(TIT2(encoding=3, text=tag))
                if audio_description:
                    audio.tags.add(TIT3(encoding=3, text=audio_description))
            audio.save()
            return True
        except Exception as e:
            log_service.error(f"Tagging failed: {e}")
            return False

    async def _save_audio_and_embedding(self, audio_path, audio_data, tag, audio_description, content_voice,
                                        embeddings_type):
        try:
            directory = os.path.dirname(audio_path)
            if not os.path.exists(directory):
                os.makedirs(directory, exist_ok=True)

            async with aiofiles.open(audio_path, 'wb') as f:
                await f.write(audio_data)

            await asyncio.to_thread(self._tag_audio_sync, audio_path, tag, audio_description)

            await asyncio.to_thread(
                self.vector_db_service.save_embedding,
                audio_path, tag, content_voice, embeddings_type
            )
            log_service.tts_generation(f"API Cache: Background save and embedding complete: {audio_path}")
        except Exception as e:
            log_service.error(f"Background save failed: {e} {traceback.format_exc()}")

    async def fetch_or_generate_audio(
            self,
            tag: str,
            embeddings_type: str,
            content_voice: str,
            audio_process_mix: float = 0.0,
            previous_segment_end_mix: Optional[float] = None,
            next_segment_start_mix: Optional[float] = None,
            can_generate: bool = False
    ) -> Tuple[Optional[AudioSegment], Optional[str]]:
        if embeddings_type == 'tts_embeddings':
            directory = self.get_voice_directory(str(self.tts_directory), content_voice)
        elif embeddings_type == 'meta_embeddings':
            directory = self.get_voice_directory(str(self.meta_directory), content_voice)
        elif embeddings_type == 'impulse_embeddings':
            directory = self.get_voice_directory(str(self.impulse_directory), content_voice)
        elif embeddings_type == 'audio_embeddings':
            directory = self.audio_directory
        else:
            log_service.error(f"Unknown embeddings type: {embeddings_type}")
            return None, None

        similar_responses = await asyncio.to_thread(
            self.vector_db_service.query_embeddings,
            tag, content_voice, embeddings_type, 5
        )

        if embeddings_type == 'audio_embeddings':
            threshold = settings.AUDIO_SIMILARITY_THRESHOLD
        elif embeddings_type == 'meta_embeddings':
            threshold = settings.META_SIMILARITY_THRESHOLD
        elif embeddings_type == 'impulse_embeddings':
            threshold = settings.IMPULSE_SIMILARITY_THRESHOLD
        else:
            threshold = settings.TTS_SIMILARITY_THRESHOLD

        if similar_responses and similar_responses[0][2] >= threshold:
            filename, _title, _similarity = similar_responses[0]
            cached_file_path = os.path.join(directory, filename)

            if await aiofiles.os.path.exists(cached_file_path):
                async with aiofiles.open(cached_file_path, 'rb') as f:
                    audio_data = await f.read()
                processed_audio = self.audio_processing_service.process_audio(
                    audio_data,
                    audio_process_mix,
                    previous_segment_end_mix,
                    next_segment_start_mix,
                    speaker=content_voice
                )

                self.metrics['hits'] += 1
                self._log_metrics("hit")

                return processed_audio, cached_file_path
            else:
                log_service.error(f"Cached file not found: {cached_file_path}")

        if can_generate:
            self.metrics['misses'] += 1
            self._log_metrics("miss")

            audio_data = None
            audio_description = None

            if embeddings_type == 'audio_embeddings':
                _, audio_description, duration_seconds = await self.ai_service.generate_audio_effects_gpt_response(tag)
                if audio_description:
                    audio_data = await self.generate_elevenlabs_sound_effect(audio_description,
                                                                             duration_seconds=duration_seconds)
            elif embeddings_type in ['tts_embeddings', 'meta_embeddings', 'impulse_embeddings']:
                voice_settings = settings.VOICE_PREFERENCES.get(content_voice)
                if not voice_settings:
                    raise ValueError(f"Voice settings for {content_voice} not found.")

                if embeddings_type == 'meta_embeddings':
                    _, response = await self.ai_service.generate_meta_data_gpt_response(tag)
                    audio_description = response if response != "N/A" else tag
                elif embeddings_type == 'impulse_embeddings':
                    _, response = await self.ai_service.generate_impulse_gpt_response(tag, content_voice)
                    audio_description = response if response != "N/A" else tag
                else:
                    audio_description = tag

                if audio_description:
                    audio_data = await self.generate_elevenlabs_tts(audio_description, voice_settings)

            if audio_data:
                if isinstance(audio_data, AudioSegment):
                    audio_bytes = io.BytesIO()
                    await asyncio.to_thread(audio_data.export, audio_bytes, format='mp3')
                    audio_data_bytes = audio_bytes.getvalue()
                else:
                    audio_data_bytes = audio_data

                processed_audio = self.audio_processing_service.process_audio(
                    audio_data_bytes,
                    audio_process_mix,
                    previous_segment_end_mix,
                    next_segment_start_mix,
                    speaker=content_voice
                )

                audio_path = os.path.join(directory, f'{str(uuid.uuid4())}.mp3')

                asyncio.create_task(self._save_audio_and_embedding(
                    audio_path, audio_data_bytes, tag, audio_description, content_voice, embeddings_type
                ))

                return processed_audio, audio_path
        else:
            if embeddings_type == 'impulse_embeddings':
                log_service.tts_generation(
                    "Impulse: No suitable impulse found in vector DB and generation not permitted.")

        return None, None