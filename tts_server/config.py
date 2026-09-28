import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


HOST = _env("TTS_SERVER_HOST", "127.0.0.1")
PORT = int(_env("TTS_SERVER_PORT", "8090"))
GPU = _env("TTS_CUDA_VISIBLE_DEVICES", _env("CUDA_VISIBLE_DEVICES", "0"))

QUALITY = _env("TTS_QUALITY", "AVERAGE").upper()
NUM_WORKERS = int(_env("TTS_NUM_WORKERS", "2"))
SAMPLE_RATE = 24000
DEFAULT_VOICE = _env("TTS_DEFAULT_VOICE", "leo")

TEMPERATURE = float(_env("TTS_TEMPERATURE", "0.8"))
TOP_P = float(_env("TTS_TOP_P", "0.9"))
MIN_P = float(_env("TTS_MIN_P", "0.1"))
REPEAT_PENALTY = float(_env("TTS_REPEAT_PENALTY", "1.2"))
FREQUENCY_PENALTY = float(_env("TTS_FREQUENCY_PENALTY", "0.1"))
MAX_TOKENS = int(_env("TTS_REQUEST_MAX_TOKENS", "2000"))
STOP_ON_END_OF_SPEECH = _env("TTS_STOP_ON_END_OF_SPEECH", "true").lower() in ("1", "true", "yes")
DURATION_CAP = _env("TTS_DURATION_CAP", "true").lower() in ("1", "true", "yes")
DURATION_CAP_FLOOR_S = float(_env("TTS_DURATION_CAP_FLOOR_S", "5.0"))
DURATION_CAP_BASE_S = float(_env("TTS_DURATION_CAP_BASE_S", "3.0"))
DURATION_CAP_PER_WORD_S = float(_env("TTS_DURATION_CAP_PER_WORD_S", "1.0"))
DURATION_CAP_PER_TAG_S = float(_env("TTS_DURATION_CAP_PER_TAG_S", "2.0"))

N_CTX = int(_env("TTS_N_CTX", "4096"))
N_BATCH = int(_env("TTS_N_BATCH", "128"))
N_UBATCH = int(_env("TTS_N_UBATCH", "128"))
FLASH_ATTN = _env("TTS_FLASH_ATTN", "true").lower() in ("1", "true", "yes")
SNAC_DEVICE = _env("TTS_SNAC_DEVICE", "cuda").lower()

MAX_QUEUE_DEPTH = 16
