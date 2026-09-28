export const PENDING_ACK_TIMEOUT_MS = 4000

export const NO_PENDING = Object.freeze({ seq: 0, firstSeq: 0, since: 0 })

export function createSyncCursor() {
  return { epoch: null, version: -1 }
}

function hasServerVersion(data) {
  return !!data && typeof data.state_epoch === 'string' && typeof data.version === 'number'
}

export function isOutdatedSnapshot(data, cursor) {
  if (!hasServerVersion(data) || !cursor) return false
  return cursor.epoch === data.state_epoch && data.version <= cursor.version
}

export function advanceCursor(data, cursor) {
  if (!hasServerVersion(data)) return cursor
  return { epoch: data.state_epoch, version: data.version }
}

export function ackedSeq(data, deviceId) {
  const value = data?.acks?.[deviceId]
  return typeof value === 'number' ? value : 0
}

export function markCommandSent(pending, seq, now) {
  return {
    seq,
    firstSeq: pending?.seq ? pending.firstSeq : seq,
    since: now
  }
}

export function classifySnapshot(data, deviceId, pending, now, timeoutMs = PENDING_ACK_TIMEOUT_MS) {
  if (!pending?.seq) return 'apply'
  if (!hasServerVersion(data)) return 'apply'
  const acked = ackedSeq(data, deviceId)
  if (acked >= pending.seq) return 'apply'
  if (pending.since && now - pending.since > timeoutMs) return 'apply'
  if (acked >= pending.firstSeq) return 'display'
  return 'defer'
}

function sameValue(a, b) {
  if (a === b) return true
  if (a === null || b === null || typeof a !== 'object' || typeof b !== 'object') return false
  try {
    return JSON.stringify(a) === JSON.stringify(b)
  } catch {
    return false
  }
}

function sameShallow(a, b) {
  if (a === b) return true
  if (!a || !b) return false
  const keysA = Object.keys(a)
  const keysB = Object.keys(b)
  if (keysA.length !== keysB.length) return false
  for (let i = 0; i < keysA.length; i++) {
    const key = keysA[i]
    if (!Object.prototype.hasOwnProperty.call(b, key) || !sameValue(a[key], b[key])) return false
  }
  return true
}

export function reconcileValue(prev, next) {
  return sameValue(prev, next) ? prev : next
}

export function reconcileTrack(prev, next) {
  if (!next) return next ?? null
  if (prev && prev.id === next.id && sameShallow(prev, next)) return prev
  return next
}

export function reconcileQueue(prev, next) {
  if (!Array.isArray(next)) return Array.isArray(prev) ? prev : []
  if (!Array.isArray(prev) || prev.length === 0) return next
  const previousById = new Map()
  for (let i = 0; i < prev.length; i++) {
    if (prev[i]?.id) previousById.set(prev[i].id, prev[i])
  }
  let changed = prev.length !== next.length
  const out = new Array(next.length)
  for (let i = 0; i < next.length; i++) {
    const item = next[i]
    const old = item?.id ? previousById.get(item.id) : undefined
    const kept = old && sameShallow(old, item) ? old : item
    if (kept !== prev[i]) changed = true
    out[i] = kept
  }
  return changed ? out : prev
}

export function expectedProgressMs(desired, now = Date.now()) {
  const base = desired?.progressMs || 0
  if (!desired?.isPlaying || !desired.receivedAt) return base
  const elapsed = Math.max(0, now - desired.receivedAt)
  const duration = desired.track?.duration_ms || Infinity
  return Math.min(base + elapsed, duration)
}
