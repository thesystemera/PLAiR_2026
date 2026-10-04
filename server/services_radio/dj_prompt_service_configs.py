"""DJ prompt configuration: which context nodes each prompt type uses (build_node_configs), segment kinds and their
stand-in lines, and the GPT error wrapper."""
import re
from services import log_service


PARTIAL_SIGN_OFF = re.compile(r"\b(partial|partly|incomplete|not done)\b", re.IGNORECASE)

BROADCAST_CUE = "Write the script for this segment now, following the instructions above."

RADIO_SEGMENT_MARKUP_NOTE = (
    "\n\nMARKUP DISCIPLINE: Tildes are reserved for ~paralanguage~ sound cues. Never write asterisks; use "
    "CAPITALS for emphasis. Every ~, %, @, & and $ must belong to a complete tag."
)
SCRIPT_PROVIDER_NOTES = {"deepseek": RADIO_SEGMENT_MARKUP_NOTE}

PERSONAL_NODE_NEUTRAL_PREFIXES = {
    'user_persona': ("LISTENER PERSONA: Guest",),
    'user_profile': ("LISTENER PROFILE: Guest",),
    'conversation_recent': ("CONVERSATION HISTORY: None", "CONVERSATION HISTORY: No session", "CONVERSATION HISTORY: Error"),
}

INTERPRETATION_CACHE_NODES = {
    'news': ('instruction_news', 'data_news_report', 'user_basic', 'segment_length'),
    'weather': ('instruction_weather', 'data_weather_report', 'segment_length'),
    'biography': ('instruction_biography', 'data_biography', 'segment_length'),
    'lyrics': ('instruction_lyrics', 'data_lyrics', 'segment_length'),
}
INTERPRETATION_TYPES = ('biography', 'lyrics', 'news', 'weather', 'location_search', 'events', 'shoutouts')
HOURLY_INTERPRETATIONS = {'weather'}

NA_MARKER = "[N/A]"

RADIO_SEGMENT_BASE_NODES = [
    'core_dj_identity',
    'format_channels',
    'format_tone',
    'format_performance_tags_guide',
    'format_performance_tag_examples',
    'format_roles_detailed',
    'format_station_characteristics',
    'format_dialogue_examples',
    'instruction_radio_segment',
    'data_radio_segment',
    'user_local_time'
]

SEGMENT_DATA_NODES = {
    'news': 'data_news_report',
    'weather': 'data_weather_report',
    'location_search': 'data_location_report',
    'events': 'data_events_report',
    'biography': 'data_biography',
    'lyrics': 'data_lyrics',
}
SEGMENT_HOSTS = {'weather': ('JESS', 'LEO')}
SEGMENT_SUBJECTS = {
    'news': ('the news wire', 'the news'),
    'weather': ('the weather feed', 'the weather'),
    'location_search': ('the places lookup', 'places nearby'),
    'events': ('the events listings', 'events'),
    'biography': ("that artist's backstory", 'that biography'),
    'lyrics': ('those lyrics', 'those lyrics'),
}
LOCATION_SEGMENTS = {
    'weather': ('your forecast', True),
    'location_search': ('spots near you', True),
    'events': ('events near you', False),
}
UNAVAILABLE_SEGMENT_LINES = (
    "[BROADCAST] [{host}] &0.2& ~sighs~ Damn, {subject} just came back empty on us. &0.2& Nothing to report right "
    "now, so give it a minute and ask again.\n[{cohost}] &0.3& ~chuckles~ Pirate radio, baby. Held together with duct tape.",
    "[BROADCAST] [{host}] &0.2& ~groans~ Ugh, nothing's coming through on {subject} right now. &0.2& Not gonna make "
    "stuff up, so ask us again in a bit.\n[{cohost}] &0.3& ~laughs~ Honest radio. What a concept.",
)
NO_LOCATION_SEGMENT_LINES = (
    "[TXT] [{host}] &0.2& ~clears throat~ I can't pull up {subject} without knowing where you're tuned in from. "
    "&0.2& {fix}\n[{cohost}] &0.3& ~chuckles~ We're pirates, not psychics.",
)
NO_LOCATION_FIX_GUEST = "Let the app use your location, then ask me again."
NO_LOCATION_FIX_USER = "Set your location in your profile and ask me again."

