import torch
from transformers import T5Tokenizer, T5EncoderModel
from services import log_service
import asyncio

device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

tokenizer = None
vector_matching_model = None

_model_loading = False
_model_loaded = False

async def initialize_tts_models():
    global tokenizer, vector_matching_model, _model_loading, _model_loaded

    if _model_loaded:
        return

    if _model_loading:
        while _model_loading:
            await asyncio.sleep(0.1)
        return

    _model_loading = True

    try:
        log_service.system("🤖 Loading TTS vector matching models...")

        model_name = "google/flan-t5-large"

        tokenizer = await asyncio.to_thread(
            T5Tokenizer.from_pretrained,
            model_name
        )
        log_service.system("✓ T5 Tokenizer loaded")

        vector_matching_model = await asyncio.to_thread(
            T5EncoderModel.from_pretrained,
            model_name
        )
        vector_matching_model.to(device)
        log_service.system(f"✓ T5 Vector model loaded on {device}")

        _model_loaded = True
        log_service.system("✓ All TTS models initialized")

    except Exception as e:
        log_service.error(f"Failed to load TTS models: {e}")
        raise
    finally:
        _model_loading = False

def get_device():
    return device

def get_tokenizer():
    if tokenizer is None:
        raise RuntimeError("TTS models not initialized. Call initialize_tts_models() first.")
    return tokenizer

def get_vector_model():
    if vector_matching_model is None:
        raise RuntimeError("TTS models not initialized. Call initialize_tts_models() first.")
    return vector_matching_model
