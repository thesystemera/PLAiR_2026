const TALK_BREAK_TIMING = {
  talkUpMaxMs: 12000,
  talkUpMinMs: 800,
  postLeadS: 2.5,
  postFadeMs: 2500,
  resumeFadeMs: 1200,
  startTimeoutMs: 20000,
  maxOnAirMs: 200000,
  maxPausedMs: 10 * 60 * 1000,
  pollMs: 250,
  consumedMemory: 24,
}

const noopLog = { info() {}, warn() {}, error() {} }

function defaultEnv() {
  return {
    now: () => Date.now(),
    setInterval: (fn, ms) => setInterval(fn, ms),
    clearInterval: (id) => clearInterval(id),
  }
}

export class TalkBreakController {
  constructor({ getEngine = null, bed = null, send = null, report = null, log = noopLog, env = null, timing = null } = {}) {
    this.getEngine = getEngine || (() => null)
    this.bed = bed
    this.send = send
    this.report = report
    this.log = log
    this.env = { ...defaultEnv(), ...(env || {}) }
    this.timing = { ...TALK_BREAK_TIMING, ...(timing || {}) }

    this.player = null
    this.active = false
    this.server = null
    this.serverTrackId = null
    this.serverPlaying = true
    this.phase = 'idle'
    this.info = null
    this.paused = false
    this.pausedAt = 0
    this.onAirSince = 0
    this.consumed = []
    this.reported = new Set()
    this.failedStreams = new Set()
    this._timer = null
    this._lastReport = null
  }

  configure({ getEngine, bed, send, report } = {}) {
    if (getEngine) this.getEngine = getEngine
    if (bed !== undefined) {
      if (this.bed && this.bed !== bed) this.bed.stop(0)
      this.bed = bed
    }
    if (send) this.send = send
    if (report) this.report = report
  }

  attachPlayer(player) {
    this.player = player
    player.onStagedEnd = (streamId, info) => this._onStagedEnd(streamId, info)
    player.onStreamEnd = (streamId, reason) => this._onStreamEnd(streamId, reason)
    this._evaluate()
  }

  detachPlayer(player) {
    if (this.player !== player) return
    this.player = null
    if (this.phase !== 'idle') this._abortLocal()
  }

  isOnAir() {
    return this.phase === 'on_air'
  }

  isLive() {
    return this.phase === 'on_air' || this.phase === 'posting'
  }

  isPaused() {
    return this.phase === 'on_air' && this.paused
  }

  display() {
    if (!this.isLive() || !this.info) return null
    return {
      id: this.info.id,
      kind: this.info.kind,
      label: this.info.label,
      title: this.info.title,
      paused: this.paused,
      durationS: this.info.durationS,
    }
  }

  progress() {
    if (!this.isLive() || !this.info) return null
    const live = this.player?.getProgress(this.info.streamId)
    if (live) return live
    return this.info.durationS ? { elapsed: 0, duration: this.info.durationS } : null
  }

  setActive(active) {
    const next = !!active
    if (next === this.active) return
    this.active = next
    if (!next) this._abortLocal()
    this._sync()
  }

  onServerState(talkBreak, { currentTrackId = null, isPlaying = true } = {}) {
    const previousPlaying = this.serverPlaying
    this.server = talkBreak || null
    this.serverTrackId = currentTrackId
    this.serverPlaying = isPlaying
    if (!this.active) {
      this._sync()
      return
    }

    if (this.isLive()) {
      const info = this.info
      if (!talkBreak || talkBreak.id !== info.id) {
        this._endFromServer(currentTrackId)
      } else if (this.phase === 'on_air' && previousPlaying !== isPlaying) {
        if (isPlaying) this.resume()
        else this.pause()
      }
      this._sync()
      return
    }

    if (this.phase === 'armed') {
      const info = this.info
      const stillValid = talkBreak && talkBreak.id === info.id && talkBreak.status === 'ready' &&
        talkBreak.stream_id === info.streamId && talkBreak.after_track_id === info.trackId
      if (!stillValid) this._disarm()
    }

    if (talkBreak?.status === 'on_air' && this.phase === 'idle' && !this.consumed.includes(talkBreak.id)) {
      this._consume(talkBreak.id)
      this.log.warn('[TalkBreak] Server has a break on air that this device is not playing - ending it')
      this._send('talk_break_end', { break_id: talkBreak.id, stream_id: talkBreak.stream_id, reason: 'lost' })
    }

    this._maybeReport()
    this._evaluate()
    this._sync()
  }

