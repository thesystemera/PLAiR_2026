# Unified WebSocket Progress Protocol

## Problem

Two pipelines (Suno generation, Human upload) use completely separate WebSocket messaging:
- **Suno**: 7 different message types (`generation_started`, `generation_processing`, `generation_retrying`, `generation_stage_update`, `generation_batch_completed`, `generation_batch_failed`, `generation_job_completed`)
- **Upload**: 1 message type (`upload_progress`) with no `job_id`
- Frontend has **8 separate `useWebSocketSubscribe()` calls** across 2 files (6 in GenerationQueueContext + 2 in App.jsx that aren't no-ops)
- Dead fields: `gemini_stage`, `suno_stage`, `upscaling_stage` stored but never rendered

## Solution

One message type: `task_progress`. Both pipelines emit it. Frontend subscribes once.

### Unified Protocol

```python
{
    "type": "task_progress",
    "data": {
        "job_id": "uuid",
        "job_type": "generation" | "upload",
        "status": "pending" | "processing" | "completed" | "failed",
        "current_stage": "Track 1/2: Mastering",
        "progress_percent": 67,
        "title": "My Song",
        "total_tracks": 2,
        "completed_tracks": 1,
        "tracks": ["track-id-1"],
        "error": null,
        # Generation-specific (ignored by upload consumers):
        "batch_index": 0,           # which batch this update is for
        "retry_attempt": null       # non-null on retries: { attempt: 2, max: 3 }
    }
}
```

Upload example:
```python
{
    "type": "task_progress",
    "data": {
        "job_id": "upload-abc123",
        "job_type": "upload",
        "status": "processing",
        "current_stage": "Transcoding (128k)",
        "progress_percent": 78,
        "title": "my_song.mp3",
        "total_tracks": 1,
        "completed_tracks": 0,
        "tracks": [],
        "error": null
    }
}
```

---

## Files to Modify (5 files)

### 1. `server/services/suno_generation_queue_service.py` — Replace all 7 types with `task_progress`

#### A. `GenerationJob.get_status()` (lines 109-161) — Drop dead fields, add `job_type`

Remove: `gemini_stage`, `suno_stage`, `upscaling_stage`
Add: `"job_type": "generation"`

```python
return {
    "job_id": self.job_id,
    "job_type": "generation",
    "status": overall_status,
    "current_stage": current_stage,
    "progress_percent": progress_percent,
    "title": title,
    "total_tracks": self.batch_count * 2,
    "completed_tracks": len(self.completed_track_ids),
    "tracks": self.completed_track_ids,
    "error": None,
    # Kept for internal batch tracking (not dead — used by _state_changed serialization):
    "batch_count": self.batch_count,
    "pending": pending,
    "processing": processing,
    "completed": completed,
    "failed": failed,
    "retrying": retrying,
    "expected_tracks": self.batch_count * 2,
    "source_track_id": self.source_track_id,
}
```

Also remove the batch-level reads of `gemini_stage`, `suno_stage`, `upscaling_stage` (lines 128-138). We still read `current_stage`, `title`, `progress_percent` from current_batch.

#### B. `_notify()` dedup (lines 226-249) — Update type check

Change:
```python
if message.get("type") in ["generation_started", "generation_stage_update", "generation_processing"]:
```
To:
```python
if message.get("type") == "task_progress":
```

#### C. `_serialize_state()` (lines 89-101) — Drop dead fields

Remove `gemini_stage`, `suno_stage`, `upscaling_stage` from the comparison dict.

#### D. Replace ALL `_notify()` calls — 13 call sites become `task_progress`

Every `_notify()` call changes from its specific type to:
```python
await self._notify(job.session_id, {
    "type": "task_progress",
    "data": job.get_status()
})
```

Since `get_status()` already reads the latest `current_stage`, `progress_percent`, `status`, `tracks`, etc. from the current batch, we just need to make sure each call site updates the batch state BEFORE calling `_notify`.

**Specific call sites and what changes:**

| Line | Old Type | Extra Fields | Change |
|------|----------|-------------|--------|
| 326 | `generation_started` | None (data = get_status()) | `type: "task_progress"`, data = get_status() |
| 354 | `generation_retrying` | attempt, max_attempts | Set `batch["current_stage"]` = retry text, then `type: "task_progress"`, data = `{**job.get_status(), "retry_attempt": {"attempt": N, "max": M}}` |
| 365 | `generation_processing` | batch_index, nested status | `type: "task_progress"`, data = get_status() |
| 388 | `generation_stage_update` | gemini_stage, stage | Already sets batch current_stage. Just: `type: "task_progress"`, data = get_status() |
| 447 | `generation_stage_update` | gemini_stage COMPLETE | Already sets batch current_stage. Just: `type: "task_progress"`, data = get_status() |
| 470 | `generation_stage_update` | suno_stage QUEUED | Already sets batch current_stage. Just: `type: "task_progress"`, data = get_status() |
| 487 | `generation_stage_update` | suno_stage SUBMITTING | Already sets batch current_stage. Just: `type: "task_progress"`, data = get_status() |
| 513 | `generation_stage_update` | suno_stage PENDING | Already sets batch current_stage. Just: `type: "task_progress"`, data = get_status() |
| 526 | `generation_stage_update` | suno_stage dynamic | Suno status callback — set `batch["current_stage"]` from suno status, then: `type: "task_progress"`, data = get_status() |
| 639 | `generation_stage_update` | downloading | Already sets current_stage. Just: `type: "task_progress"`, data = get_status() |
| 664 | `generation_stage_update` | metadata enrichment | Already sets current_stage. Just: `type: "task_progress"`, data = get_status() |
| 720 | `generation_stage_update` | upscaling stages (via callback) | Already sets current_stage. Just: `type: "task_progress"`, data = `{**job.get_status(), "current_stage": batch["current_stage"], "progress_percent": batch["progress_percent"]}` |
| 773 | `generation_stage_update` | finalizing | Already sets current_stage. Just: `type: "task_progress"`, data = get_status() |
| 832 | `generation_batch_completed` | tracks | get_status() already has tracks. Just: `type: "task_progress"`, data = get_status() |
| 856 | `generation_batch_failed` | error | Set status to failed, then: `type: "task_progress"`, data = `{**job.get_status(), "error": "Suno credits exhausted"}` |
| 880 | `generation_batch_failed` | error | Set status to failed, then: `type: "task_progress"`, data = `{**job.get_status(), "error": error_msg}` |
| 895 | `generation_job_completed` | None (data = get_status()) | `type: "task_progress"`, data = get_status() |

**Special case — suno_status_callback (line 524)**: Currently sets `batch["suno_stage"]` which is dead. Should instead set `batch["current_stage"]` to a human-readable suno status. Map suno statuses to display text:
- "PENDING" → "Generating with Suno AI..."
- "COMPLETE" → "Suno generation complete"
- Other → `f"Suno: {status}"`

**Special case — upscaling_progress_callback (line 701)**: The callback already builds `batch["current_stage"]` and `batch["progress_percent"]`. The get_status() call will pick those up. Just change to `type: "task_progress"`.

#### E. Remove batch-level dead field writes

All `batch["gemini_stage"] = ...`, `batch["suno_stage"] = ...`, `batch["upscaling_stage"] = ...` assignments can be removed since they feed only dead `get_status()` fields. But to minimize blast radius, we can leave them — they're harmless writes to a dict that just won't be read.

Decision: **Leave them for now** — removing doesn't affect behavior and keeps the diff focused on the protocol change.

---

### 2. `server/app.py` — Upload route sends `task_progress` with `job_id`

#### A. Upload route progress_callback (lines 2380-2388)

Generate a `job_id` for the upload. Change message type:

```python
import uuid

upload_job_id = str(uuid.uuid4())

async def progress_callback(stage: str, percent: float):
    assert websocket_service is not None
    await websocket_service.broadcast_to_session(session_id, {
        "type": "task_progress",
        "data": {
            "job_id": upload_job_id,
            "job_type": "upload",
            "status": "completed" if percent >= 100 else "processing",
            "current_stage": stage,
            "progress_percent": int(percent),
            "title": file.filename or "Upload",
            "total_tracks": 1,
            "completed_tracks": 1 if percent >= 100 else 0,
            "tracks": [],
            "error": None
        }
    })
```

No changes needed in `human_music_upload_service.py` — it just calls `progress_callback(stage, percent)` and doesn't know about WebSocket details. The callback in app.py handles the protocol.

---

### 3. `client/src/contexts/GenerationQueueContext.jsx` — Single subscription

#### A. `normalizeJobData` (lines 10-25) — Drop dead fields

```jsx
const normalizeJobData = (data) => ({
  id: data.job_id || data.id,
  type: data.job_type || data.type || 'generation',
  query: data.title || data.query || 'Processing...',
  status: data.status || 'pending',
  total_tracks: data.total_tracks || 0,
  completed_tracks: data.completed_tracks || 0,
  current_stage: data.current_stage || 'Processing...',
  progress_percent: data.progress_percent !== undefined ? data.progress_percent : 0,
  error: data.error || null,
  tracks: data.tracks || [],
  created_at: Date.now()
})
```

Removed: `gemini_stage`, `suno_stage`, `upscaling_stage`

#### B. `updateJobFromStatus` (lines 41-70) — Drop dead fields, flatten

No more `data.status || data` nesting. The data IS the status.

```jsx
const updateJobFromStatus = useCallback((jobId, statusData) => {
  setJobs(prev => {
    const existingJob = prev.find(job => job.id === jobId)

    if (!existingJob) {
      const newJob = normalizeJobData({ ...statusData, job_id: jobId })
      setIsOpen(true)
      return [...prev, newJob]
    }

    return prev.map(job =>
      job.id === jobId
        ? {
            ...job,
            status: statusData.status || job.status,
            total_tracks: statusData.total_tracks || job.total_tracks,
            completed_tracks: statusData.completed_tracks || 0,
            current_stage: statusData.current_stage || job.current_stage,
            query: statusData.title || job.query,
            progress_percent: statusData.progress_percent !== undefined ? statusData.progress_percent : job.progress_percent,
            error: statusData.error || null,
            tracks: statusData.tracks || job.tracks
          }
        : job
    )
  })
}, [])
```

#### C. Replace 6 subscriptions (lines 72-101) with 1

Replace all the individual handlers + subscriptions with:

```jsx
const handleTaskProgress = useCallback((data) => {
  if (!data?.job_id) return
  // Handle both generation and upload job types
  // (upload jobs will also appear in the queue panel for visibility)
  updateJobFromStatus(data.job_id, data)
}, [updateJobFromStatus])

useWebSocketSubscribe('task_progress', handleTaskProgress)
```

Delete: `handleGenerationStarted`, `handleGenerationUpdate`, `handleGenerationCompleted`, `handleGenerationFailed`, and all 6 `useWebSocketSubscribe` calls.

---

### 4. `client/src/App.jsx` — Replace 5 generation subscriptions with 1

#### A. Replace handlers (lines 486-511) + subscriptions (lines 634-638)

The App.jsx handlers show **toast notifications** for lifecycle events. With the unified protocol, we handle this in one subscription:

```jsx
const handleTaskProgress = useCallback(async (data) => {
  if (!data?.job_id || data?.job_type === 'upload') return  // Upload toasts handled by modal

  // Batch completed — tracks added to queue
  if (data.status === 'completed' && data.tracks?.length > 0) {
    // Check if this is a per-batch completion (has batch_index) or final job completion
    success(`✓ Generated ${data.total_tracks || 0} tracks!`, 5000, 'top')
  }

  // Retry notification
  if (data.retry_attempt) {
    info(`Generation retry ${data.retry_attempt.attempt}/${data.retry_attempt.max}...`, 3000, 'top')
  }

  // Error notification
  if (data.status === 'failed' && data.error) {
    errorToast(`Generation failed: ${data.error}`, 5000, 'top')
  }
}, [success, info, errorToast])

useWebSocketSubscribe('task_progress', handleTaskProgress)
```

Delete: `handleGenerationBatchCompleted`, `handleGenerationRetrying`, `handleGenerationBatchFailed`, `handleGenerationJobCompleted`, and all 5 `useWebSocketSubscribe` calls (lines 634-638).

**Note**: The old `handleGenerationBatchCompleted` called `playback.addToQueue(trackIds)`. But looking at the backend (line 814), the suno service ALREADY adds tracks to the queue via `playback_service.add_to_queue()` and broadcasts playback state. So the frontend `addToQueue` call is **redundant** — the tracks arrive via playback state WebSocket. We can safely remove it. (The toast notification stays.)

Wait — need to verify this. The backend adds tracks and broadcasts, but the frontend handler also calls addToQueue. Let me flag this for verification. Actually, looking more carefully at line 811-823 of suno_generation_queue_service.py, the backend calls `self.playback_service.add_to_queue()` which adds to the backend queue and broadcasts playback_state to all devices. So the frontend `playback.addToQueue(trackIds)` in App.jsx line 492 IS redundant and could cause duplicates. But this is a pre-existing issue — let's not change it in this PR to minimize blast radius. Instead, just keep the toast-only behavior.

Actually, re-reading App.jsx line 489-493 more carefully: it extracts track IDs from `data.tracks` and calls `playback.addToQueue`. But `data.tracks` in `generation_batch_completed` are just string track IDs (line 837: `"tracks": batch["tracks"]` where batch["tracks"] is a list of track_id strings). And `data.tracks.map(t => t.id || t.track_id).filter(Boolean)` would return empty array because strings don't have `.id` or `.track_id`. So this code does nothing! It's a no-op. Safe to remove.

---

### 5. `client/src/components/modals/UploadMusicModal.jsx` — Subscribe to `task_progress`

#### A. Replace `upload_progress` subscription (lines 283-288)

```jsx
useWebSocketSubscribe('task_progress', useCallback((data) => {
  if (data?.job_type === 'upload' && data?.current_stage && data?.progress_percent !== undefined) {
    setProgress(data.progress_percent)
    setStageText(data.current_stage)
  }
}, []))
```

---

## Execution Order

1. **`suno_generation_queue_service.py`** — Biggest change: get_status(), _notify(), _serialize_state(), all 13+ _notify call sites
2. **`app.py`** — Upload route: add job_id, change message type
3. **`GenerationQueueContext.jsx`** — Single subscription, drop dead fields
4. **`App.jsx`** — Replace 5 subscriptions with 1
5. **`UploadMusicModal.jsx`** — Update subscription type + field names

## NOT changing
- `human_music_upload_service.py` — No changes needed. It calls `progress_callback(stage, percent)` and doesn't know about WebSocket message structure.
- `GenerationQueuePanel.jsx` — Already simplified to `job.current_stage || 'Processing...'`. No changes needed.

## Verification
- Start a Suno generation job → check GenerationQueuePanel shows progress through all stages
- Start a Suno generation job → check toast notifications appear for completion/failure
- Upload a human music file → check UploadMusicModal shows progress
- Upload a file → check it also appears in the GenerationQueuePanel (new behavior — uploads visible in queue)
- Check browser console for any unhandled WebSocket message warnings
