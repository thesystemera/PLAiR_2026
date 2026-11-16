import { logger } from '../lib/logger'
import { createContext, useContext, useState, useCallback, useEffect, useMemo } from 'react'
import { api } from '../lib/api'
import { useAuth } from './AuthContext'
import { useWebSocketSubscribe } from './WebSocketContext'
import { useUISelector } from './UIStateContext'
import { usePlaybackActions } from './PlaybackContext'
import { useTaskNotices } from '../components/TaskNotice'
import { isActiveTask as isActiveJob, jobTypeOf, taskProgress } from '../lib/tasks'

const GenerationQueueContext = createContext(null)

const LISTED_JOB_TYPES = new Set(['generation', 'upload'])

const refundNote = (count) => {
  if (!count) return ''
  return count > 1
    ? ` ${count} generation credits were refunded.`
    : ' Your generation credit was refunded.'
}

export function GenerationQueueProvider({ children }) {
  const { isAuthenticated } = useAuth()
  const { publishQueueState, toastError } = useUISelector(state => ({
    publishQueueState: state.publishQueueState,
    toastError: state.toastError,
  }))
  const { playTrack } = usePlaybackActions()
  const { taskDone, taskFailed, taskNote } = useTaskNotices()
  const [jobs, setJobs] = useState([])
  const [isOpen, setIsOpen] = useState(false)
  const [hasLoadedInitialJobs, setHasLoadedInitialJobs] = useState(false)

  const upsertJob = useCallback((status) => {
    setJobs(prev => {
      const index = prev.findIndex(job => job.job_id === status.job_id)
      if (index === -1) {
        if (jobTypeOf(status) === 'generation') setIsOpen(true)
        return [...prev, status]
      }
      const next = prev.slice()
      next[index] = status
      return next
    })
  }, [])

  const removeJob = useCallback((jobId) => {
    setJobs(prev => prev.filter(job => job.job_id !== jobId))
  }, [])

  const handleTaskProgress = useCallback((data) => {
    if (!LISTED_JOB_TYPES.has(data?.job_type) || !data.job_id) return
    const { event, ...job } = data
    const kind = event?.kind
    if (job.status === 'cancelled') {
      removeJob(job.job_id)
      if (kind === 'finished' && job.refunded > 0) {
        taskNote({ jobType: job.job_type, key: `generation-cancelled-${job.job_id}`, text: `Generation cancelled.${refundNote(job.refunded)}` })
      }
      return
    }
    upsertJob(job)
    if (job.job_type !== 'generation') return
    if (kind === 'batch_completed') {
      const count = event.tracks?.length || 0
      if (count) {
        taskDone({
          jobType: 'generation',
          text: `${count} new song${count > 1 ? 's' : ''} in your queue`,
          actionLabel: 'Play',
          onAction: () => void playTrack(event.tracks[0]),
        })
      }
    } else if (kind === 'retrying') {
      taskNote({ jobType: 'generation', text: `Generation retry ${event.attempt}/${event.max_attempts}...` })
    } else if (kind === 'batch_failed' && isActiveJob(job)) {
      taskFailed({ jobType: 'generation', text: `One song in your batch failed: ${event.error}${refundNote(event.refunded ? 1 : 0)}` })
    } else if (kind === 'finished' && job.status === 'failed') {
      taskFailed({ jobType: 'generation', key: `generation-failed-${job.job_id}`, text: `Song generation failed: ${job.error}${refundNote(job.refunded)}` })
    }
  }, [upsertJob, removeJob, taskDone, taskFailed, taskNote, playTrack])

  useWebSocketSubscribe('task_progress', handleTaskProgress)

  useEffect(() => {
    if (!isAuthenticated) {
      queueMicrotask(() => {
        setHasLoadedInitialJobs(false)
        setJobs([])
        setIsOpen(false)
      })
    }
  }, [isAuthenticated])

  useEffect(() => {
    if (!isAuthenticated || hasLoadedInitialJobs) return

    const loadJobs = async () => {
      try {
        const response = await api.getGenerationJobs()
        const loaded = (response.jobs || []).filter(job => job.status !== 'cancelled')
        if (loaded.length > 0) {
          setJobs(loaded)
          if (loaded.some(isActiveJob)) setIsOpen(true)
        }
        setHasLoadedInitialJobs(true)
      } catch (err) {
        logger.error('[GenerationQueue] Failed to load initial jobs:', err)
        setHasLoadedInitialJobs(true)
      }
    }

    void loadJobs()
  }, [isAuthenticated, hasLoadedInitialJobs])

  const cancelJob = useCallback(async (jobId) => {
    const job = jobs.find(item => item.job_id === jobId)
    const isUpload = job && jobTypeOf(job) === 'upload'
    try {
      if (isUpload) await api.cancelUploadJob(jobId)
      else {
        await api.cancelGenerationJob(jobId)
        removeJob(jobId)
      }
    } catch (err) {
      logger.error('[GenerationQueue] Failed to cancel job:', jobId, err)
      const what = isUpload ? 'that upload' : 'that generation'
      toastError(err?.message ? `Couldn't cancel ${what}: ${err.message}` : `Couldn't cancel ${what}`)
    }
  }, [jobs, removeJob, toastError])

  const clearCompleted = useCallback(() => {
    setJobs(prev => prev.filter(isActiveJob))
  }, [])

  const togglePanel = useCallback(() => {
    setIsOpen(prev => !prev)
  }, [])

  const generation = taskProgress(jobs, 'generation')
  const upload = taskProgress(jobs, 'upload')
  const hasActiveJobs = generation.active

  useEffect(() => {
    publishQueueState({
      hasActiveJobs,
      generationProgress: generation.progress,
      uploadActive: upload.active,
      uploadProgress: upload.progress,
    })
  }, [hasActiveJobs, generation.progress, upload.active, upload.progress, publishQueueState])

  const value = useMemo(() => ({
    jobs,
    isOpen,
    setIsOpen,
    togglePanel,
    cancelJob,
    removeJob,
    clearCompleted,
    hasActiveJobs
  }), [jobs, isOpen, togglePanel, cancelJob, removeJob, clearCompleted, hasActiveJobs])

  return (
    <GenerationQueueContext.Provider value={value}>
      {children}
    </GenerationQueueContext.Provider>
  )
}

export function useGenerationQueue() {
  const context = useContext(GenerationQueueContext)
  if (!context) {
    throw new Error('useGenerationQueue must be used within a GenerationQueueProvider')
  }
  return context
}