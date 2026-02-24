# Human Music Upload System

## Overview

The human music upload system allows users to upload their own music to the PLAiR.fm catalog. **Critically, uploaded human tracks are treated identically to AI-generated tracks** - they use the same database schema, the same playback systems, the same queue mechanics, and the same metadata structure. This DRY approach ensures consistency and reduces maintenance burden.

## Architecture Principle: Same as AI Catalog

**Human uploads are NOT a separate system.** They are first-class citizens in the existing catalog:

```
AI-Generated Track (Suno)     Human Upload
        │                           │
        └───────────┬───────────────┘
                    │
                    ▼
            Same Database Table
            Same Metadata Schema
            Same Playback System
            Same Queue System
            Same Search/Discovery
            Same Artwork Pipeline
```

### Key Design Decisions

1. **Single `tracks` Table**: Human uploads go in the same table as AI tracks
   - `is_ai_generated` column distinguishes them (0 = human, 1 = AI)
   - `uploaded_by_user_id` links to the uploader (NULL for AI tracks)

2. **Same Metadata Schema**: `generation_params` and `derived_tags` work identically
   - AI tracks: metadata comes from Suno generation
   - Human tracks: metadata extracted by Gemini Pro audio analysis

3. **Same ID Format**: Track IDs use consistent hashing
   - AI: `{suno_clip_id}`
   - Human: `user_{user_id}_{timestamp}_{hash}`

4. **Same File Structure**: Audio files stored in same location
   - `/catalog/audio/{track_id}.opus`
   - `/catalog/artwork/{track_id}.jpg`

## Current Implementation

### Backend Services

| Service | File | Purpose |
|---------|------|---------|
| `HumanMusicUploadService` | `server/services/human_music_upload_service.py` | Upload pipeline orchestration (10 stages) |
| `HumanMetadataExtractionService` | `server/services/human_metadata_extraction_service.py` | Gemini Pro audio analysis |
| `SourceQualityAnalysisService` | `server/services/source_quality_analysis_service.py` | Source quality detection & processing recommendations |
| `EmbeddedArtworkService` | `server/services/embedded_artwork_service.py` | Extract artwork from MP3/FLAC/etc |
| `ArtworkGenerationService` | `server/services/artwork_generation_service.py` | **NEW** SDXL Lightning AI artwork generation |
| `TrackArtworkService` | `server/services/track_artwork_service.py` | Manual artwork upload for tracks |
| `AudioMasterService` | `server/services/audio_master_service.py` | Loudness normalization (-14.0 LUFS) |
| `AudioFeaturesService` | `server/services/audio_features_service.py` | Tempo, beats, sections extraction |
| `ArtworkEnrichmentService` | `server/services/suno_artwork_enrichment_service.py` | Depth map generation for parallax |

### API Endpoints

```
POST   /api/user/music/upload                    Upload a track (multipart/form-data)
GET    /api/user/music/tracks                    List user's uploaded tracks
PUT    /api/user/music/tracks/{id}               Update track metadata
DELETE /api/user/music/tracks/{id}               Delete uploaded track
POST   /api/user/music/tracks/{id}/artwork       Upload artwork for a track
DELETE /api/user/music/tracks/{id}/artwork       Delete artwork for a track
POST   /api/user/music/tracks/{id}/artwork/generate  Generate AI artwork (SDXL Lightning)
```

### Upload API Response

The upload endpoint returns rich metadata from Gemini analysis:

```json
{
  "status": "success",
  "message": "Successfully uploaded: Track Title (Genre)",
  "track_id": "user_123_1705123456_abc12345",
  "metadata": {
    "title": "Track Title",
    "artist": "Artist Name",
    "style": "Detailed production style description...",
    "primary_genre": "Indie Folk",
    "secondary_genres": ["Singer-Songwriter", "Acoustic"],
    "mood_keywords": ["melancholic", "introspective"],
    "similar_artists": ["Elliott Smith", "Nick Drake"],
    "vocal_style_keywords": ["soft", "breathy"],
    "duration_ms": 180000,
    "has_lyrics": true,
    "transcribed_lyrics": "Full lyrics text...",
    "lyrical_interpretation": "Themes of isolation and longing..."
  }
}
```

