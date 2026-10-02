# Welcome to PLAiR.fm

## AI-Powered Music Discovery

PLAiR (Personalized Localized Adaptive Interactive Radio) is your own radio station that knows your city: AI DJs that pick the music, talk with you in real time and know what's on around you. The catalog mixes AI-generated tracks with **music uploaded by real artists**.

---

## Quick Start

- **Listen** — Browse the catalog, ask the DJ for recommendations, or let it surprise you
- **Talk** — Use voice commands or chat to request music by mood, genre, style, or vibe
- **Discover** — Search across 10 dimensions: genre, mood, style, theme, vocals, and more
- **Create** — Record shoutouts, replies and track reviews that get cleaned up and played on air, or upload your own music
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
- **Artist uploads** — Human musicians upload their own work and it is tagged, mastered and discoverable like everything else

The AI-generated part of the catalog started as training data—we needed diverse tracks to teach our classification system what "aggressive industrial with dystopian themes" actually sounds like. Uploads are now open to signed-in artists, and every listener can choose to hear human music, AI music or both.

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

**Radio Mode**
Switch it on and the hosts run scheduled talk breaks like a real station: news on the hour, a city update at half past, features in between, plus station idents and time checks.

**Keeps Playing Offline**
When you're signed in, tracks you like are downloaded in the background. If the connection drops, the music carries on from your downloads and hands back to the station when it returns.

### For Artists

Sign in and upload from the User panel:

- Upload your music and get **automatic semantic tagging** across 10 dimensions
- Your tracks become **instantly discoverable** through natural language search
- An **AI DJ introduces your work** with context-aware commentary
- **Real-time analytics** show exactly who's listening and engaging

---

## Features

### Semantic Music Search
Unlike playlist algorithms that push what's already popular, PLAiR searches by meaning. Query by artist similarity, mood, production style, lyrical themes, vocal delivery—or combine them all. Sub-200ms search across 1M+ embeddings.

### Studio-Quality Voice Engine
Two DJ hosts and a station voice with natural studio dynamics—overlapping speech, background ambiance, real conversation flow. The voices run on our own hardware, and lines that have been said before are reused from a semantic cache. All AI-generated in real-time.

### 3D Parallax Artwork
AI-generated depth maps from album art create actual parallax scrolling effects. A/B layer crossfading with proactive preloading ensures instant transitions.

### Shoutouts, Replies & Reviews
Record or type a message, reply to another listener, or review a track. Recordings get AI-enhanced (noise reduction), transcribed, and played on air; the best line of a review can play over the song it's about. Everything is indexed and semantically searchable.

### Audio-Reactive Visuals
WebGL shaders respond to music in real-time—FFT frequency analysis, tempo sync, glitch effects. Maintains 60fps rendering.

### AI Music Playground
Experiment with AI-assisted music generation via Suno API. A creative sandbox that also demonstrates our classification system works on any audio source.

---

## Current Beta Status

This is a **live beta** in active development. Everything works, but some features are limited to manage costs.

### What's Fully Functional
- Real-time AI DJ and voice commands
- Music playback with dual-buffer crossfading
- Multi-device WebSocket synchronization
- Semantic search and recommendations
- Shoutouts, replies and reviews
- Artist uploads
- Radio Mode talk breaks
- Offline playback from downloads
- Audio-reactive visualizations

### Current Limitations
- **DJ Voice** — Runs on a single local GPU, so replies can queue at busy times
- **Vector Search** — Accuracy varies with query complexity
- **iPhone** — Supported, but still being tested on real devices

### Known Issues
- Edge cases and incomplete polish (active beta)
- Some features rate-limited to manage costs

---

## Roadmap

### v1.0 Production
- Full device testing on iPhone and Android
- Enhanced vector search accuracy
- More of what aired, searchable by the hosts

### Beyond v1.0
- Additional DJ personalities
- Social features (profiles, shared playlists)
- Native mobile apps

---

## For Technical Reviewers

If you're here from a job application or portfolio review:

**Architecture**
- React + Vite frontend, FastAPI backend, PostgreSQL (SQLAlchemy)
- WebSocket-based real-time state sync across devices
- Publisher/Subscriber SSOT pattern for state management

**Audio Engineering**
- Custom dual-buffer A/B crossfading engine
- FFT analysis with early-exit optimization (60fps)
- Multi-layer caching (memory → IndexedDB → Cache API)

**AI/ML Integration**
- Annoy-based vector similarity search (10 embedding dimensions per track)
- Gemini for conversational AI with tool calling, a self-hosted TTS engine, Whisper for STT
- Semantic cache of spoken lines, so repeated phrases cost nothing to voice

**Audio Processing Pipeline**
- Demucs source separation
- ClearVoice speech enhancement
- Dynamic mastering and multi-bitrate transcoding

---

## Feedback

This is production-ready software, not a mockup. Try voice commands, browse the catalog, test multi-device sync.

Questions? Dive into the codebase or reach out.

---

**Version:** RC 1.0-beta | **Last Updated:** October 2026
