# Dead Code Cleanup - January 2026

This document records dead/unused functions removed from the Python backend during a codebase cleanup. These were either:
- Half-implemented features that were never wired up
- Deprecated code replaced by newer implementations
- Utility functions that lost all callers over time

If you need to re-implement any of these features, this serves as a reference for what existed.

## Summary

- **Lines removed:** ~850
- **Files affected:** 26
- **Commit:** `8f8b50e` - "Cleanup: Remove 850+ lines of dead Python code"

---

## Removed Functions by File

### `server/services/log_service.py`
| Function | Description | Re-implementation Notes |
|----------|-------------|------------------------|
| `alog`, `aerror`, `awarning`, `ainfo`, `asuccess`, `adebug` (x24 total) | Async wrappers for all log functions | Were redundant - sync logging works fine in async context |
| `vector_database` | Sync logging for vector DB | Use `log_service.vector()` instead |

### `server/services/api_utils.py`
| Function | Description | Re-implementation Notes |
|----------|-------------|------------------------|
| `async_retry_decorator` | Decorator for retrying async functions | Simple retry logic, ~30 lines |
| `async_with_retry` | Context manager for retry logic | Similar to decorator |

### `server/services/analytics_file_service.py`
| Function | Description | Re-implementation Notes |
|----------|-------------|------------------------|
| `read_track_analytics` | Read JSON analytics for a track | Simple file read, check `write_track_analytics` for format |
| `get_all_track_ids` | List all track IDs with analytics | Glob `*.json` in tracks_dir |

### `server/services/analytics_service.py`
| Function | Description | Re-implementation Notes |
|----------|-------------|------------------------|
| `log_shoutout_play_event` | Track shoutout plays in analytics | Was never wired to playback |
| `rebuild_from_json` | Rebuild analytics from JSON exports | Batch import functionality |

### `server/services/artwork_generation_service.py`
| Function | Description | Re-implementation Notes |
|----------|-------------|------------------------|
| `batch_generate_missing_artwork` | Generate artwork for all tracks missing it | Iterate tracks, call `generate_artwork` |
| `is_model_loaded` | Check if Flux model is loaded | VRAM management |
| `unload_model` | Unload Flux model from VRAM | VRAM management |

### `server/services/audio_features_service.py`
| Function | Description | Re-implementation Notes |
|----------|-------------|------------------------|
| `get_features_path` | Get path to audio features JSON | `settings.AUDIOFEATURES_DIR / f"{track_id}.json"` |

### `server/services/audio_lyrical_timestamp_service.py`
| Function | Description | Re-implementation Notes |
|----------|-------------|------------------------|
| `set_model` | Switch Whisper model size | Model hot-swapping |
| `get_timestamps_path` | Get path to timestamps JSON | `settings.LYRIC_TIMESTAMPS_DIR / f"{track_id}.json"` |
| `batch_generate` | Generate timestamps for multiple tracks | Iterate and call `generate_timestamps` |

### `server/services/audio_master_service.py`
| Function | Description | Re-implementation Notes |
|----------|-------------|------------------------|
| `analyze_loudness` | Analyze LUFS loudness of audio file | Uses pyloudnorm, ~40 lines |
| `master_audio_batch` | Master multiple audio files | Iterate and call `master_audio` |

### `server/services/audio_transcoding_service.py`
| Function | Description | Re-implementation Notes |
|----------|-------------|------------------------|
| `get_or_create_opus_shoutout` | Convert shoutout to Opus format | Similar to track Opus conversion |

### `server/services/device_management_service.py`
| Function | Description | Re-implementation Notes |
|----------|-------------|------------------------|
| `activate_device` | Activate a device for playback | Sets `is_active=True` in DB, handled differently now |
| `update_device_activity` | Update device last_active timestamp | Simple DB update |

### `server/services/playback_state.py`
| Function | Description | Re-implementation Notes |
|----------|-------------|------------------------|
| `set_override` | Set playback override mode | Was for DJ takeover feature |

### `server/services/preferences_cache_service.py`
| Function | Description | Re-implementation Notes |
|----------|-------------|------------------------|
| `clear_cache` | Clear entire preference cache | `self.cache.clear()` |

### `server/services/rate_limit_service.py`
| Function | Description | Re-implementation Notes |
|----------|-------------|------------------------|
| `cleanup_old_data` | Remove old rate limit data | Cleanup for memory management |