### Frontend Components

| Component | File | Purpose |
|-----------|------|---------|
| `UploadMusicModal` | `client/src/components/modals/UploadMusicModal.jsx` | Upload UI with drag/drop |
| MediaSearch upload button | `client/src/components/MediaSearch.jsx` | Quick access in search bar |
| User panel uploads section | `client/src/components/User.jsx` | "My Uploads" accordion |

### Modal Architecture

The upload modal follows the app's standard modal pattern (same as ShoutoutModal, GenerationModal, etc.):

1. **State lives in UIStateContext** (not local component state)
   - `uploadModalOpen` - boolean state
   - `openUploadModal()` - function to open
   - `closeUploadModal()` - function to close

2. **Modal rendered at App.jsx level** (not inside triggering component)
   - Required for proper mobile positioning
   - Prevents CSS transform issues with `position: fixed`

3. **Components just call `openUploadModal()`**
   - MediaSearch.jsx: Green upload button in search bar
   - User.jsx: "Upload Your Music" button in My Uploads section

4. **Uses shared Modal components** from `Modal.jsx`:
   - `ModalSection` - titled sections
   - `ModalCard` - content containers
   - `ModalOptionButton` - toggleable options (used for Apollo upscaling)
   - `ModalButton` - action buttons
   - `ModalFooter` - footer with actions

### Preview Screen Fields

After upload, the modal shows a preview with all extracted metadata:

- **Track Info**: Title, Artist (editable)
- **Genre & Classification**: Primary genre (editable), secondary genres
- **Mood & Vibe**: Mood keywords, similar artists
- **Production Style**: Detailed sonic description
- **Vocal Style**: Vocal characteristics tags
- **Lyrics**: Lyrical interpretation + full transcribed lyrics (scrollable)

### Metadata Extraction (Gemini Pro)

Human uploads use Google's Gemini 2.5 Pro model to analyze audio and extract:

- **Title** (if not provided by user)
- **Primary Artist** (detected or user-provided)
- **Primary Genre** (e.g., "Roots Reggae", "Indie Folk", "Industrial Rock")
- **Secondary Genres** (sub-genres, tags)
- **Mood Keywords** (emotional descriptors)
- **Similar Artists** (for discovery/recommendations)
- **Vocal Style** (if vocals present)
- **Production Style** (sonic characteristics)
- **Lyrical Themes** (if lyrics detected)
- **Transcribed Lyrics** (via Whisper)

**Cost**: ~$0.002 per 3-minute track (25 tokens/sec audio, $2.50/1M tokens)

**Why Gemini Pro over Flash**: Testing showed Flash Lite misclassified genres frequently (e.g., Roots Reggae → "Urban Funk"). Pro provides dramatically better accuracy for genre classification.

## Database Schema

The `tracks` table includes these columns for human upload support:

```sql
-- Added columns for human uploads
is_ai_generated INTEGER DEFAULT 1      -- 0 = human, 1 = AI
uploaded_by_user_id INTEGER DEFAULT NULL  -- FK to users table
```

Metadata JSON structure is identical to AI tracks:

```json
{
  "generation_params": {
    "title": "Track Title",
    "style": "Genre / Style description",
    "prompt": "Lyrics if available",
    "primary_artist": "Artist Name"
  },
  "derived_tags": {
    "primary_genre": "Indie Folk",
    "secondary_genres": ["Singer-Songwriter", "Acoustic"],
    "mood_keywords": ["melancholic", "introspective"],
    "similar_artists": ["Elliott Smith", "Nick Drake"],
    "vocal_style_keywords": ["soft", "breathy"],
    "production_style_description": "Sparse acoustic arrangement...",
    "lyrical_interpretation": "Themes of isolation..."
  }
}
```