def gpt_error_handler(func):
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            log_service.error(f"GPT error in {func.__name__}: {e}")
            if hasattr(func, '__annotations__') and 'return' in func.__annotations__:
                return_type = func.__annotations__['return']
                if hasattr(return_type, '__origin__') and return_type.__origin__ is tuple:
                    num_values = len(return_type.__args__)
                    return tuple([None] * num_values)
            return None

    return wrapper

def build_node_configs() -> dict:
    configs = {
        'interactive_tools': {
            'required_nodes': [
                'core_dj_identity',
                'format_channels',
                'format_tone',
                'format_performance_tags_guide',
                'format_performance_tag_examples',
                'format_roles_detailed',
                'format_station_characteristics',
                'format_dialogue_examples',
                'guidelines_general',
                'guidelines_critical',
                'guidelines_internal_dialogue',
                'instruction_dj_tools',
                'tool_guidance',
                'station_recent_airings',
                'studio_clock',
                'queue_playlist',
                'city_pulse'
            ],
            'use_ai_picker': True
        },
        'biography': {
            'required_nodes': [
                'core_dj_identity',
                'format_channels',
                'format_tone',
                'format_performance_tags_guide',
                'format_performance_tag_examples',
                'format_roles_detailed',
                'format_station_characteristics',
                'format_dialogue_examples',
                'instruction_biography',
                'data_biography',
                'user_local_time',
                'user_persona',
                'user_profile',
                'conversation_recent'
            ],
            'use_ai_picker': False
        },
        'lyrics': {
            'required_nodes': [
                'core_dj_identity',
                'format_channels',
                'format_tone',
                'format_performance_tags_guide',
                'format_performance_tag_examples',
                'format_roles_detailed',
                'format_station_characteristics',
                'format_dialogue_examples',
                'instruction_lyrics',
                'data_lyrics',
                'user_local_time',
                'user_persona',
                'user_profile',
                'conversation_recent'
            ],
            'use_ai_picker': False
        },
        'news': {
            'required_nodes': [
                'core_dj_identity',
                'format_channels',
                'format_tone',
                'format_performance_tags_guide',
                'format_performance_tag_examples',
                'format_roles_detailed',
                'format_station_characteristics',
                'format_dialogue_examples',
                'instruction_news',
                'data_news_report',
                'user_local_time',
                'user_basic',
                'weather_current',
                'user_persona',
                'user_profile',
                'conversation_recent'
            ],
            'use_ai_picker': False
        },
        'weather': {
            'required_nodes': [
                'core_dj_identity',
                'format_channels',
                'format_tone',
                'format_performance_tags_guide',
                'format_performance_tag_examples',
                'format_roles_detailed',
                'format_station_characteristics',
                'format_dialogue_examples',
                'instruction_weather',
                'data_weather_report',
                'user_local_time',
                'conversation_recent'
            ],
            'use_ai_picker': False
        },
        'location_search': {
            'required_nodes': [
                'core_dj_identity',
                'format_channels',
                'format_tone',
                'format_performance_tags_guide',
                'format_performance_tag_examples',
                'format_roles_detailed',
                'format_station_characteristics',
                'format_dialogue_examples',
                'instruction_location_search',
                'data_location_report',
                'user_local_time',
                'user_basic',
                'weather_current',
                'user_persona',
                'user_profile',
                'conversation_recent'
            ],
            'use_ai_picker': False
        },
        'events': {
            'required_nodes': [
                'core_dj_identity',
                'format_channels',
                'format_tone',
                'format_performance_tags_guide',
                'format_performance_tag_examples',
                'format_roles_detailed',
                'format_station_characteristics',
                'format_dialogue_examples',
                'instruction_events',
                'data_events_report',
                'user_local_time',
                'user_basic',
                'weather_current',
                'user_persona',
                'user_profile',
                'conversation_recent'
            ],
            'use_ai_picker': False
        },
        'shoutouts': {
            'required_nodes': [
                'core_dj_identity',
                'format_channels',
                'format_tone',
                'format_performance_tags_guide',
                'format_performance_tag_examples',
                'format_roles_detailed',
                'format_station_characteristics',
                'format_dialogue_examples',
                'instruction_shoutouts',
                'data_shoutouts_data',
                'user_local_time',
                'user_basic',
                'weather_current',
                'conversation_recent'
            ],
            'use_ai_picker': False
        },
        'announcements': {
            'required_nodes': [
                'core_dj_identity',
                'format_channels',
                'format_tone',
                'format_performance_tags_guide',
                'format_performance_tag_examples',
                'format_roles_detailed',
                'format_station_characteristics',
                'format_dialogue_examples',
                'instruction_announcements',
                'station_recent_airings',
                'listener_notes',
                'bank_talking_points'
            ],
            'use_ai_picker': False,
            'time_presets': {
                'minimal': {
                    'max_time': 5,
                    'nodes': [
                        'track_title_artist', 'queue_next_track',
                        'user_local_time', 'conversation_recent'
                    ]
                },
                'quick': {
                    'max_time': 10,
                    'nodes': [
                        'track_title_artist', 'track_style_description',
                        'queue_next_track', 'queue_next_details',
                        'user_local_time', 'user_basic', 'weather_current',
                        'station_current_show', 'conversation_recent'
                    ]
                },
                'standard': {
                    'max_time': 15,
                    'nodes': [
                        'track_title_artist', 'track_duration', 'track_style_description', 'track_audio_features_full',
                        'queue_next_track', 'queue_next_details',
                        'history_last_track',
                        'station_previous_show', 'station_current_show', 'station_next_show',
                        'user_local_time', 'user_basic', 'weather_current',
                        'user_favorite_artists', 'conversation_recent'
                    ]
                },
                'full': {
                    'max_time': 20,
                    'nodes': [
                        'track_title_artist', 'track_release_date', 'track_duration',
                        'track_vocal_info', 'track_style_description', 'track_audio_features_full',
                        'queue_next_track', 'queue_next_details', 'queue_next_audio_features',
                        'history_last_track', 'history_last_audio_features',
                        'station_previous_show', 'station_current_show', 'station_next_show',
                        'user_local_time', 'user_basic', 'weather_current',
                        'user_favorite_artists', 'user_banned_tracks',
                        'instruction_shoutouts', 'data_shoutouts_data',
                        'conversation_recent'
                    ]
                },
                'extended': {
                    'max_time': 25,
                    'nodes': [
                        'track_title_artist', 'track_release_date', 'track_duration',
                        'track_vocal_info', 'track_style_description', 'track_audio_features_full',
                        'track_progress', 'track_lyrics_preview',
                        'queue_next_track', 'queue_next_details', 'queue_next_audio_features',
                        'queue_upcoming_track', 'queue_upcoming_audio_features',
                        'history_last_track', 'history_last_audio_features',
                        'station_previous_show', 'station_current_show', 'station_next_show',
                        'user_local_time', 'user_basic', 'weather_current',
                        'user_favorite_artists', 'user_banned_tracks',
                        'instruction_shoutouts', 'data_shoutouts_data',
                        'conversation_recent'
                    ]
                },
                'everything': {
                    'max_time': 999,
                    'nodes': [
                        'track_title_artist', 'track_release_date', 'track_duration',
                        'track_vocal_info', 'track_style_description', 'track_audio_features_full',
                        'track_progress', 'track_lyrics_preview',
                        'queue_next_track', 'queue_next_details', 'queue_next_audio_features',
                        'queue_upcoming_track', 'queue_upcoming_audio_features',
                        'history_last_track', 'history_last_audio_features',
                        'station_previous_show', 'station_current_show', 'station_next_show',
                        'user_local_time', 'user_basic', 'weather_current',
                        'user_favorite_artists', 'user_banned_tracks',
                        'instruction_shoutouts', 'data_shoutouts_data',
                        'conversation_recent'
                    ]
                }
            }
        },
        'radio_segment': {
            'required_nodes': RADIO_SEGMENT_BASE_NODES + [
                'station_recent_airings',
                'listener_notes'
            ],
            'use_ai_picker': False
        },
        'radio_segment_shared': {
            'required_nodes': list(RADIO_SEGMENT_BASE_NODES),
            'use_ai_picker': False
        },
    }
    for kind in INTERPRETATION_TYPES:
        configs[kind]['required_nodes'].append('segment_length')
    return configs


