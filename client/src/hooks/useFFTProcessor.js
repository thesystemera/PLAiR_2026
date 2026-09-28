import { useRef, useEffect } from 'react'
import { useUIState } from '../contexts/UIStateContext'

const FFT_REPORT_INTERVAL = 50
const FREQUENCY_BANDS = [
  { start: 20, end: 200 },
  { start: 200, end: 500 },
  { start: 500, end: 1000 },
  { start: 1000, end: 2000 },
  { start: 2000, end: 4000 },
  { start: 4000, end: 6000 },
  { start: 6000, end: 8000 },
  { start: 8000, end: 10000 }
]

export function useFFTProcessor(isActive, analyser, reportKey, options = {}) {
  const {
    numBars = 32,
    processingMode = 'frequency_bands'
  } = options

  const fftDataRef = useRef(new Array(numBars).fill(0))
  const dataArrayRef = useRef(null)
  const bandAmplitudesRef = useRef(new Float32Array(FREQUENCY_BANDS.length))
  const animationFrameRef = useRef(null)
  const { reportEngineStatus } = useUIState()

  useEffect(() => {
    window.registerRAFSource?.('FFTProcessor')
  }, [])

  useEffect(() => {
    if (fftDataRef.current.length !== numBars) fftDataRef.current = new Array(numBars).fill(0)
    if (!isActive || !analyser) return

    let lastReportTime = 0
    const fftData = fftDataRef.current
    const sampleRate = analyser.context?.sampleRate || 48000

    const processFFT = () => {
      try {
        if (!dataArrayRef.current || dataArrayRef.current.length !== analyser.frequencyBinCount) {
          dataArrayRef.current = new Uint8Array(analyser.frequencyBinCount)
        }

        analyser.getByteFrequencyData(dataArrayRef.current)

        if (processingMode === 'frequency_bands') {
          processFrequencyBands(dataArrayRef.current, fftData, numBars, analyser.fftSize, sampleRate, bandAmplitudesRef.current)
        } else if (processingMode === 'logarithmic') {
          processLogarithmicBands(dataArrayRef.current, fftData, numBars, analyser.fftSize, sampleRate)
        }

        const now = performance.now()
        if (now - lastReportTime >= FFT_REPORT_INTERVAL) {
          lastReportTime = now
          reportEngineStatus({ [reportKey]: fftData })
        }
      } catch {
        // FFT processing errors are intentionally suppressed - audio continues
      }

      window.__rafDebug?.sources && (window.__rafDebug.sources['FFTProcessor'] = (window.__rafDebug.sources['FFTProcessor'] || 0) + 1)
      animationFrameRef.current = requestAnimationFrame(processFFT)
    }

    processFFT()

    return () => {
      if (animationFrameRef.current) {
        cancelAnimationFrame(animationFrameRef.current)
        animationFrameRef.current = null
      }
      fftData.fill(0)
      reportEngineStatus({ [reportKey]: fftData })
    }
  }, [isActive, analyser, reportKey, processingMode, numBars, reportEngineStatus])

  return fftDataRef
}

function processFrequencyBands(allData, fftData, numBars, fftSize, sampleRate, bandAmplitudes) {
  const binWidth = sampleRate / fftSize
  const bandCount = FREQUENCY_BANDS.length
  for (let b = 0; b < bandCount; b++) {
    const band = FREQUENCY_BANDS[b]
    const startIndex = Math.floor(band.start / binWidth)
    const endIndex = Math.floor(band.end / binWidth)

    if (startIndex >= allData.length || endIndex > allData.length) {
      bandAmplitudes[b] = 0
      continue
    }

    let sum = 0
    let count = 0
    for (let i = startIndex; i < endIndex; i++) {
      sum += allData[i]
      count++
    }
    bandAmplitudes[b] = count > 0 ? sum / count : 0
  }

  for (let i = 0; i < numBars; i++) {
    const bandIndex = Math.floor(i / (numBars / bandCount))
    fftData[i] = bandAmplitudes[bandIndex]
  }
}

function processLogarithmicBands(allData, fftData, numBars, fftSize, sampleRate) {
  const binSize = sampleRate / fftSize

  for (let i = 0; i < numBars; i++) {
    const freqStart = 20 * Math.pow(500, i / numBars)
    const freqEnd = 20 * Math.pow(500, (i + 1) / numBars)

    const startBin = Math.floor(freqStart / binSize)
    const endBin = Math.ceil(freqEnd / binSize)

    if (startBin >= allData.length) {
      fftData[i] = 0
      continue
    }

    let sum = 0
    let count = 0
    for (let bin = startBin; bin < Math.min(endBin, allData.length); bin++) {
      sum += allData[bin]
      count++
    }

    fftData[i] = count > 0 ? sum / count : 0
  }
}