## Upload Flow (Full Processing Pipeline)

Human uploads now receive the **SAME processing pipeline** as AI-generated tracks. This ensures consistent quality across the catalog.

```
User selects file
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│ STAGE 1: Validation & Storage (5-10%)                       │
│ • Validate format (mp3, wav, flac, ogg, m4a, aac, opus)     │
│ • Max 100MB, 30sec-15min duration                           │
│ • Store original: /catalog/users/{user_id}/tracks/          │
└────────┬────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│ STAGE 2: Source Quality Analysis (10-15%)                   │
│ • Analyze sample rate, bit depth, format                    │
│ • Spectral analysis (bandwidth utilization)                 │
│ • Detect compression artifacts                              │
│ • Classify: STUDIO | HIGH | MEDIUM | LOW                    │
│ • Generate processing recommendations                       │
└────────┬────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│ STAGE 3: Metadata Extraction (15-30%)                       │
│ • Gemini Pro multimodal audio analysis                      │
│ • Extract: genre, mood, artists, lyrics, production style   │
└────────┬────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│ STAGE 4: Artwork Extraction/Generation (30-35%)             │
│ • First: Extract embedded artwork (MP3 ID3, FLAC, M4A, OGG) │
│ • Supports: APIC frames, FLAC PICTURE blocks, MP4 covr      │
│ • Fallback: Generate artwork using SDXL Lightning           │
│   - Uses track metadata (title, genre, mood) to build prompt│
│   - Produces 1024x1024 album-style artwork in ~1 second     │
│ • Save to: /catalog/artwork/{track_id}.jpeg                 │
└────────┬────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│ STAGE 5: Smart Audio Enhancement (35-55%)                   │
│ • Based on quality tier from Stage 2:                       │
│   - STUDIO/HIGH: Skip Apollo (preserve original quality)    │
│   - MEDIUM: Apply Apollo bandwidth restoration              │
│   - LOW: Full enhancement pipeline                          │
│ • Apollo: Bandwidth restoration neural network              │
└────────┬────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│ STAGE 6: Final Mastering (55-65%)                           │
│ • AudioMasterService (same as Suno tracks)                  │
│ • Target: -14.0 LUFS loudness normalization                 │
│ • Adaptive notch filtering (remove resonant peaks)          │
│ • Multiband tonality analysis (body/presence/air)           │
│ • Output: /catalog/master_wav/{track_id}.wav                │
└────────┬────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│ STAGE 7: Audio Features Extraction (65-75%)                 │
│ • AudioFeaturesService (same as Suno tracks)                │
│ • Extract: tempo, beats, sections, key, mode, loudness      │
│ • Crossfade points (beat-aligned)                           │
│ • Announcer safe zones (for DJ talk-over)                   │
│ • Output: /catalog/audiofeatures/{track_id}.json            │
└────────┬────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│ STAGE 8: Transcoding (75-85%)                               │
│ • Opus encoding at 128k, 192k, 256k bitrates                │
│ • Source: mastered WAV (or enhanced/original if no master)  │
│ • Output: /catalog/opus_*/[webm/]{track_id}.*               │
└────────┬────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│ STAGE 9: Artwork Enrichment (85-90%)                        │
│ • ArtworkEnrichmentService (same as Suno tracks)            │
│ • Generate depth map using Depth Anything V2                │
│ • Create side-by-side JPEG (color + depth)                  │
│ • Enables parallax effects in NowPlaying UI                 │
│ • Output: /catalog/artwork_enriched/{track_id}.jpeg         │
└────────┬────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│ STAGE 10: Catalog Registration (90-100%)                    │
│ • Save metadata JSON                                        │
│ • Add to catalog database (same table as AI tracks)         │
│ • Add to vector index (for similarity search)               │
└─────────────────────────────────────────────────────────────┘
```