### `server/services/source_quality_analysis_service.py`
| Function | Description | Re-implementation Notes |
|----------|-------------|------------------------|
| `should_skip_processing` | Check if processing should be skipped | Inverse of `should_apply_processing` |

### `server/services/suno_artwork_enrichment_service.py`
| Function | Description | Re-implementation Notes |
|----------|-------------|------------------------|
| `has_enriched_artwork` | Check if enriched artwork exists | `get_enriched_path() is not None` |
| `get_enriched_path` | Get path to enriched artwork | Check `enriched_dir / f"{id}.jpeg"` |

### `server/services/suno_generation_queue_service.py`
| Function | Description | Re-implementation Notes |
|----------|-------------|------------------------|
| `get_generation_stats` | Get generation statistics | Returns `self.generation_stats.copy()` |

### `server/services/suno_metadata_service.py`
| Function | Description | Re-implementation Notes |
|----------|-------------|------------------------|
| `get_metadata_path` | Get path to metadata JSON | `self.metadata_dir / f"{id}.json"` |

### `server/services/suno_service_orchestrator.py`
| Function | Description | Re-implementation Notes |
|----------|-------------|------------------------|
| `shutdown` | Graceful orchestrator shutdown | Cancel consumer tasks, await completion |

### `server/services/track_artwork_service.py`
| Function | Description | Re-implementation Notes |
|----------|-------------|------------------------|
| `get_artwork_info` | Get artwork info dict for track | Returns URLs, has_enriched status |

### `server/services/user_content_database_service.py`
| Function | Description | Re-implementation Notes |
|----------|-------------|------------------------|
| `get_shoutouts` | Get paginated shoutouts list | Filter by user, paginate, enrich |
| `get_recent_shoutouts` | Get shoutouts sorted by recency/distance | Time + geo scoring algorithm |
| `format_shoutouts_for_broadcast` | Format shoutouts for DJ context | Text formatting for AI prompt |
| `calculate_distance` | Haversine distance calculation | Standard geo distance formula |
| `finalize_shoutout` | Mark shoutout as finalized | Was part of multi-step flow |
| `get_parent_shoutout` | Get parent of a reply shoutout | Reply threading |

### `server/services/websocket_service.py`
| Function | Description | Re-implementation Notes |
|----------|-------------|------------------------|
| `get_session_connections` | Get all connections for a session | Return `self.sessions.get(id, set())` |
| `broadcast_playback_override` | Broadcast override state | Special broadcast type |

### `server/services_radio/announcer_service.py`
| Function | Description | Re-implementation Notes |
|----------|-------------|------------------------|
| `stop_monitoring_session` | Stop announcer for a session | Cleanup tasks and caches |
| `get_crossfade_timing` | Get cached crossfade timing | Lookup in transition_cache |

### `server/services_radio/context_service.py`
| Function | Description | Re-implementation Notes |
|----------|-------------|------------------------|
| `get_user_local_time` | Get user's local time from timezone | Async version, use `format_user_time_str` instead |

### `server/services_radio/dj_prompt_helper_service.py`
| Function | Description | Re-implementation Notes |
|----------|-------------|------------------------|
| `convert_ms_to_seconds_and_words` | Convert duration to estimated word count | `seconds * 2.5` for word estimate |

### `server/services_radio/external_web_service.py`
| Function | Description | Re-implementation Notes |
|----------|-------------|------------------------|
| `retrieve_lyrics` | Fetch lyrics from Musixmatch | API integration, needs API key |

---

## Detection Tool

A custom dead code finder script was created at `server/utils/find_dead_functions.py`. It:
- Uses AST to find function/method definitions
- Searches for calls using regex patterns
- Handles callbacks (e.g., `obj.method,` without parentheses)
- Filters out dynamic patterns (decorators, FastAPI routes, context nodes)

Run with:
```bash
python server/utils/find_dead_functions.py
```

## False Positives to Ignore

The script may report these as dead, but they're actually used:
- **Nested callbacks** - Functions defined inside other functions and passed to executors
- **Decorators** - `@gpt_error_handler` appears as function definition
- **Context nodes** - Registered dynamically at runtime via `@register_node`
- **Stripe service** - Kept for future payment integration
