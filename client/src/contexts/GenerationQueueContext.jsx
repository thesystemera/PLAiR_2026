import { logger } from '../lib/logger'
import { createContext, useContext, useState, useCallback, useEffect, useMemo } from 'react'
import { api } from '../lib/api'
import { useAuth } from './AuthContext'
import { useWebSocketSubscribe } from './WebSocketContext'
import { useUISelector } from './UIStateContext'

const GenerationQueueContext = createContext(null)

const isActiveJob = (job) => job.status === 'processing'

const refundNote = (count) => {
  if (!count) return ''
  return count > 1
    ? ` ${count} generation credits were refunded.`
    : ' Your generation credit was refunded.'
}

export function GenerationQueueProvider({ children }) {
  const { isAuthenticated } = useAuth()
  const {
    publishQueueState,
    toastError,
    toastInfo,
    toastSuccess,
  } = useUISelector(state => ({
    publishQueueState: state.publishQueueState,
    toastError: state.toastError,
    toastInfo: state.toastInfo,
    toastSuccess: state.toastSuccess,
  }))
  const [jobs, setJobs] = useState([])
  const [isOpen, setIsOpen] = useState(false)
  const [hasLoadedInitialJobs, setHasLoadedInitialJobs] = useState(false)

  const upsertJob = useCallback((status) => {
    setJobs(prev => {
      const index = prev.findIndex(job => job.job_id === status.job_id)
      if (index === -1) {
        setIsOpen(true)
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
    if (data?.job_type !== 'generation' || !data.job_id) return
    const { event, ...job } = data
    const kind = event?.kind
    if (job.status === 'cancelled') {
      removeJob(job.job_id)
      if (kind === 'finished' && job.refunded > 0) {
        toastInfo(`Generation cancelled.${refundNote(job.refunded)}`, 5000, 'top', `generation-cancelled-${job.job_id}`)
      }
      return
    }
    upsertJob(job)
    if (kind === 'batch_completed') {
      const count = event.tracks?.length || 0
      if (count) toastSuccess(`${count} new song${count > 1 ? 's' : ''} added to your queue!`, 4000, 'top')
    } else if (kind === 'retrying') {
      toastInfo(`Generation retry ${event.attempt}/${event.max_attempts}...`, 3000, 'top')
    } else if (kind === 'batch_failed' && isActiveJob(job)) {
      toastError(`One song in your batch failed: ${event.error}${refundNote(event.refunded ? 1 : 0)}`, 6000, 'top')
    } else if (kind === 'finished' && job.status === 'failed') {
      toastError(`Song generation failed: ${job.error}${refundNote(job.refunded)}`, 8000, 'top', `generation-failed-${job.job_id}`)
    }
  }, [upsertJob, removeJob, toastError, toastInfo, toastSuccess])

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
    try {
      await api.cancelGenerationJob(jobId)
      removeJob(jobId)
    } catch (err) {
      logger.error('[GenerationQueue] Failed to cancel job:', jobId, err)
    }
  }, [removeJob])

  const clearCompleted = useCallback(() => {
    setJobs(prev => prev.filter(isActiveJob))
  }, [])

  const togglePanel = useCallback(() => {
    setIsOpen(prev => !prev)
  }, [])

  const hasActiveJobs = jobs.some(isActiveJob)

  useEffect(() => {
    publishQueueState({ hasActiveJobs })
  }, [hasActiveJobs, publishQueueState])

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