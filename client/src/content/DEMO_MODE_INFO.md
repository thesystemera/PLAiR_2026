# Welcome to PLAiR.fm

## AI-Powered Music Discovery

PLAiR is a music platform with an AI DJ that curates, discovers, and talks about music in real-time. Currently featuring an AI-generated catalog, with **artist uploads coming soon**.

---

## Quick Start

- **Listen** — Browse the catalog, ask the DJ for recommendations, or let it surprise you
- **Talk** — Use voice commands or chat to request music by mood, genre, style, or vibe
- **Discover** — Search across 10 dimensions: genre, mood, style, theme, vocals, and more
- **Create** — Record shoutouts that get AI-enhanced and played on air
- **Control** — Manage playback across multiple devices with real-time sync

---

## The Story Behind PLAiR

**November 2024** — The day before Thanksgiving, Spotify [shut down critical API access](https://www.theverge.com/2024/12/5/24311523/spotify-locked-down-apis-developers) with zero warning. Our original PLAiR app was instantly killed alongside hundreds of other developer projects.

We had two choices: give up, or rebuild without depending on any platform that could shut us down overnight.

**We rebuilt. And we built something better.**

Now we own the entire stack:
- **10-dimensional semantic search** — Vector embeddings classify music by genre, mood, style, theme, vocals, and more
- **Full audio processing pipeline** — Source separation, mastering, transcoding, all in-house
- **Real-time AI DJ** — Context-aware conversations, not pre-recorded playlists
- **Artist-ready infrastructure** — Built for human musicians to upload and share their work

The AI-generated catalog you hear now is training data—we needed diverse tracks to teach our classification system what "aggressive industrial with dystopian themes" actually sounds like. Once the platform is fully polished, we'll open it up for real artists to upload their music.

**This time, nobody can pull the plug on us.**

---

## What You Can Do Here

### For Listeners

**Conversational Music Discovery**
Ask for music naturally: *"Find something dark and atmospheric"* or *"Play upbeat indie with female vocals."* The AI understands context, not just keywords.

**AI DJ That Actually Knows Things**
The DJ pulls from 15+ real-time sources—your location's weather, current news, nearby concerts, your listening history, time of day. Every response is contextually aware.

**Multi-Device Control**
Start on your laptop, switch to your phone. One device plays, all others show the same queue and state in real-time. Universal remote functionality.

### For Artists (Future)

Artist uploads aren't available yet, but here's what we're building:

- Upload your music and get **automatic semantic tagging** across 10 dimensions
- Your tracks become **instantly discoverable** through natural language search
- An **AI DJ introduces your work** with context-aware commentary
- **Real-time analytics** show exactly who's listening and engaging

---

## Features

### Semantic Music Search
Unlike playlist algorithms that push what's already popular, PLAiR searches by meaning. Query by artist similarity, mood, production style, lyrical themes, vocal delivery—or combine them all. Sub-200ms search across 1M+ embeddings.

### Studio-Quality TTS Engine
Multiple DJ voices with natural studio dynamics—overlapping speech, background ambiance, real conversation flow. Semantic caching reuses similar phrases while maintaining voice consistency. All AI-generated in real-time.

### 3D Parallax Artwork
AI-generated depth maps from album art create actual parallax scrolling effects. A/B layer crossfading with proactive preloading ensures instant transitions.

### User Shoutouts
Record voice messages that get AI-enhanced (noise reduction), transcribed, and played on air. Shoutouts are indexed and semantically searchable.

### Audio-Reactive Visuals
WebGL shaders respond to music in real-time—FFT frequency analysis, tempo sync, glitch effects. Maintains 60fps rendering.

### AI Music Playground
Experiment with AI-assisted music generation via Suno API. A creative sandbox that also demonstrates our classification system works on any audio source.

---

## Current Beta Status

This is a **live beta** in active development. Everything works, but some features are limited to manage API costs.

### What's Fully Functional
- Real-time AI DJ and voice commands
- Music playback with dual-buffer crossfading
- Multi-device WebSocket synchronization
- Semantic search and recommendations
- Shoutout recording and playback
- Audio-reactive visualizations

### Current Limitations
- **DJ Voice Quality** — Using cost-optimized TTS during beta (reduced accuracy)
- **Shoutout Replies** — DJ doesn't yet respond dynamically to individual shoutouts
- **Vector Search** — Accuracy varies with query complexity
- **Mobile UI** — Functional but desktop-optimized

### Known Issues
- Edge cases and incomplete polish (active beta)
- Some features rate-limited to manage costs

---

## Roadmap

### v1.0 Production
- **Artist uploads** — Real musicians can submit their music
- Premium DJ voice quality
- Full ratings and opinions system
- Interactive shoutout conversations
- Enhanced vector search accuracy
- Mobile-first UI refinements

### Beyond v1.0
- Additional DJ personalities
- Social features (profiles, shared playlists)
- Native mobile apps
- Offline mode and PWA enhancements

---

## For Technical Reviewers

If you're here from a job application or portfolio review:

**Architecture**
- React + Vite frontend, FastAPI backend, SQLAlchemy ORM
- WebSocket-based real-time state sync across devices
- Publisher/Subscriber SSOT pattern for state management

**Audio Engineering**
- Custom dual-buffer A/B crossfading engine
- FFT analysis with early-exit optimization (60fps)
- Multi-layer caching (memory → IndexedDB → Cache API)

**AI/ML Integration**
- Annoy-based vector similarity search (10 embedding dimensions per track)
- Gemini for conversational AI, ElevenLabs for TTS, Whisper for STT
- Semantic TTS caching (~70% cost reduction)

**Audio Processing Pipeline**
- Demucs source separation
- ClearVoice speech enhancement
- Dynamic mastering and multi-bitrate transcoding

---

## Feedback

This is production-ready software, not a mockup. Try voice commands, browse the catalog, test multi-device sync.

Questions? Dive into the codebase or reach out.

---

**Version:** RC 1.0-beta | **Last Updated:** January 2026
