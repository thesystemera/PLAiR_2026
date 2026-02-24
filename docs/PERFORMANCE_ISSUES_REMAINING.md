# Remaining Performance Issues (Low Priority)

## 1. Catalog renderTrack Callback
**File:** `client/src/components/Catalog.jsx` (lines 561-573)

**Issue:** `renderTrack` useCallback recreates when `currentTrackId`, `queuedTrackSet`, etc. change. VirtualScroller's memo comparison checks `renderItem` by reference, so this can invalidate the scroller's optimization.

**Impact:** Low - VirtualScroller may re-render more than necessary when queue/selection changes.

**Fix:** Use a stable render function or wrap TrackCard export in React.memo with custom comparison.

---

## 2. MediaSession API Updates Too Frequently
**File:** `client/src/App.jsx` (lines 415-434)

**Issue:** useEffect runs on `engineState.is_playing` changes, updating MediaSession metadata every play/pause.

**Impact:** Minimal - just unnecessary work, user doesn't notice.

**Fix:** Only update when `currentTrack.id` actually changes, not on play/pause.

---

**Recommendation:** Skip both for now - very low impact compared to fixes already made.
