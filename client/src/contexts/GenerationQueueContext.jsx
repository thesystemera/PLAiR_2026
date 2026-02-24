import { logger } from '../lib/logger'
import { createContext, useContext, useState, useCallback, useEffect } from 'react'
import { api } from '../lib/api'
import { useAuth } from './AuthContext'
import { useWebSocketSubscribe } from './WebSocketContext'
import { useUIState } from './UIStateContext'

const GenerationQueueContext = createContext() // Okay this might actually be a BUG BUG BUG

const normalizeJobData = (data) => ({
  id: data.job_id || data.id,
  type: data.type || 'generate',
  query: data.title || data.query || data.track_title || 'Generating tracks...',
  status: data.status || 'pending',
  total_tracks: data.total_tracks || 0,
  completed_tracks: data.completed_tracks || 0,
  current_stage: data.current_stage || 'Processing...',
  progress_percent: data.progress_percent !== undefined ? data.progress_percent : 0,
  error: data.error || null,
  tracks: data.tracks || [],
  created_at: Date.now()
})

export function GenerationQueueProvider({ children }) {
  const { isAuthenticated } = useAuth()
  const { publishQueueState } = useUIState()
  const [jobs, setJobs] = useState([])
  const [isOpen, setIsOpen] = useState(false)
  const [hasLoadedInitialJobs, setHasLoadedInitialJobs] = useState(false)

  const addJob = useCallback((jobData) => {
    const newJob = normalizeJobData(jobData)
    setJobs(prev => [...prev, newJob])
    setIsOpen(true)
    return newJob.id
  }, [])

  const updateJobFromStatus = useCallback((jobId, statusData) => {
    setJobs(prev => {
      const existingJob = prev.find(job => job.id === jobId)

      if (!existingJob) {
        const newJob = normalizeJobData({ ...statusData, job_id: jobId })
        setIsOpen(true)
        return [...prev, newJob]
      }

      return prev.map(job =>
        job.id === jobId
          ? {
              ...job,
              status: statusData.status || job.status,
              total_tracks: statusData.total_tracks || job.total_tracks,
              completed_tracks: statusData.completed_tracks || 0,
              current_stage: statusData.current_stage || job.current_stage,
              query: statusData.title || job.query,
              progress_percent: statusData.progress_percent !== undefined ? statusData.progress_percent : job.progress_percent,
              error: statusData.error || null,
              tracks: statusData.tracks || job.tracks
            }
          : job
      )
    })
  }, [])

  const handleGenerationStarted = useCallback((data) => {
    if (data?.job_id) {
      updateJobFromStatus(data.job_id, data)
    }
  }, [updateJobFromStatus])

  const handleGenerationUpdate = useCallback((data) => {
    if (data?.job_id) {
      updateJobFromStatus(data.job_id, data.status || data)
    }
  }, [updateJobFromStatus])

  const handleGenerationCompleted = useCallback((data) => {
    if (data?.job_id) {
      updateJobFromStatus(data.job_id, { ...data, status: 'completed' })
    }
  }, [updateJobFromStatus])

  const handleGenerationFailed = useCallback((data) => {
    if (data?.job_id) {
      updateJobFromStatus(data.job_id, { ...data, status: 'failed' })
    }
  }, [updateJobFromStatus])

  useWebSocketSubscribe('generation_started', handleGenerationStarted)
  useWebSocketSubscribe('generation_stage_update', handleGenerationUpdate)
  useWebSocketSubscribe('generation_processing', handleGenerationUpdate)
  useWebSocketSubscribe('generation_job_completed', handleGenerationCompleted)
  useWebSocketSubscribe('generation_batch_completed', handleGenerationUpdate)
  useWebSocketSubscribe('generation_batch_failed', handleGenerationFailed)

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
        if (response.jobs && response.jobs.length > 0) {
          const convertedJobs = response.jobs.map(normalizeJobData)
          setJobs(convertedJobs)

          const hasActiveJobs = convertedJobs.some(job =>
            job.status === 'pending' || job.status === 'processing' || job.status === 'running'
          )
          if (hasActiveJobs) {
            setIsOpen(true)
          }
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
      setJobs(prev => prev.filter(job => job.id !== jobId))
    } catch (err) {
      logger.error('[GenerationQueue] Failed to cancel job:', jobId, err)
    }
  }, [])

  const removeJob = useCallback((jobId) => {
    setJobs(prev => prev.filter(job => job.id !== jobId))
  }, [])

  const clearCompleted = useCallback(() => {
    setJobs(prev => prev.filter(job => job.status !== 'completed' && job.status !== 'failed'))
  }, [])

  const togglePanel = useCallback(() => {
    setIsOpen(prev => !prev)
  }, [])

  const hasActiveJobs = jobs.some(job =>
    job.status === 'pending' || job.status === 'processing' || job.status === 'running'
  )

  useEffect(() => {
    publishQueueState({ hasActiveJobs })
  }, [hasActiveJobs, publishQueueState])

  const value = {
    jobs,
    isOpen,
    setIsOpen,
    togglePanel,
    addJob,
    cancelJob,
    removeJob,
    clearCompleted,
    hasActiveJobs
  }

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