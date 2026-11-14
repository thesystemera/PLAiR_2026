import { renderVideo } from './offlineVideoRenderer'

const clipWaits = new Map()
let clipRequests = 0

function requestClipFrame(index, time) {
  return new Promise(resolve => {
    const id = ++clipRequests
    clipWaits.set(id, resolve)
    self.postMessage({ type: 'clip', id, index, time })
  })
}

self.onmessage = ({ data }) => {
  if (data.type === 'clipFrame') {
    clipWaits.get(data.id)?.(data.frame || null)
    clipWaits.delete(data.id)
    return
  }
  if (data.type !== 'start') return
  renderVideo({
    ...data.job,
    requestClipFrame,
    onProgress: progress => self.postMessage({ type: 'progress', progress }),
    onStatus: stage => self.postMessage({ type: 'stage', stage }),
  }).then(
    ({ buffer, stats }) => self.postMessage({ type: 'done', buffer, stats }, [buffer]),
    error => self.postMessage({ type: 'error', message: error?.message || 'Failed to generate video' }),
  )
}