### Quality-Based Processing Decisions

The `SourceQualityAnalysisService` analyzes input files and recommends processing:

| Quality Tier | Criteria | Processing Applied |
|--------------|----------|-------------------|
| **STUDIO** | 24-bit, 48kHz+, lossless, >85% bandwidth | Mastering only (skip all enhancement) |
| **HIGH** | 16-bit, 44.1kHz+, lossless or ≥256kbps | Light mastering, optional enhancement |
| **MEDIUM** | ≥192kbps, 70-85% bandwidth | Apollo + mastering |
| **LOW** | <192kbps, <70% bandwidth, artifacts | Full pipeline (Apollo + mastering) |

This ensures high-quality sources aren't over-processed while low-quality sources get restoration.

---

## Future Enhancements (TODO)

### Multi-Track Upload

- [ ] Batch upload UI (select multiple files)
- [ ] Progress tracking per file
- [ ] Parallel Gemini analysis (rate-limited)
- [ ] Batch metadata editor after upload
- [ ] Album grouping during upload

### Metadata Editor

- [ ] Full metadata editing screen (not just inline)
- [ ] Correction workflow for AI-extracted data
- [ ] Genre/mood picker with existing catalog values
- [ ] Similar artists autocomplete from catalog
- [ ] Lyrics editor with timestamp alignment
- [ ] Re-run Gemini analysis button

### Additional Fields for Human Content

Human uploads may need fields that AI tracks don't have:

| Field | Description |
|-------|-------------|
| `album` | Album name |
| `album_artist` | Album artist (for compilations) |
| `track_number` | Position in album |
| `disc_number` | For multi-disc albums |
| `release_date` | Original release date |
| `record_label` | Label name |
| `isrc` | International Standard Recording Code |
| `upc` | Universal Product Code (album) |
| `copyright` | Copyright notice |
| `composer` | Songwriter/composer credits |
| `producer` | Production credits |
| `featuring` | Featured artists |
| `remix_of` | Original track if remix |
| `bpm` | Beats per minute (extracted) |
| `key` | Musical key (extracted) |
| `explicit` | Explicit content flag |

**Note**: These should be added to the metadata JSON schema, NOT as separate database columns. Keep the schema flexible.

### Artwork Upload

Like Bandcamp/SoundCloud, users should be able to:

- [x] **Extract embedded artwork** from uploaded files (MP3 ID3, FLAC, M4A, OGG) ✅ IMPLEMENTED
- [x] **Enrich artwork with depth maps** for parallax effects ✅ IMPLEMENTED
- [x] **Upload custom cover art after upload** ✅ IMPLEMENTED (TrackArtworkService + POST /artwork endpoint)
- [x] **AI-generate artwork** if none provided ✅ IMPLEMENTED (SDXL Lightning - POST /artwork/generate endpoint)
- [ ] Upload cover art during initial upload (drag-drop in UploadMusicModal)
- [ ] Album art (shared across tracks in album)
- [ ] Artist profile picture (separate from track art)

### Album Support

- [ ] Create albums from uploaded tracks
- [ ] Album metadata (title, release date, description)
- [ ] Album artwork
- [ ] Track ordering within album
- [ ] Album-level playback (play full album)

### Rights & Licensing

- [ ] Copyright declaration during upload
- [ ] License selection (All Rights Reserved, CC-BY, etc.)
- [ ] Proof of ownership for disputes
- [ ] DMCA takedown workflow

### Discovery Integration

- [ ] Human uploads in radio seeding (by genre, mood, etc.)
- [ ] "Uploaded by users" filter in catalog
- [ ] Featured user uploads section
- [ ] User profile with their uploaded tracks

### Analytics

- [ ] Play counts for uploaded tracks
- [ ] Listener demographics
- [ ] Export analytics (like Spotify for Artists)

---

## Testing

### Backend Test Script