  onHoldReached(trackId) {
    if (this.phase !== 'armed' || !this.info || this.info.trackId !== trackId) return
    const info = this.info
    if (!this.player || !this.player.release(info.streamId)) {
      this.log.warn('[TalkBreak] Segment audio missing at the boundary - resuming music')
      this._resumeMusic(this.timing.resumeFadeMs)
      this._send('talk_break_end', { break_id: info.id, stream_id: info.streamId, reason: 'not_ready' })
      this._reset()
      return
    }
    this.phase = 'on_air'
    this.paused = false
    this.onAirSince = this.env.now()
    this._consume(info.id)
    this._send('talk_break_start', { break_id: info.id, stream_id: info.streamId })
    if (this.bed && info.bed) {
      if (!this.bed.start(info.bed)) this.log.info('[TalkBreak] Music bed not ready - going on air dry')
    }
    this.log.info(`[TalkBreak] 🔴 ON AIR: ${info.label}`)
    this._publish()
    this._sync()
  }

  pause() {
    if (this.phase !== 'on_air') return false
    if (this.paused) return true
    this.paused = true
    this.pausedAt = this.env.now()
    this.player?.setHoldReason('break', true)
    this.bed?.pause()
    this._publish()
    return true
  }

  resume() {
    if (this.phase !== 'on_air') return false
    if (!this.paused) return true
    this.paused = false
    this.onAirSince += this.env.now() - this.pausedAt
    this.player?.setHoldReason('break', false)
    this.bed?.resume()
    this._publish()
    return true
  }

  onUserTransport(kind = 'skip') {
    if (this.phase === 'armed') {
      this._disarm()
      this._sync()
      return
    }
    if (!this.isLive()) return
    const info = this.info
    this.phase = 'idle'
    this.player?.setHoldReason('break', false)
    this.player?.cancelStream(info.streamId, kind)
    this.getEngine()?.clearHold()
    this.bed?.stop(400)
    this._send('talk_break_end', { break_id: info.id, stream_id: info.streamId, reason: `user_${kind}` })
    this._reset()
  }

  destroy() {
    this._abortLocal()
    this._stopTimer()
    this.bed?.destroy()
  }

  _send(type, data) {
    if (this.active) this.send?.(type, data)
  }

  _consume(id) {
    if (this.consumed.includes(id)) return
    this.consumed.push(id)
    if (this.consumed.length > this.timing.consumedMemory) this.consumed.shift()
  }

  _publish() {
    const view = this.display()
    const key = view ? `${view.id}:${view.paused}` : null
    if (key === this._lastReport) return
    this._lastReport = key
    this.report?.(view)
  }

  _evaluate() {
    if (!this.active || this.phase !== 'idle' || !this.player) return
    const talkBreak = this.server
    if (!talkBreak || talkBreak.status !== 'ready' || this.consumed.includes(talkBreak.id)) return
    const engine = this.getEngine()
    if (!engine || !this.player.isStagedReady(talkBreak.stream_id)) return
    const trackId = talkBreak.after_track_id
    if (!trackId || engine.getCurrentTrackId() !== trackId) return
    const talkUpMs = engine.talkUpPointMs(this.timing.talkUpMaxMs, this.timing.talkUpMinMs)
    if (!engine.armHold(trackId, { talkUpMs })) return
    this.phase = 'armed'
    this.info = {
      id: talkBreak.id,
      streamId: talkBreak.stream_id,
      trackId,
      kind: talkBreak.kind,
      label: talkBreak.label,
      title: talkBreak.title,
      bed: talkBreak.bed || null,
      durationS: this.player.stagedDuration(talkBreak.stream_id),
    }
    if (this.bed && this.info.bed) this.bed.prepare(this.info.bed)
    this.log.info(`[TalkBreak] Armed ${talkBreak.label} after ${trackId.slice(0, 8)} (talk-up ${Math.round(talkUpMs)}ms)`)
  }

  _maybeReport() {
    const talkBreak = this.server
    if (!this.active || !talkBreak || talkBreak.status !== 'rendering' || !this.player) return
    const streamId = talkBreak.stream_id
    if (!streamId || this.reported.has(streamId)) return
    if (this.player.isStagedReady(streamId)) {
      this.reported.add(streamId)
      this._send('talk_break_ready', {
        break_id: talkBreak.id,
        stream_id: streamId,
        duration_s: this.player.stagedDuration(streamId),
      })
    } else if (this.failedStreams.has(streamId)) {
      this.reported.add(streamId)
      this._send('talk_break_failed', { break_id: talkBreak.id, stream_id: streamId, reason: 'incomplete' })
    }
    if (this.reported.size > 64) this.reported = new Set([...this.reported].slice(-32))
  }

  _onStagedEnd(streamId, { complete } = {}) {
    if (!complete) {
      this.failedStreams.add(streamId)
      if (this.failedStreams.size > 32) this.failedStreams = new Set([...this.failedStreams].slice(-16))
    }
    this._maybeReport()
    this._evaluate()
    this._sync()
  }

