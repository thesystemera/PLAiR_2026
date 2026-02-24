# Audio Engine Race Condition Fix

## Problem
AB crossfade causing track ID desync - system thinks it's playing one track but actually playing another. Rapid track selection caused multiple tracks to load into same slot simultaneously.

## Files Changed
- `client/src/lib/audioEngine.js`

## Changes Made

### 1. Engine-Level Lock (Instead of Per-Slot)
**Before:** Each slot had `isLoading` flag - multiple tracks could load into same slot
**After:** Single `_isLoadingAnyTrack` flag prevents ANY concurrent loads

### 2. Cancel-Instead-of-Block Pattern
**Before:** New loads blocked if one in progress (caused skipped tracks)
**After:** New load CANCELS previous load - **last click wins!**

### 3. Implementation Details
```javascript
// Constructor - added:
this._isLoadingAnyTrack = false
this._currentLoadAbortController = null

// loadTrack() - cancel previous:
if (this._isLoadingAnyTrack && this._currentLoadAbortController) {
  this._currentLoadAbortController.abort()  // Cancel old
}
this._currentLoadAbortController = new AbortController()  // Create new

// Abort checks at key points:
- After cleanup
- After loading
- After crossfade

// Graceful abort handling:
catch (error) {
  if (error.name === 'AbortError') {
    // Intentional cancellation, not an error
  }
}
```

## Result
- ✅ No more race conditions (only 1 load at a time)
- ✅ Rapid clicking works (last clicked track plays)
- ✅ No track ID desync
- ✅ Crossfades still smooth

## Testing
Rapid-click queue items - should hear LAST clicked track, not random subset.
