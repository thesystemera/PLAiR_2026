import asyncio
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import Lock
from typing import Optional

COLORS = {
    'RESET': '\033[0m',
    'BRIGHT': '\033[1m',

    'BLACK': '\033[30m',
    'RED': '\033[91m',
    'GREEN': '\033[92m',
    'YELLOW': '\033[93m',
    'BLUE': '\033[94m',
    'MAGENTA': '\033[95m',
    'CYAN': '\033[96m',
    'WHITE': '\033[97m',
    'GRAY': '\033[90m',

    'BG_RED': '\033[41m',
    'BG_GREEN': '\033[42m',
    'BG_YELLOW': '\033[43m',
    'BG_BLUE': '\033[44m',
    'BG_MAGENTA': '\033[45m',
    'BG_CYAN': '\033[46m',
    'BG_WHITE': '\033[47m',
}

LOG_CATEGORIES = {
    'error': {'color_fg': 'WHITE', 'color_bg': 'BG_RED', 'bright': False, 'enabled': True},
    'warning': {'color': 'YELLOW', 'enabled': True},
    'info': {'color': 'BLUE', 'enabled': False},
    'success': {'color': 'GREEN', 'enabled': True},
    'debug': {'color': 'GRAY', 'enabled': False},
    'system': {'color': 'CYAN', 'enabled': True},

    'gpt': {'color': 'MAGENTA', 'enabled': False},
    'ai': {'color': 'MAGENTA', 'enabled': False},
    'api': {'color_fg': 'GREEN', 'color_bg': 'BG_BLUE', 'enabled': False},
    'external': {'color': 'CYAN', 'enabled': False},

    'conversation': {'color_fg': 'WHITE', 'color_bg': 'BG_GREEN', 'enabled': False},
    'persona_profile': {'color': 'GREEN', 'enabled': False},
    'user_content': {'color': 'CYAN', 'enabled': True},

    'vector_database': {'color_fg': 'YELLOW', 'color_bg': 'BG_BLUE', 'enabled': True},
    'vector_music': {'color_fg': 'BLACK', 'color_bg': 'BG_MAGENTA', 'enabled': True},
    'commands': {'color_fg': 'WHITE', 'color_bg': 'BG_CYAN', 'enabled': True},
    'filter': {'color_fg': 'BLACK', 'color_bg': 'BG_YELLOW', 'enabled': False},

    'tts_broadcast': {'color_fg': 'WHITE', 'color_bg': 'BG_MAGENTA', 'enabled': True},
    'tts_generation': {'color_fg': 'BLACK', 'color_bg': 'BG_GREEN', 'enabled': True},
    'tts_processing': {'color_fg': 'CYAN', 'color_bg': 'BG_BLUE', 'enabled': True},
    'tts_queue_manager': {'color_fg': 'BLACK', 'color_bg': 'BG_CYAN', 'enabled': True},
    'tts_stream_planner': {'color_fg': 'YELLOW', 'color_bg': 'BG_MAGENTA', 'enabled': True},
    'tts_vector_db': {'color_fg': 'BLACK', 'color_bg': 'BG_YELLOW', 'enabled': True},

    'audio': {'color_fg': 'WHITE', 'color_bg': 'BG_CYAN', 'enabled': False},

    'catalog': {'color': 'GREEN', 'enabled': True},
    'analytics': {'color_fg': 'BLACK', 'color_bg': 'BG_CYAN', 'enabled': False},
    'station': {'color': 'MAGENTA', 'enabled': False},
    'announcer': {'color_fg': 'YELLOW', 'color_bg': 'BG_MAGENTA', 'enabled': False},
    'playback': {'color_fg': 'CYAN', 'color_bg': 'BG_BLUE', 'enabled': True},

    'node_producer': {'color_fg': 'BLACK', 'color_bg': 'BG_GREEN', 'enabled': False},
    'node_registry': {'color_fg': 'CYAN', 'color_bg': 'BG_BLUE', 'enabled': False},
    'node_performance': {'color_fg': 'YELLOW', 'color_bg': 'BG_MAGENTA', 'enabled': False},

    'suno': {'color_fg': 'WHITE', 'color_bg': 'BG_MAGENTA', 'enabled': True},
    'batch_music': {'color_fg': 'CYAN', 'color_bg': 'BG_MAGENTA', 'enabled': True},
    'upscaling': {'color_fg': 'WHITE', 'color_bg': 'BG_BLUE', 'enabled': False},
    'audio_features': {'color_fg': 'YELLOW', 'color_bg': 'BG_BLUE', 'enabled': False},
}

_log_queue: Optional[asyncio.Queue] = None
_log_task: Optional[asyncio.Task] = None
_print_lock = Lock()
_file_logger: Optional[logging.Logger] = None
_file_handler: Optional[RotatingFileHandler] = None