  _onStreamEnd(streamId, reason) {
    const info = this.info
    if (!info || info.streamId !== streamId) return
    if (this.phase === 'armed') {
      this._disarm()
      this._sync()
      return
    }
    if (!this.isLive()) return
    if (!this.active) {
      this._reset()
      return
    }
    const posted = this.phase === 'posting'
    this.player?.setHoldReason('break', false)
    if (!posted) this._resumeMusic(this.timing.resumeFadeMs)
    this.bed?.stop(posted ? this.timing.postFadeMs : 1500)
    const finished = reason === 'ended' || reason === 'ended-timeout'
    this._send('talk_break_end', { break_id: info.id, stream_id: streamId, reason: finished ? 'done' : reason })
    this.log.info(`[TalkBreak] ${info.label} off air (${reason})`)
    this._reset()
  }

  _endFromServer(currentTrackId) {
    const info = this.info
    const posted = this.phase === 'posting'
    this.phase = 'idle'
    this.player?.setHoldReason('break', false)
    this.player?.cancelStream(info.streamId, 'server')
    const engine = this.getEngine()
    if (!posted) {
      if (currentTrackId && currentTrackId !== info.trackId) engine?.clearHold()
      else this._resumeMusic(this.timing.resumeFadeMs)
    }
    this.bed?.stop(800)
    this._reset()
  }

  _resumeMusic(fadeMs) {
    const engine = this.getEngine()
    if (!engine) return
    if (!engine.resumeFromHold({ fadeMs })) engine.clearHold()
  }

  _disarm() {
    const engine = this.getEngine()
    if (engine && this.info && engine.isHolding(this.info.trackId) && !engine.isHoldReached(this.info.trackId)) {
      engine.clearHold()
    } else if (engine && this.info && engine.isHoldReached(this.info.trackId)) {
      this._resumeMusic(this.timing.resumeFadeMs)
    }
    this.phase = 'idle'
    this.info = null
  }

  _abortLocal() {
    if (this.phase === 'idle') return
    this.player?.setHoldReason('break', false)
    this.getEngine()?.clearHold()
    this.bed?.stop(0)
    this._reset()
  }

  _reset() {
    const bedId = this.info?.bed?.id
    this.phase = 'idle'
    this.info = null
    this.paused = false
    this.onAirSince = 0
    if (bedId && this.bed) this.bed.release(bedId)
    this._publish()
    this._sync()
  }

  _tick() {
    const now = this.env.now()
    if (this.phase === 'idle') {
      this._maybeReport()
      this._evaluate()
      return
    }
    if (this.phase === 'armed') {
      const engine = this.getEngine()
      if (!engine || !engine.isHolding(this.info.trackId)) {
        this.phase = 'idle'
        this.info = null
      }
      return
    }
    const info = this.info
    if (this.paused) {
      if (now - this.pausedAt > this.timing.maxPausedMs) this._abort('paused_too_long')
      return
    }
    if (now - this.onAirSince > this.timing.maxOnAirMs) {
      this._abort('max_duration')
      return
    }
    if (!this.player?.isCurrent(info.streamId)) {
      if (now - this.onAirSince > this.timing.startTimeoutMs) this._abort('start_timeout')
      return
    }
    if (this.phase === 'on_air') {
      const remaining = this.player.getRemaining(info.streamId)
      if (remaining !== null && remaining <= this.timing.postLeadS) {
        this.phase = 'posting'
        this._resumeMusic(this.timing.postFadeMs)
        this.bed?.stop(this.timing.postFadeMs)
        this.log.info(`[TalkBreak] Posting the next track under the last ${remaining.toFixed(1)}s`)
      }
    }
  }

  _abort(reason) {
    const info = this.info
    if (!info) return
    this.log.warn(`[TalkBreak] Ending ${info.label} early (${reason})`)
    const posted = this.phase === 'posting'
    this.phase = 'idle'
    this.player?.setHoldReason('break', false)
    this.player?.cancelStream(info.streamId, reason)
    if (!posted) this._resumeMusic(this.timing.resumeFadeMs)
    this.bed?.stop(1000)
    this._send('talk_break_end', { break_id: info.id, stream_id: info.streamId, reason })
    this._reset()
  }

  _wantsTimer() {
    if (!this.active) return false
    if (this.phase !== 'idle') return true
    const status = this.server?.status
    return status === 'ready' || status === 'rendering'
  }

  _sync() {
    if (this._wantsTimer()) {
      if (!this._timer) this._timer = this.env.setInterval(() => this._tick(), this.timing.pollMs)
    } else {
      this._stopTimer()
    }
  }

  _stopTimer() {
    if (!this._timer) return
    this.env.clearInterval(this._timer)
    this._timer = null
  }
}
