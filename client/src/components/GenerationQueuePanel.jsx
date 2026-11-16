import { memo } from 'react'
import { X, Trash2, CheckCircle2, XCircle } from 'lucide-react'
import { motion, AnimatePresence } from 'framer-motion'
import { useGenerationQueue } from '../contexts/GenerationQueueContext'
import { MOTION, PRESETS } from '../lib/motion'
import { Fade, FadeSwap, Pop } from './Motion'
import { InlineNote, StatusPill } from './Notice'
import { MediaEmptyState } from './MediaShared'
import { CardIconButton, OptionCard, PlayerSheet, SheetGrid } from './PlayerSheet'
import { useUISelector } from '../contexts/UIStateContext'
import { SHARE_JOB_ID, shareJobView, shareVideoJob } from '../lib/shareVideoJob'
import { jobTypeOf, taskKind } from '../lib/tasks'
import { formatTimeLeft } from '../lib/utils'

const STATUS_TONE = { completed: 'success', failed: 'error' }

function JobProgress({ progress, status, color }) {
  const fill = status === 'completed' ? '#34d399' : status === 'failed' ? '#f87171' : color
  return (
    <div className="h-1.5 bg-white/10 rounded-full overflow-hidden">
      <motion.div
        className="h-full w-full origin-left rounded-full"
        style={{ backgroundColor: fill }}
        initial={{ scaleX: 0 }}
        animate={{ scaleX: progress / 100 }}
        transition={MOTION.base}
      />
    </div>
  )
}

function JobItem({ job, onCancel, onRemove, onOpen }) {
  const jobType = jobTypeOf(job)
  const kind = taskKind(jobType)
  const isActive = job.status === 'processing'
  const isCompleted = job.status === 'completed'
  const isFailed = job.status === 'failed'
  const showProgress = jobType !== 'generation' || job.total_tracks > 0
  const action = isActive ? 'cancel' : (isCompleted || isFailed) ? 'remove' : null

  return (
    <motion.div {...PRESETS.listItem}>
      <OptionCard
        icon={kind.icon}
        tint={kind.color}
        tone={STATUS_TONE[job.status]}
        title={<span className="truncate">{job.title || kind.fallbackTitle}</span>}
        badge={<StatusPill color={kind.color}>{kind.badge}</StatusPill>}
        subtitle={(
          <span className="flex items-center gap-2">
            <Pop show={isCompleted || isFailed} className="flex flex-shrink-0">
              {isCompleted
                ? <CheckCircle2 size={12} className="text-emerald-300" />
                : <XCircle size={12} className="text-red-300" />}
            </Pop>
            <span className="truncate">{job.current_stage || 'Processing...'}</span>
            {job.total_tracks > 0 && (
              <span className="ml-auto whitespace-nowrap">{job.completed_tracks} / {job.total_tracks} tracks</span>
            )}
            <Fade as="span" show={isActive && job.eta_seconds > 0} className="ml-auto whitespace-nowrap">
              {formatTimeLeft(job.eta_seconds)}
            </Fade>
          </span>
        )}
        trailing={(
          <>
            <Pop show={isCompleted && !!onOpen} className="flex">
              <button
                onClick={onOpen}
                className="ui-press px-2.5 py-1 text-xs font-semibold text-white bg-white/15 hover:bg-white/25 rounded-full transition-colors"
              >
                Open
              </button>
            </Pop>
            <Pop show={!!action} className="flex">
              <FadeSwap swapKey={action} preset={PRESETS.iconSwap} mode="wait" className="flex">
                {action === 'cancel'
                  ? <CardIconButton icon={X} tone="danger" onClick={() => onCancel(job.job_id)} title={kind.cancelTitle} />
                  : <CardIconButton icon={X} onClick={() => onRemove(job.job_id)} title="Remove from list" />}
              </FadeSwap>
            </Pop>
          </>
        )}
        footer={(
          <>
            {showProgress && <JobProgress progress={job.progress_percent || 0} status={job.status} color={kind.color} />}
            <Fade show={isFailed && !!job.error}>
              <InlineNote tone="error">{job.error}</InlineNote>
            </Fade>
          </>
        )}
      />
    </motion.div>
  )
}

export const GenerationQueuePanel = memo(function GenerationQueuePanel() {
  const { jobs: listedJobs, isOpen, setIsOpen, cancelJob, removeJob, clearCompleted } = useGenerationQueue()
  const { status, title, stage, progress, etaSeconds, error, shareTrack, openShareModal } = useUISelector(state => ({
    status: state.shareVideoState.status,
    title: state.shareVideoState.title,
    stage: state.shareVideoState.stage,
    progress: state.shareVideoState.progress,
    etaSeconds: state.shareVideoState.etaSeconds,
    error: state.shareVideoState.error,
    shareTrack: state.shareVideoState.track,
    openShareModal: state.openShareModal,
  }))
  const shareJob = shareJobView({ status, title, stage, progress, etaSeconds, error })
  const jobs = shareJob ? [...listedJobs, shareJob] : listedJobs

  const completedJobs = jobs.filter(j => j.status === 'completed' || j.status === 'failed')
  const clearAllCompleted = () => {
    clearCompleted()
    shareVideoJob.clear()
  }

  return (
    <PlayerSheet
      open={isOpen}
      title="Rendering"
      onClose={() => setIsOpen(false)}
      actions={(
        <Pop show={completedJobs.length > 0} className="flex">
          <button
            onClick={clearAllCompleted}
            className="ui-press flex items-center gap-1.5 px-2.5 py-1 text-xs text-white/70 hover:text-white hover:bg-white/10 rounded-full transition-colors"
            title="Clear finished jobs"
          >
            <Trash2 size={13} />
            Clear finished
          </button>
        </Pop>
      )}
    >
      <FadeSwap swapKey={jobs.length === 0 ? 'empty' : 'jobs'} preset={PRESETS.fade} mode="wait">
        {jobs.length === 0 ? (
          <MediaEmptyState compact title="Nothing rendering" subtitle="New songs, uploads and share videos show here while they are made" />
        ) : (
          <SheetGrid>
            <AnimatePresence>
              {jobs.map(job => (
                <JobItem
                  key={job.job_id}
                  job={job}
                  onCancel={job.job_id === SHARE_JOB_ID ? shareVideoJob.cancel : cancelJob}
                  onRemove={job.job_id === SHARE_JOB_ID ? shareVideoJob.clear : removeJob}
                  onOpen={job.job_id === SHARE_JOB_ID ? () => openShareModal(shareTrack) : undefined}
                />
              ))}
            </AnimatePresence>
          </SheetGrid>
        )}
      </FadeSwap>
    </PlayerSheet>
  )
})
