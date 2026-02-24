export class DJBroadcastChain {
  constructor(audioContext, audioElement) {
    this.ctx = audioContext
    this.audioElement = audioElement

    this.source = null
    this.inputGain = null
    this.compressor = null
    this.limiter = null
    this.analogHiss = null
    this.hissBandpass = null
    this.hissGain = null
    this.backgroundNoise = null
    this.backgroundSource = null
    this.backgroundGain = null
    this.mixer = null
    this.analyser = null

    this.isSetup = false
    this.isConnected = false
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
    const noiseBuffer = this.ctx.createBuffer(1, bufferSize, this.ctx.sampleRate)
    const output = noiseBuffer.getChannelData(0)
    for (let i = 0; i < bufferSize; i++) {
      output[i] = Math.random() * 2 - 1
    }
    this.analogHiss = this.ctx.createBufferSource()
    this.analogHiss.buffer = noiseBuffer
    this.analogHiss.loop = true

    this.hissBandpass = this.ctx.createBiquadFilter()
    this.hissBandpass.type = 'bandpass'
    this.hissBandpass.frequency.value = 2200
    this.hissBandpass.Q.value = 0.7

    this.hissGain = this.ctx.createGain()
    this.hissGain.gain.value = 0.0008

    this.backgroundNoise = new Audio('/audio/studio/background.mp3')
    this.backgroundNoise.loop = true
    this.backgroundSource = this.ctx.createMediaElementSource(this.backgroundNoise)
    this.backgroundGain = this.ctx.createGain()
    this.backgroundGain.gain.value = 0.1

    this.backgroundNoise.addEventListener('loadedmetadata', () => {
      if (Number.isFinite(this.backgroundNoise.duration)) {
        this.backgroundNoise.currentTime = Math.random() * this.backgroundNoise.duration
      }
    }, { once: true })

    this.mixer = this.ctx.createGain()
    this.mixer.gain.value = 0

    this.analyser = this.ctx.createAnalyser()
    this.analyser.fftSize = 2048
    this.analyser.smoothingTimeConstant = 0.8

    this.analogHiss.connect(this.hissBandpass)
    this.hissBandpass.connect(this.hissGain)
    this.hissGain.connect(this.mixer)

    this.backgroundSource.connect(this.backgroundGain)
    this.backgroundGain.connect(this.mixer)

    this.source.connect(this.inputGain)
    this.inputGain.connect(this.mixer)

    this.mixer.connect(this.compressor)
    this.compressor.connect(this.limiter)
    this.limiter.connect(this.analyser)

    if (this.analogHiss.context.state !== 'closed') {
      this.analogHiss.start(0)
    }

    this.isSetup = true
    return this.analyser
  }

  connect(destinationNode) {
    if (!this.isSetup || !destinationNode) return

    if (!this.isConnected) {
      try {
        this.analyser.disconnect()
        this.analyser.connect(destinationNode)
      } catch { /* error intentionally suppressed */ }

      this.backgroundNoise.play().catch(() => {})
      this.isConnected = true
    }

    this.mixer.gain.value = 1.0
  }

  disconnect() {
    if (!this.isSetup || !this.isConnected) return

    this.mixer.gain.value = 0
    this.backgroundNoise.pause()
    this.isConnected = false
    try {
      this.analyser.disconnect()
    } catch { /* error intentionally suppressed */ }
  }

  destroy() {
    this.disconnect()

    if (this.backgroundNoise) {
      this.backgroundNoise.pause()
      this.backgroundNoise = null
    }

    if (this.analogHiss) {
      try { this.analogHiss.stop() } catch { /* error intentionally suppressed */ }
      this.analogHiss = null
    }

    this.isSetup = false
    this.source = null
  }
}