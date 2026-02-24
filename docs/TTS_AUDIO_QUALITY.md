# TTS Audio Quality Documentation

## Current Setup (As of 2026-01-10)

### TTS Audio Pipeline
```
ElevenLabs API (MP3, ~128kbps)
    ↓
Audio Processing (reverb, EQ, gain, panning)
    ↓
Live Encoding → WebM/Opus @ 128kbps
    ↓
WebSocket Streaming to Frontend
```

**Key Implementation:**
- File: `server/services_radio/tts_broadcast_service.py`
- Lines 163-173: FFmpeg parameters for live encoding
- Hardcoded bitrate: `-b:a", "128k"`
- Sample rate: 48kHz stereo

### Music Audio Pipeline (For Comparison)
```
Pre-transcoded WebM/Opus files
    ↓
Adaptive bitrate (128k, 192k, 256k based on user preference)
    ↓
Direct file streaming
```

## Current Status: **Appropriate ✅**

The TTS streaming bitrate (128kbps) matches the source MP3 quality (~128kbps from ElevenLabs). There's no benefit to streaming at higher bitrates when source material is 128kbps.

## Future Enhancement Plan

### Phase 1: Upscale Source MP3s
- Upscale existing TTS MP3 source files to higher quality (WAV or high-bitrate MP3)
- Consider requesting higher quality output from ElevenLabs if available
- Store processed TTS at higher quality

### Phase 2: Implement Adaptive TTS Bitrate
Once source files are upscaled, implement adaptive bitrate for TTS to match music quality:

**Required Changes:**
1. **Pass user bitrate preference to TTS broadcast:**
   - Get user's `audio_quality` setting (128k, 192k, 256k, auto)
   - Pass to `broadcast_audio_stream()` function

2. **Update `tts_broadcast_service.py`:**
   ```python
   # Line 168: Replace hardcoded "128k" with dynamic bitrate
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
- **Sample Rate:** 48kHz stereo
- **Chunk Size:** 8192ms with dynamic boundary detection
- **Peak Limiting:** -1.0 dBFS to prevent clipping

### Live Encoding Rationale
TTS requires live encoding (unlike pre-transcoded music files) because:
1. Real-time audio effects processing (reverb, EQ varies per segment)
2. Dynamic mixing of main audio + background audio layers
3. Speaker intensity metadata synchronized with audio chunks
4. Cannot pre-generate due to infinite TTS variations

## Related Files
- `server/services_radio/tts_broadcast_service.py` - Live encoding (line 168)
- `server/services_radio/tts_generation_service.py` - ElevenLabs API integration
- `server/services_radio/tts_processing_service.py` - Audio effects processing
- `client/src/hooks/useDJAudioStream.js` - Frontend TTS playback
- `server/services/media_streaming_service.py` - Music bitrate resolution (line 18)
