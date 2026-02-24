# Known Issues & Architectural Gaps

Critical bugs and architectural issues that need to be addressed.

---

## 🔴 CRITICAL: Session State Resyncing on Reconnect

**Status**: Not Implemented
**Priority**: HIGH
**Affects**: All users, especially mobile with unstable connections

### The Problem

When **server restarts** OR **client reconnects** (network interruption), session state becomes desynchronized:

**Scenario 1: Server Restart (Client Still Connected)**
1. Client has state in localStorage: current track, queue position, radio mode, playback progress
2. Server restarts → loses ALL session state
3. Client reconnects via WebSocket
4. Server creates NEW `PlaybackState` with defaults:
   - `radio_mode = 'top_hits_week'`
   - `queue = []`
   - `history = []`
   - `current_track_id = None`
5. **Mismatch**: Client thinks it's playing track 5, server thinks queue is empty
6. **Result**: Queue filling fails, playback breaks, user confused

**Scenario 2: Client Network Interruption (Mobile)**
1. Client loses internet briefly (tunnel, airplane mode, dropped signal)
2. Client WebSocket disconnects
3. Server session state still exists
4. Client reconnects with stale localStorage state
5. **Mismatch**: Client and server have different queue/position
6. **Result**: UI shows wrong track, controls don't work correctly

**Scenario 3: Server Down But Internet Up**
1. Server is down for maintenance/crash
2. Client still has internet connection
3. **No visual indicator** that server is down
4. User thinks app is broken, tries controls
5. **Result**: Silent failures, confusing UX

### Current Workaround

Refresh the page (F5) - forces client to restart and sync with server state. Not user-friendly.

### What's Needed

#### 1. Client → Server State Sync on Reconnect

When client reconnects to server, send current state:

```javascript
// WebSocket reconnect handler
socket.on('connect', () => {
  const clientState = {
    currentTrack: getCurrentTrack(),
    queuePosition: getCurrentIndex(),
    radioMode: getRadioMode(),
    progressMs: getCurrentProgress(),
    queue: getQueue(),  // Or just track IDs
  };

  socket.emit('client_state_sync', clientState);
});
```

#### 2. Server State Recovery Logic

Server should:
- Check if session still exists in memory (Scenario 2)
  - If YES: Send server state to client, client updates
  - If NO: Accept client state, recreate session (Scenario 1)
- Validate client state (tracks still exist in catalog, etc.)
- Merge intelligently (preserve user's listening position)

#### 3. Visual Connection Status Indicators

**Client needs to detect and show:**

| Condition | Visual Indicator | User Action |
|-----------|------------------|-------------|
| WebSocket disconnected | 🔴 "Reconnecting..." banner | Wait for auto-reconnect |
| WebSocket connected, server down | 🟡 "Server unavailable" banner | Wait or refresh |
| Internet offline | 🔴 "No internet connection" | Check network |
| All connected | 🟢 Normal UI | Continue listening |

**Implementation:**
- Use `navigator.onLine` API for internet detection
- Use WebSocket `connect`/`disconnect` events for server detection
- Ping/pong heartbeat to detect server health (already have this?)
- Show banner at top of UI with reconnection countdown

#### 4. Graceful Degradation

When offline/disconnected:
- **Disable controls** that require server (play, next, search)
- **Keep controls** that work locally (pause, volume, seek on current track)
- **Show cached queue** (read-only) so user knows what was playing
- **Auto-resume** on reconnect (if still on same track)

### Why This Is Critical

1. **Mobile users**: Constantly go through tunnels, switch networks, drop signal
2. **Server maintenance**: Every deployment causes reconnects
3. **Development**: Server restarts frequently during dev
4. **User trust**: Silent failures make app feel broken/buggy
5. **Data loss**: Lose listening history, queue position, preferences

### Implementation Plan (TODO)

- [ ] Add `NetworkContext` to detect internet + server status
- [ ] Add connection status banner component
- [ ] Implement `client_state_sync` WebSocket message
- [ ] Add server-side session recovery logic in `PlaybackState`
- [ ] Add visual indicators for all connection states
- [ ] Test with forced disconnects (airplane mode, server restart)
- [ ] Add auto-resume on reconnect
- [ ] Document new WebSocket protocol in `ARCHITECTURE_SSOT_PATTERN.md`

### Related Code

**Frontend:**
- `client/src/contexts/WebSocketContext.jsx` - Connection management
- `client/src/contexts/PlaybackContext.jsx` - Client playback state
- `client/src/lib/session.js` - localStorage persistence
- `client/src/contexts/NetworkContext.jsx` - **NEEDS TO BE CREATED**

**Backend:**
- `server/services/playback_state.py` - Server session state
- `server/app.py` - WebSocket connection handler
- `server/services/websocket_service.py` - WebSocket broadcast

### Notes

This explains many "random" bugs:
- "Queue disappeared after server restart"
- "Controls stopped working on mobile"
- "UI shows wrong track"
- "Can't play anything after reconnect"

All symptoms of state desync between client/server.

---

## Template for New Issues

**Copy/paste this template when adding new issues:**

```markdown
## 🔴/🟡/🟢 Issue Title

**Status**: Not Implemented / In Progress / Needs Design
**Priority**: HIGH / MEDIUM / LOW
**Affects**: Who/what is impacted

### The Problem
Describe the issue...

### Current Workaround
How users can work around it now (if any)

### What's Needed
What needs to be built/fixed

### Why This Is Critical
Why it matters

### Implementation Plan (TODO)
- [ ] Task 1
- [ ] Task 2

### Related Code
Where to look

### Notes
Additional context
```

---

## Issue Priority Levels

- 🔴 **CRITICAL**: Blocks core functionality, affects all users
- 🟡 **HIGH**: Major feature broken, affects many users
- 🟢 **MEDIUM**: Minor feature broken, affects some users
- ⚪ **LOW**: Nice-to-have, cosmetic, edge case
