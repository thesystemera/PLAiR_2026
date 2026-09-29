import { api } from './api'

export const UPLOAD_FINISHED_STATUSES = new Set(['done', 'failed', 'cancelled'])

const SETTLE_DELAY_MS = 1000
const SETTLE_TRIES = 15

const wait = (ms) => new Promise(resolve => setTimeout(resolve, ms))

export const isUploadFinished = (job) => UPLOAD_FINISHED_STATUSES.has(job?.status)

export async function fetchFinishedUploadJob(uploadId, { shouldStop } = {}) {
  let job = await api.getUploadJob(uploadId)
  for (let attempt = 1; attempt < SETTLE_TRIES && !isUploadFinished(job) && !shouldStop?.(); attempt++) {
    await wait(SETTLE_DELAY_MS)
    job = await api.getUploadJob(uploadId)
  }
  return job
}

export const uploadJobTitle = (job) => job?.result?.metadata?.title || job?.filename || 'Your track'
