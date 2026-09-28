const ATTACK_SEC = 0.015
const RELEASE_HOLD_MS = 600
const HISS_LEVEL = 0.0008
const BACKGROUND_LEVEL = 0.1
const BACKGROUND_URL = '/audio/studio/background.mp3'

export class DJBroadcastChain {
  constructor(engine, audioElement) {
    this.engine = engine
    this.ctx = engine.context
    this.audioElement = audioElement

    this.source = null
    this.inputGain = null
    this.compressor = null
    this.limiter = null
    this.hissBuffer = null
    this.analogHiss = null
    this.hissBandpass = null
    this.hissGain = null
    this.backgroundNoise = null
    this.backgroundSource = null
    this.backgroundGain = null
    this.mixer = null
    this.analyser = null
    this.destination = null

    this.isSetup = false
    this.isActive = false
    this._releaseTimer = null
  }

  setup() {
    if (this.isSetup) return this.analyser

    this.source = this.ctx.createMediaElementSource(this.audioElement)
    this.inputGain = this.ctx.createGain()
    this.inputGain.gain.value = 1.0

    this.compressor = this.ctx.createDynamicsCompressor()
    this.compressor.threshold.value = -24
    this.compressor.knee.value = 12
    this.compressor.ratio.value = 6
    this.compressor.attack.value = 0.002
    this.compressor.release.value = 0.15

    this.limiter = this.ctx.createDynamicsCompressor()
    this.limiter.threshold.value = -2
    this.limiter.knee.value = 2
    this.limiter.ratio.value = 12
    this.limiter.attack.value = 0.001
    this.limiter.release.value = 0.05

    const bufferSize = 2 * this.ctx.sampleRate
    this.hissBuffer = this.ctx.createBuffer(1, bufferSize, this.ctx.sampleRate)
    const output = this.hissBuffer.getChannelData(0)
    for (let i = 0; i < bufferSize; i++) {
      output[i] = Math.random() * 2 - 1
    }

    this.hissBandpass = this.ctx.createBiquadFilter()
    this.hissBandpass.type = 'bandpass'
    this.hissBandpass.frequency.value = 2200
    this.hissBandpass.Q.value = 0.7

    this.hissGain = this.ctx.createGain()
    this.hissGain.gain.value = HISS_LEVEL

    this.backgroundNoise = new Audio(BACKGROUND_URL)
    this.backgroundNoise.loop = true
    this.backgroundNoise.preload = 'auto'
    this.backgroundSource = this.ctx.createMediaElementSource(this.backgroundNoise)
    this.backgroundGain = this.ctx.createGain()
    this.backgroundGain.gain.value = BACKGROUND_LEVEL

    this.backgroundNoise.addEventListener('loadedmetadata', () => {
      if (this.backgroundNoise && Number.isFinite(this.backgroundNoise.duration)) {
        this.backgroundNoise.currentTime = Math.random() * this.backgroundNoise.duration
      }
    }, { once: true })

    this.mixer = this.ctx.createGain()
    this.mixer.gain.value = 0

    this.analyser = this.ctx.createAnalyser()
    this.analyser.fftSize = 2048
    this.analyser.smoothingTimeConstant = 0.8

    this.hissBandpass.connect(this.hissGain)
    this.hissGain.connect(this.mixer)

    this.backgroundSource.connect(this.backgroundGain)
    this.backgroundGain.connect(this.mixer)

    this.source.connect(this.inputGain)
    this.inputGain.connect(this.mixer)

    this.mixer.connect(this.compressor)
    this.compressor.connect(this.limiter)

    this.isSetup = true
    return this.analyser
  }

  isUsable() {
    return this.isSetup && !!this.ctx && this.ctx.state !== 'closed' && this.engine?.context === this.ctx
  }

  activate() {
    if (!this.isSetup || !this.engine?.masterGain || this.ctx.state === 'closed') return false

    if (this._releaseTimer) {
      clearTimeout(this._releaseTimer)
      this._releaseTimer = null
    }

    if (!this.isActive) {
      const destination = this.engine.masterGain
      if (destination.context !== this.ctx) return false

      this.limiter.connect(this.analyser)
      this.analyser.connect(destination)
      this.destination = destination

      const hiss = this.ctx.createBufferSource()
      hiss.buffer = this.hissBuffer
      hiss.loop = true
      hiss.connect(this.hissBandpass)
      hiss.start()
      this.analogHiss = hiss

      this.backgroundNoise.play().catch(() => {})
      this.isActive = true
    }

    this.engine._rampGain(this.mixer, this.mixer.gain.value, 1.0, ATTACK_SEC)
    return true
  }

  deactivate(fadeSec = 0.05) {
    if (!this.isActive) return
    if (this._releaseTimer) clearTimeout(this._releaseTimer)

    const fade = Math.max(0.005, fadeSec)
    if (this.ctx.state !== 'closed') {
      this.engine._rampGain(this.mixer, this.mixer.gain.value, 0, fade)
    }
    this._releaseTimer = setTimeout(() => {
      this._releaseTimer = null
      this._release()
    }, fade * 1000 + RELEASE_HOLD_MS)
  }

  _release() {
    if (!this.isActive) return
    this.isActive = false

    if (this.analogHiss) {
      try { this.analogHiss.stop() } catch { /* already stopped */ }
      try { this.analogHiss.disconnect() } catch { /* already disconnected */ }
      this.analogHiss = null
    }

    this.backgroundNoise?.pause()

    try { this.limiter.disconnect(this.analyser) } catch { /* already disconnected */ }
    if (this.destination) {
      try { this.analyser.disconnect(this.destination) } catch { /* already disconnected */ }
      this.destination = null
    }
  }

  destroy() {
    if (this._releaseTimer) {
      clearTimeout(this._releaseTimer)
      this._releaseTimer = null
    }
    if (this.isSetup && this.ctx.state !== 'closed') {
      this.mixer.gain.cancelScheduledValues(0)
      this.mixer.gain.value = 0
    }
    this._release()

    if (this.backgroundNoise) {
      this.backgroundNoise.pause()
      this.backgroundNoise.removeAttribute('src')
      this.backgroundNoise.load()
      this.backgroundNoise = null
    }

    const nodes = [this.source, this.inputGain, this.hissBandpass, this.hissGain, this.backgroundSource, this.backgroundGain, this.mixer, this.compressor, this.limiter, this.analyser]
    nodes.forEach(node => {
      if (!node) return
      try { node.disconnect() } catch { /* already disconnected */ }
    })

    this.hissBuffer = null
    this.isSetup = false
    this.source = null
  }
}
