import { useRef, useEffect } from 'react'
import { useUIState } from '../contexts/UIStateContext'

export function useFFTProcessor(isActive, analyser, reportKey, options = {}) {
  const {
    numBars = 32,
    processingMode = 'frequency_bands' // or 'logarithmic'
  } = options

  const fftDataRef = useRef(new Array(numBars).fill(0))
  const dataArrayRef = useRef(null)
  const animationFrameRef = useRef(null)
  const { reportEngineStatus } = useUIState()

  // Register RAF source for debugging
  useEffect(() => {
    window.registerRAFSource?.('FFTProcessor')
  }, [])

  useEffect(() => {
    if (!isActive) {
      if (animationFrameRef.current) {
        cancelAnimationFrame(animationFrameRef.current)
        animationFrameRef.current = null
      }
      return
    }

    const FFT_REPORT_INTERVAL = 50  // 20fps for analysis
    let lastReportTime = 0

    const processFFT = () => {
      if (analyser && isActive) {
        try {
          if (!dataArrayRef.current || dataArrayRef.current.length !== analyser.frequencyBinCount) {
            dataArrayRef.current = new Uint8Array(analyser.frequencyBinCount)
          }

          analyser.getByteFrequencyData(dataArrayRef.current)

          if (processingMode === 'frequency_bands') {
            processFrequencyBands(dataArrayRef.current, fftDataRef.current, numBars, analyser.fftSize)
          } else if (processingMode === 'logarithmic') {
            processLogarithmicBands(dataArrayRef.current, fftDataRef.current, numBars, analyser.fftSize)
          }

          // Report at 20fps - visual layer interpolates smoothly
          const now = performance.now()
          if (reportEngineStatus && now - lastReportTime >= FFT_REPORT_INTERVAL) {
            lastReportTime = now
            reportEngineStatus({ [reportKey]: fftDataRef.current })
          }
        } catch {
          // FFT processing errors are intentionally suppressed - audio continues
        }
      }

      if (isActive) {
        window.__rafDebug?.sources && (window.__rafDebug.sources['FFTProcessor'] = (window.__rafDebug.sources['FFTProcessor'] || 0) + 1)
        animationFrameRef.current = requestAnimationFrame(processFFT)
      }
    }

    processFFT()

    return () => {
      if (animationFrameRef.current) {
        cancelAnimationFrame(animationFrameRef.current)
      }
    }
  }, [isActive, analyser, reportKey, processingMode, numBars, reportEngineStatus])

  return fftDataRef
}

function processFrequencyBands(allData, fftData, numBars, fftSize) {
  const sampleRate = 48000
  const frequencyBands = [
    { start: 20, end: 200 },
    { start: 200, end: 500 },
    { start: 500, end: 1000 },
    { start: 1000, end: 2000 },
    { start: 2000, end: 4000 },
    { start: 4000, end: 6000 },
    { start: 6000, end: 8000 },
    { start: 8000, end: 10000 }
  ]

  const bandAmplitudes = new Array(frequencyBands.length)
  for (let b = 0; b < frequencyBands.length; b++) {
    const band = frequencyBands[b]
    const startIndex = Math.floor(band.start / (sampleRate / fftSize))
    const endIndex = Math.floor(band.end / (sampleRate / fftSize))

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
    const bandIndex = Math.floor(i / (numBars / frequencyBands.length))
    fftData[i] = bandAmplitudes[bandIndex]
  }
}

function processLogarithmicBands(allData, fftData, numBars, fftSize) {
  const sampleRate = 48000
  const binSize = sampleRate / fftSize

  for (let i = 0; i < numBars; i++) {
    const freqStart = 20 * Math.pow(500, i / numBars) // Log scale
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