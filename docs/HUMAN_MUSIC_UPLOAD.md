# Human Music Uploads

PLAiR is moving from mostly AI music to mostly human music. Uploading is built around least resistance: pick a file and tap Upload; everything else is automatic and can be corrected afterwards.

## Principles

- **Same catalog as AI tracks.** Human tracks live in the `tracks` table with `is_ai_generated = 0` and use the same metadata schema, streaming, mastering, search and radio code.
- **Automatic first, editable always.** Title, genre, moods, lyrics, explicit flag, artwork and mastering are decided automatically; every one of them can be edited from the upload window or later from User → My Uploads.
- **Every click we don't need is a good thing.** The only required input is a one-time rights confirmation on a user's first upload.

## Flow

1. **Select** (`UploadMusicModal.jsx`): pick or drop a file. One compact row: artist (the user's artist profiles, defaults to the last one used), "Enhance audio" (remembered opt-in, Apollo restoration), and on the first upload only "I made this music or have the rights to share it".
2. **Send**: XHR with real byte progress and Cancel. Files are refused before the body is read unless the request carries a valid token (`security_middleware.SIGNED_IN_UPLOAD_PATHS`).
3. **Accept**: `POST /api/user/music/upload` checks format, size and rights, resolves the artist, and answers `{"status": "processing", "upload_id"}` at once.
4. **Process** in a background job (`UPLOAD_JOBS` in `routers/user_music.py`): progress over the `upload_progress` WebSocket event, `GET /api/user/music/uploads[/{id}]` for status, `DELETE` to cancel (partial files are rolled back). The user can close the window; `UploadNotice` (AppBridges) toasts when the track is live. The final event is sent only after the job's result is stored.
5. **Preview / edit**: the result opens in the same editor used from My Uploads.

## Pipeline (`human_music_upload_service.py`)

Validate → save original → probe/decode → **fingerprint + duplicate check** → source quality → **Gemini analysis** → Apollo (only when the user opted in and the source needs it) → vocal cleanup (Demucs + ClearVoice, only when Gemini asks) → SonicMaster → mastering (-14 LUFS) → audio features → lyric timing (Whisper aligns Gemini's lyrics) → Opus 128/192/256k + catalog MP3 → artwork (embedded, else SDXL) + depth map → metadata JSON → catalog DB + memory → search index. Any fatal failure rolls the track back. Measured on a 3-minute song: 2.5-8 minutes, dominated by SonicMaster, mastering, lyric timing and artwork.

**Duplicates:** every human upload stores a Chromaprint `fingerprint` (ffmpeg's built-in chromaprint muxer, first 120 s, `services/audio_fingerprint.py`). Right after decoding, a match at similarity ≥ 0.8 returns the uploader's existing track, or refuses a song another listener already uploaded. Measured on real re-uploads: same song 0.94-1.0 (including original vs mastered copy), different songs ≤ 0.55. Exact re-uploads are also caught by SHA-256 before any work.

## Credits and artists

- **Artist profiles** (`artist_profiles`, `services/artist_profile_service.py`, `routers/artists.py`): a user defines their bands once (User → Artists & Bands: name, bio, links). Uploads are credited to the chosen profile, else the last used, else the first, else one created from the username. Renaming a profile re-credits all its tracks. Now Playing shows an "About <artist>" card under the title for tracks with a profile (bio, links, more from this artist; `GET /api/artists/{id}`, public tracks only). AI tracks have no profile, so no card.
- **The credit** is `generation_params.artist_name` (mirrored in `track_info.artist`, plus `artist_profile_id` and `artist_slug`). Every view reads it. `derived_tags.inspired_artist` is only a "sounds like" comparison, never the credit. The DJ is told the track is by an independent human artist.
- **Titles**: the uploader's title, else the embedded tag title (mutagen, stored as `embedded_tags`), else a real title in the filename, else the sung hook. Gemini receives the tags and filename as known facts.

## Sharing and flags

- `visibility`: public (default), unlisted (link only) or private (owner only). Unlisted and private tracks are in `CatalogDatabaseService.hidden_ids` and are left out of browsing, vector search, queue fill, top hits, charts, on-air stats and new-session seeding; they still play by id.
- `explicit`: set by Gemini from the lyrics, editable.
- Rights: `users.upload_rights_confirmed_at`, asked once.

## Human, AI or both

Human tracks are ordinary catalog tracks; the only difference is `is_ai_generated = 0`, shown as the existing "100% Human" / "100% AI" tag on the Now Playing cover. Each listener has one setting, Music: Both (default) / Human / AI (User → Radio settings, radio pref `music_source`), and `services/listener_filters.excluded_ids` applies it with bans and hidden tracks everywhere tracks are picked: queue and radio fill, catalog listing, semantic search, the DJ's searches and City Pulse music. The DJ is told when a track is by an independent human artist.

## Analysis model

`GEMINI_UPLOAD_ANALYSIS_MODEL` (default `gemini-3.5-flash`) via `human_metadata_extraction_service.py`, audio inline (≤ 20 MB; long or float sources are sent as an MP3 of the first 15 minutes), structured output (`UploadAnalysis` response schema). A/B on 10 real human songs (2026-09-29, `tests/upload_llm_ab.py`):

| Model | OK | Avg time | Cost / track | Notes |
|---|---|---|---|---|
| gemini-3.5-flash | 10/10 | 20 s | $0.036 | specific genres, confident mix decisions |
| gemini-3.5-flash-lite | 10/10 | 7 s | $0.005 | "Alternative Rock" for 6/10, timid mix decisions |
| gemini-2.5-flash | 8/10 | 65 s | $0.042 | 2 runaway replies (4 min, $0.17 each) |

3.5 Flash stays: genre and mix decisions shape both the sound and discovery. DeepSeek has no audio input.

## Editing and deleting

- `PUT /api/user/music/tracks/{id}`: title, artist_profile_id, primary_genre, secondary_genres, mood_keywords, lyrics (empty = instrumental; drops the lyric timing so the asset doctor re-aligns it), visibility, explicit, description. Validated, written atomically, re-indexed.
- `DELETE /api/user/music/tracks/{id}`: removes the catalog row, memory entry, all outputs, originals, stems, and likes/bans of that track; the search index drops it on its next rebuild.

## Tools and tests

- `tests/upload_credit_test.py --base URL --file <audio> [--cancel-file <other audio>]`: full pipeline end to end (credit, edits, rename, duplicate, cancel).
- `tests/upload_llm_ab.py --models a,b --limit N`: analysis model comparison on real uploads.
- `server/utils/backfill_upload_credits.py --user N --artist NAME [--titles] [--apply]`: fix credits and titles on old uploads (dry run by default).
