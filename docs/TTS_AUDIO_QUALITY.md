# TTS Audio Quality Documentation

## Current Setup (As of 2026-09-26)

ElevenLabs has been replaced by a local **Orpheus-3B** TTS engine. See `tts_server/README.md` for engine setup.

### TTS Audio Pipeline
```
Orpheus-3B TTS engine (tts_server/, 127.0.0.1:8090)
    ↓  raw PCM s16le, 24kHz mono (streamed over HTTP)
TTSGenerationService.generate_local_tts → PCM→MP3 @ 192kbps
    ↓
Semantic clip cache (MP3 + embedding, reused for similar lines)
    ↓
Audio Processing (pedalboard: gain, high/low shelf EQ, reverb, panning)
    ↓
Live Encoding → WebM/Opus @ 128kbps, 48kHz stereo
    ↓
WebSocket Streaming to Frontend
```

### TTS Engine
- **Process:** Separate Flask server in `tts_server/` at the repo root, with its own venv. Binds to `127.0.0.1:8090` (`TTS_SERVER_URL`).
- **Runtime:** Orpheus-3B finetune (GGUF, default Q4_K_M) on llama.cpp CUDA, SNAC 24kHz decoder on ONNX Runtime.
- **Launch:** The backend starts it via `server/services_radio/tts_engine_bootstrap.py` (set `TTS_SERVER_EXTERNAL=true` to manage it yourself).
- **GPU:** Pinned to the Quadro P6000 via `CUDA_DEVICE_ORDER=PCI_BUS_ID` + `CUDA_VISIBLE_DEVICES=0` in `.env`.
- **Voices:** Hosts are named after their Orpheus voices and hard-wired in `settings.VOICE_PREFERENCES`: Leo = `leo`, Jess = `jess`, station = `zac`, each with its own sampling temperature. No env overrides, no fallback voices.
- **Emotion tags:** Inline `<laugh>`, `<chuckle>`, `<sigh>`, `<gasp>`, `<groan>`, `<yawn>`, `<cough>`, `<sniffle>`. "Meta" segments (non-verbal reactions) are generated with these tags.
- **Sound effects:** `audio_effect_audio` segments are cache-only (no generation).
- **Cache:** Rebuilds from Orpheus output. It was purged on 2026-09-28 after the end-of-speech/length-cap engine fix and the host rename.

**Key Implementation:**
- File: `server/services_radio/tts_broadcast_service.py`
- Lines 163-169 and 243-249: FFmpeg parameters for live encoding
- Hardcoded bitrate: `"-b:a", "128k"`
- Sample rate: 48kHz stereo

### Audio Processing (`tts_processing_service.py`)
`AudioProcessingService.process_audio()` splits each clip into 200ms segments (50% crossfade) and runs a per-segment pedalboard chain driven by the segment's mix value (Perlin-noise variation around `audio_process_mix`, cosine-ramped between adjacent segments):
- **Gain:** interpolated between `AUDIO_EFFECT_CONFIG['global']['gain']` min/max
- **High shelf (5kHz):** cut scales up with mix
- **Low shelf (100Hz):** boost scales down with mix
- **Reverb:** wet/dry levels scale with mix
- **Panning:** per-speaker base position (jess -0.1, leo +0.1) with slow noise drift
- 100ms fade in/out; mono input is upmixed to stereo; segments normalized if they exceed full scale

### Music Audio Pipeline (For Comparison)
```
Pre-transcoded WebM/Opus files
    ↓
Adaptive bitrate (128k, 192k, 256k based on user preference)
    ↓
Direct file streaming
```

## Current Status: **Appropriate ✅**

Orpheus outputs 24kHz mono speech, stored as 192kbps MP3. Streaming TTS at 128kbps Opus (48kHz stereo) is transparent for speech at this source quality, so there is little benefit to streaming at higher bitrates.

## Future Enhancement Plan

### Phase 1: Higher-Quality Source Storage
- Store Orpheus output losslessly (WAV/FLAC) instead of MP3 in the clip cache
- Store processed TTS at higher quality

### Phase 2: Implement Adaptive TTS Bitrate
Once source files are stored at higher quality, implement adaptive bitrate for TTS to match music quality:

**Required Changes:**
1. **Pass user bitrate preference to TTS broadcast:**
   - Get user's `audio_quality` setting (128k, 192k, 256k, auto)
   - Pass to `broadcast_audio_stream()` function

2. **Update `tts_broadcast_service.py`:**
   ```python
   # Lines 168/248: Replace hardcoded "128k" with dynamic bitrate
   "-b:a", f"{user_bitrate}",  # Instead of "-b:a", "128k"
   ```

3. **Update `tts_stream_planner.py`:**
   - Include user bitrate in stream planning
   - Pass bitrate to broadcast service

4. **Consistency:**
   - User selects 256k music → TTS streams at 256k
   - User selects 128k music → TTS streams at 128k
   - User selects "auto" → TTS uses detected bitrate (matches music)

### Benefits After Implementation
- Consistent audio quality across music and TTS
- Better TTS quality for premium users on high bandwidth
- Bandwidth optimization for users on lower quality settings
- Professional audio experience throughout

## Technical Notes

### Format Details
- **Container:** WebM (both music and TTS)
- **Codec:** Opus (efficient, web-optimized)
- **Sample Rate:** 48kHz stereo (engine output is 24kHz mono)
- **Chunk Size:** 8192ms with dynamic boundary detection
- **Peak Limiting:** -1.0 dBFS to prevent clipping

### Live Encoding Rationale
TTS requires live encoding (unlike pre-transcoded music files) because:
1. Real-time audio effects processing (reverb, EQ varies per segment)
2. Dynamic mixing of main audio + background audio layers
3. Speaker intensity metadata synchronized with audio chunks
4. Cannot pre-generate due to infinite TTS variations

## Related Files
- `tts_server/` - Orpheus TTS engine (separate process, see `tts_server/README.md`)
- `server/services_radio/tts_engine_bootstrap.py` - Launches the TTS engine
- `server/services_radio/tts_broadcast_service.py` - Live encoding (lines 168, 248)
- `server/services_radio/tts_generation_service.py` - Orpheus client (`generate_local_tts`), PCM→MP3, clip cache
- `server/services_radio/tts_processing_service.py` - Audio effects processing (pedalboard)
- `client/src/hooks/useDJAudioStream.js` - Frontend TTS playback
- `server/services/media_streaming_service.py` - Music bitrate resolution (`resolve_bitrate`, line 19)
