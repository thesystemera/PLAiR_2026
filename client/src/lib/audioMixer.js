const MUSIC_NOMINAL_LEVEL = 1.0

export class AudioMixer {
  constructor(audioEngine) {
    this.engine = audioEngine
    this.targetLevel = MUSIC_NOMINAL_LEVEL
  }

  get isDucking() {
    return this.targetLevel < MUSIC_NOMINAL_LEVEL
  }

  _rampMusic(level, durationMs) {
    const engine = this.engine
    if (!engine?.context || !engine?.musicGain) return
    this.targetLevel = level
    const gain = engine.musicGain.gain
    engine._rampGain(engine.musicGain, gain.value, level, Math.max(0, durationMs) / 1000)
  }

  duckMusic(targetLevel = 0.25, durationMs = 400) {
    this._rampMusic(Math.max(0, Math.min(MUSIC_NOMINAL_LEVEL, targetLevel)), durationMs)
  }

  restoreMusic(durationMs = 400) {
    this._rampMusic(MUSIC_NOMINAL_LEVEL, durationMs)
  }

  stopAll() {
    const engine = this.engine
    if (!engine?.context || !engine?.musicGain || engine.context.state === 'closed') return
    engine._rampGain(engine.musicGain, MUSIC_NOMINAL_LEVEL, MUSIC_NOMINAL_LEVEL, 0)
    this.targetLevel = MUSIC_NOMINAL_LEVEL
  }

  destroy() {
    this.stopAll()
  }
}
