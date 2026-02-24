export class AudioMixer {
  constructor(audioEngine) {
    this.engine = audioEngine
    this.isDucking = false
    this.originalMusicVolume = 1.0
  }

  duckMusic(targetLevel = 0.25, durationMs = 400) {
    if (!this.engine?.context || !this.engine?.musicGain) return

    const { context, musicGain } = this.engine
    const now = context.currentTime
    const durationSec = durationMs / 1000

    if (!this.isDucking) {
      this.originalMusicVolume = musicGain.gain.value
    }

    this.isDucking = true
    musicGain.gain.cancelScheduledValues(now)
    musicGain.gain.setValueAtTime(musicGain.gain.value, now)
    musicGain.gain.linearRampToValueAtTime(targetLevel, now + durationSec)
  }

  restoreMusic(durationMs = 400) {
    if (!this.engine?.context || !this.engine?.musicGain) return

    const { context, musicGain } = this.engine
    const now = context.currentTime
    const durationSec = durationMs / 1000

    musicGain.gain.cancelScheduledValues(now)
    musicGain.gain.setValueAtTime(musicGain.gain.value, now)
    musicGain.gain.linearRampToValueAtTime(this.originalMusicVolume, now + durationSec)

    setTimeout(() => {
      this.isDucking = false
    }, durationMs)
  }

  stopAll() {
    if (!this.engine?.context || !this.engine?.musicGain) return

    const { context, musicGain } = this.engine
    musicGain.gain.cancelScheduledValues(context.currentTime)
    this.isDucking = false
  }

  destroy() {
    this.stopAll()
  }
}