```bash
cd server
E:/AI_RADIO/.venv/Scripts/python.exe utils/test_human_upload.py
```

### Manual Testing

1. Start backend: `E:/AI_RADIO/.venv/Scripts/python.exe start.py`
2. Start frontend: `cd client && npm run dev`
3. Log in
4. Go to User panel → My Uploads → Upload Your Music
5. Or use the green upload button in the catalog search bar

---

## Configuration

### Gemini API

Set in environment or config:
```
GOOGLE_API_KEY=your_gemini_api_key
```

### File Paths

```python
CATALOG_DIR = "server/catalog"
AUDIO_DIR = f"{CATALOG_DIR}/audio"
ARTWORK_DIR = f"{CATALOG_DIR}/artwork"
METADATA_DIR = f"{CATALOG_DIR}/metadata"
ORIGINALS_DIR = f"{CATALOG_DIR}/originals"  # Human upload originals
```

---

## Key Files Reference

```
server/
├── services/
│   ├── human_music_upload_service.py         # Upload pipeline orchestrator (10 stages)
│   ├── human_metadata_extraction_service.py  # Gemini Pro audio analysis
│   ├── source_quality_analysis_service.py    # Quality detection & recommendations
│   ├── embedded_artwork_service.py           # Extract artwork from audio files
│   ├── artwork_generation_service.py         # SDXL Lightning AI artwork generation
│   ├── track_artwork_service.py              # Manual artwork upload for tracks
│   ├── audio_master_service.py               # Loudness normalization (-14.0 LUFS)
│   ├── audio_features_service.py             # Tempo, beats, sections extraction
│   ├── suno_artwork_enrichment_service.py    # Depth map generation (shared with AI)
│   └── catalog_database_service.py           # DB ops (shared with AI)
├── utils/
│   ├── test_gemini_audio.py                  # Gemini API testing
│   └── test_human_upload.py                  # Upload service testing
└── app.py                                    # API routes + service initialization

client/src/
├── components/
│   ├── modals/
│   │   └── UploadMusicModal.jsx              # Upload modal (artwork upload + AI generate)
│   ├── MediaSearch.jsx                       # Upload button in search
│   └── User.jsx                              # My Uploads section
└── lib/
    └── api.js                                # API client
```

---

## Known Issues & Bugs

### Lyrics Not Displaying (TODO)

**Status**: Bug - needs investigation

**Symptom**: The upload preview shows "Lyrics detected" but the actual `transcribed_lyrics` field is not being displayed, even though:
1. The Gemini prompt requests lyrics transcription
2. The extraction service has `transcribed_lyrics` in the schema
3. The API endpoint now returns `transcribed_lyrics` in the response
4. The frontend modal has UI to display lyrics

**Possible causes to investigate**:
1. Gemini might not be returning lyrics in the response (check raw API response)
2. The `transcribed_lyrics` field might be named differently in Gemini output
3. Lyrics might be in a nested location we're not extracting
4. The `has_lyrics` flag might be set based on `lyrical_interpretation` presence, not actual lyrics

**Files to check**:
- `server/services/human_metadata_extraction_service.py` - Check Gemini response parsing
- `server/app.py:2288-2306` - Check API response construction
- Test with console.log in `UploadMusicModal.jsx` to see what metadata actually arrives

**Workaround**: Lyrics are still stored in the catalog metadata JSON and used for vector search, just not displayed in the upload preview UI.

---

## Principles to Maintain

1. **DRY**: Human tracks use the same systems as AI tracks. Don't create parallel pipelines.

2. **Same Quality**: Human uploads get the same metadata richness as AI tracks (via Gemini).

3. **Unified Search**: Human and AI tracks are searchable/discoverable together.

4. **Consistent Playback**: No special handling needed in PlaybackContext, AudioEngine, etc.

5. **Schema Flexibility**: Use JSON metadata fields for new attributes, not new DB columns.
