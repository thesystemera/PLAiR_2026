import { memo } from 'react'
import { useUISelector } from '../contexts/UIStateContext'
import { shareVideoBusy } from '../lib/shareVideoJob'
import { taskKind } from '../lib/tasks'
import { Fade } from './Motion'

const ARC_EMPTY = 100
const SPIN_ARC = 72

function useTask(jobType) {
  return useUISelector(state => {
    if (jobType === 'share_video') {
      const active = shareVideoBusy(state.shareVideoState.status)
      const progress = state.shareVideoState.progress
      return { active, progress: active && progress > 0 ? progress : null }
    }
    if (jobType === 'upload') {
      return { active: state.queueState.uploadActive, progress: state.queueState.uploadProgress }
    }
    return { active: state.queueState.hasActiveJobs, progress: state.queueState.generationProgress }
  })
}

export const TaskRing = memo(function TaskRing({ jobType, className = '', children }) {
  const { active, progress } = useTask(jobType)
  const known = typeof progress === 'number'
  const offset = known ? ARC_EMPTY * (1 - progress) : SPIN_ARC

  return (
    <span className={`relative inline-flex flex-shrink-0 ${className}`}>
      {children}
      <Fade show={!!active} as="span" className="pointer-events-none absolute -inset-1" aria-hidden="true">
        <svg viewBox="0 0 40 40" className="ui-task-ring" data-spin={known ? 'false' : 'true'} style={{ color: taskKind(jobType).color }}>
          <circle cx="20" cy="20" r="18.5" pathLength="100" className="ui-task-ring-track" />
          <circle cx="20" cy="20" r="18.5" pathLength="100" className="ui-task-ring-arc" style={{ strokeDashoffset: offset }} />
        </svg>
      </Fade>
    </span>
  )
})