def _setup_file_logger():
    global _file_logger, _file_handler
    if _file_logger is not None:
        return _file_logger

    try:
        from config.settings import settings

        if not settings.ENABLE_FILE_LOGGING:
            print("File logging is disabled via settings.")
            return None

        logs_dir = Path(settings.LOGS_DIR)

        _file_logger = logging.getLogger('plair_radio')
        _file_logger.setLevel(logging.DEBUG)
        _file_logger.propagate = False

        if _file_logger.handlers:
            for handler in _file_logger.handlers[:]:
                handler.close()
                _file_logger.removeHandler(handler)

        log_file = logs_dir / 'radio.log'
        _file_handler = RotatingFileHandler(
            log_file,
            maxBytes=1048576,
            backupCount=5,
            encoding='utf-8',
            delay=True
        )

        formatter = logging.Formatter(
            '%(asctime)s [%(levelname)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        _file_handler.setFormatter(formatter)
        _file_logger.addHandler(_file_handler)
        return _file_logger

    except Exception as e:
        print(f"Warning: Could not setup file logging: {e}")
        return None

def close_file_logger():
    global _file_logger, _file_handler
    if _file_handler:
        try:
            _file_handler.close()
        except Exception:
            pass
        _file_handler = None
    if _file_logger:
        for handler in _file_logger.handlers[:]:
            try:
                handler.close()
                _file_logger.removeHandler(handler)
            except Exception:
                pass
        _file_logger = None

async def start_log_worker():
    global _log_task, _log_queue, _file_logger
    if _log_queue is None:
        _log_queue = asyncio.Queue()
    if _file_logger is None:
        _setup_file_logger()
    if _log_task is None:
        _log_task = asyncio.create_task(_log_worker())

async def _log_worker():
    global _file_logger
    while True:
        try:
            log_entry = await _log_queue.get()
            if log_entry is None:
                break

            category, message = log_entry
            config = LOG_CATEGORIES.get(category, LOG_CATEGORIES['info'])

            if not config['enabled']:
                continue

            if _file_logger:
                try:
                    level_map = {
                        'error': logging.ERROR,
                        'warning': logging.WARNING,
                        'success': logging.INFO,
                        'info': logging.INFO,
                        'debug': logging.DEBUG,
                    }
                    level = level_map.get(category, logging.INFO)
                    _file_logger.log(level, f"[{category.upper()}] {message}")
                except Exception:
                    pass

            with _print_lock:
                header_color = COLORS['BRIGHT']

                if 'color_bg' in config and 'color_fg' in config:
                    header_color += COLORS[config['color_bg']] + COLORS[config['color_fg']]
                elif 'color' in config:
                    header_color += COLORS[config['color']]

                formatted = f"{header_color}[{category.upper()}]{COLORS['RESET']} {COLORS['WHITE']}{message}{COLORS['RESET']}"
                print(formatted)

        except Exception as e:
            print(f"Error in log worker: {e}")

def log(message: str, category: str = "info"):
    global _log_queue
    if _log_queue is None:
        _log_queue = asyncio.Queue()
    try:
        _log_queue.put_nowait((category, message))
    except asyncio.QueueFull:
        pass

def error(msg): log(msg, "error")
def warning(msg): log(msg, "warning")
def info(msg): log(msg, "info")
def success(msg): log(msg, "success")
def debug(msg): log(msg, "debug")
def system(msg): log(msg, "system")

def gpt(msg): log(msg, "gpt")
def ai(msg): log(msg, "ai")
def api(msg): log(msg, "api")
def external(msg): log(msg, "external")

def conversation(msg): log(msg, "conversation")
def persona_profile(msg): log(msg, "persona_profile")
def user_content(msg): log(msg, "user_content")

def vector_music(msg): log(msg, "vector_music")
def commands(msg): log(msg, "commands")
def filter(msg): log(msg, "filter")

def tts_broadcast(msg): log(msg, "tts_broadcast")
def tts_generation(msg): log(msg, "tts_generation")
def tts_processing(msg): log(msg, "tts_processing")
def tts_queue_manager(msg): log(msg, "tts_queue_manager")
def tts_stream_planner(msg): log(msg, "tts_stream_planner")
def tts_vector_db(msg): log(msg, "tts_vector_db")

def audio(msg): log(msg, "audio")

def catalog(msg): log(msg, "catalog")
def analytics(msg): log(msg, "analytics")
def station(msg): log(msg, "station")
def announcer(msg): log(msg, "announcer")
def playback(msg): log(msg, "playback")

def node_producer(msg): log(msg, "node_producer")
def node_registry(msg): log(msg, "node_registry")
def node_performance(msg): log(msg, "node_performance")

def suno(msg): log(msg, "suno")
def batch_music(msg): log(msg, "batch_music")
def upscaling(msg): log(msg, "upscaling")
def audio_features(msg): log(msg, "audio_features")

async def stop_worker():
    global _log_task, _log_queue
    if _log_task and _log_queue:
        await _log_queue.put(None)
        try:
            await _log_task
        except asyncio.CancelledError:
            pass
        _log_task = None
    close_file_logger()