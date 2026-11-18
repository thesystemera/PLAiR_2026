import { renderVideo } from './offlineVideoRenderer'

self.onmessage = ({ data }) => {
  if (data.type !== 'start') return
  renderVideo({
    ...data.job,
    onProgress: progress => self.postMessage({ type: 'progress', progress }),
    onStatus: stage => self.postMessage({ type: 'stage', stage }),
  }).then(
    ({ blob, stats }) => self.postMessage({ type: 'done', blob, stats }),
    error => self.postMessage({ type: 'error', message: error?.message || 'Failed to generate video' }),
  )
}
