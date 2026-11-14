import { X, Loader2, Trash2, CheckCircle2, XCircle, Video } from 'lucide-react'
import { motion, AnimatePresence } from 'framer-motion'
import { useGenerationQueue } from '../contexts/GenerationQueueContext'
import { GLASS } from '../lib/themeManager'
import { MOTION, PRESETS } from '../lib/motion'
import { Expandable, Fade, FadeSwap, Pop } from './Motion'
import { InlineNote } from './Notice'
import { MediaEmptyState } from './MediaShared'
import { useUISelector } from '../contexts/UIStateContext'
import { SHARE_JOB_ID, shareJobView, shareVideoJob } from '../lib/shareVideoJob'

const JOB_KINDS = {
  generation: { badge: 'Generating', fallbackTitle: 'Generating tracks...', cancelTitle: 'Cancel generation' },
  share_video: { badge: 'Video', fallbackTitle: 'Share video', cancelTitle: 'Cancel video' },
}

function JobItem({ job, onCancel, onRemove, onOpen }) {
  const kind = JOB_KINDS[job.job_type] || JOB_KINDS.generation
  const isVideo = job.job_type === 'share_video'
  const showProgress = isVideo || job.total_tracks > 0
  const isActive = job.status === 'processing'
  const isCompleted = job.status === 'completed'
  const isFailed = job.status === 'failed'
  const progress = job.progress_percent || 0
  const statusIcon = isActive ? 'active' : isCompleted ? 'completed' : isFailed ? 'failed' : null
  const action = isActive ? 'cancel' : (isCompleted || isFailed) ? 'remove' : null

  return (
    <motion.div
      {...PRESETS.listItem}
      className={`
        flex flex-col gap-2 p-3 rounded-lg border transition-colors
        ${isCompleted ? 'bg-green-500/10 border-green-500/30' : ''}
        ${isFailed ? 'bg-red-500/10 border-red-500/30' : ''}
        ${isActive ? 'bg-purple-500/10 border-purple-500/30' : ''}
      `}
    >
      <div className="flex items-center gap-3">
        <div className="text-2xl w-8 h-8 flex items-center justify-center flex-shrink-0">
          {isVideo ? <Video size={22} className="text-white/80" /> : '✨'}
        </div>

        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium text-white flex items-center gap-2 mb-1">
            <span className="truncate">{job.title || kind.fallbackTitle}</span>
            <span className="text-xs px-2 py-0.5 rounded-full bg-white/20 text-white/90 font-medium whitespace-nowrap">
              {kind.badge}
            </span>
          </div>

          <div className="flex items-center gap-2 text-xs text-white/60">
            <Pop show={!!statusIcon} className="flex flex-shrink-0">
              <FadeSwap swapKey={statusIcon} preset={PRESETS.iconSwap} mode="wait" className="flex">
                {statusIcon === 'active' ? (
                  <Loader2 size={12} className="animate-spin flex-shrink-0" />
                ) : statusIcon === 'completed' ? (
                  <CheckCircle2 size={12} className="text-green-400 flex-shrink-0" />
                ) : (
                  <XCircle size={12} className="text-red-400 flex-shrink-0" />
                )}
              </FadeSwap>
            </Pop>

            <span className="truncate">{job.current_stage || 'Processing...'}</span>

            {job.total_tracks > 0 && (
              <span className="ml-auto whitespace-nowrap">
                {job.completed_tracks} / {job.total_tracks} tracks
              </span>
            )}
          </div>
        </div>

        <Pop show={isCompleted && !!onOpen} className="flex">
          <button
            onClick={onOpen}
            className="ui-press px-2.5 py-1 text-xs font-semibold text-white bg-white/15 hover:bg-white/25 rounded-full transition"
          >
            Open
          </button>
        </Pop>

        <Pop show={!!action} className="flex">
          <FadeSwap swapKey={action} preset={PRESETS.iconSwap} mode="wait" className="flex">
            {action === 'cancel' ? (
              <button
                onClick={() => onCancel(job.job_id)}
                className="ui-press p-1.5 hover:bg-red-500/20 rounded transition"
                title={kind.cancelTitle}
              >
                <X size={16} className="text-red-400" />
              </button>
            ) : (
              <button
                onClick={() => onRemove(job.job_id)}
                className="ui-press p-1.5 hover:bg-white/10 rounded transition"
                title="Remove from list"
              >
                <X size={16} className="text-gray-400" />
              </button>
            )}
          </FadeSwap>
        </Pop>
      </div>

      {showProgress && (
        <div className="h-1.5 bg-white/10 rounded-full overflow-hidden">
          <motion.div
            className={`h-full w-full origin-left ${
              isCompleted ? 'bg-green-500' :
              isFailed ? 'bg-red-500' :
              'bg-gradient-to-r from-purple-600 to-blue-600'
            }`}
            initial={{ scaleX: 0 }}
            animate={{ scaleX: progress / 100 }}
            transition={MOTION.base}
          />
        </div>
      )}

      <Fade show={isFailed && !!job.error}>
        <InlineNote tone="error">{job.error}</InlineNote>
      </Fade>
    </motion.div>
  )
}

export function GenerationQueuePanel() {
  const { jobs: generationJobs, isOpen, setIsOpen, cancelJob, removeJob, clearCompleted } = useGenerationQueue()
  const { status, title, stage, progress, error, shareTrack, openShareModal } = useUISelector(state => ({
    status: state.shareVideoState.status,
    title: state.shareVideoState.title,
    stage: state.shareVideoState.stage,
    progress: state.shareVideoState.progress,
    error: state.shareVideoState.error,
    shareTrack: state.shareVideoState.track,
    openShareModal: state.openShareModal,
  }))
  const shareJob = shareJobView({ status, title, stage, progress, error })
  const jobs = shareJob ? [...generationJobs, shareJob] : generationJobs

  const completedJobs = jobs.filter(j => j.status === 'completed' || j.status === 'failed')
  const clearAllCompleted = () => {
    clearCompleted()
    shareVideoJob.clear()
  }

  return (
    <Expandable open={isOpen} className={GLASS.expandPanel}>
        <div className="max-w-screen-2xl mx-auto px-3 py-2 md:px-4 md:py-3">
          <div className={GLASS.expandPanelHeader}>
            <h3 className="m-0 text-lg font-semibold text-white">Rendering</h3>
            <div className="flex items-center gap-2">
              <Pop show={completedJobs.length > 0} className="flex">
                <button
                  onClick={clearAllCompleted}
                  className="ui-press flex items-center gap-2 px-3 py-1.5 text-xs text-red-400 hover:text-red-300 hover:bg-red-500/10 rounded-lg transition"
                  title="Clear completed jobs"
                >
                  <Trash2 size={14} />
                  Clear Completed
                </button>
              </Pop>
              <button
                onClick={() => setIsOpen(false)}
                className="ui-press bg-transparent border-none text-white/60 text-xl cursor-pointer p-1 leading-none transition-colors hover:text-white"
              >
                <X size={20} />
              </button>
            </div>
          </div>

          <FadeSwap swapKey={jobs.length === 0 ? 'empty' : 'jobs'} preset={PRESETS.fade} mode="wait">
            {jobs.length === 0 ? (
              <MediaEmptyState compact title="Nothing rendering" subtitle="New songs and share videos show here while they are made" />
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2 pb-2">
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
              </div>
            )}
          </FadeSwap>
        </div>
    </Expandable>
  )
}