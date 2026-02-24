import { X, Loader2, Trash2, CheckCircle2, XCircle } from 'lucide-react'
import { motion, AnimatePresence } from 'framer-motion'
import { useGenerationQueue } from '../contexts/GenerationQueueContext'
import { TRANSITIONS } from '../lib/themeManager'

function getJobTypeIcon(type) {
  switch (type) {
    case 'generate':
      return '✨'
    case 'similar':
      return '🔄'
    case 'similar_style':
      return '🎨'
    default:
      return '🎵'
  }
}

function getJobTypeLabel(type) {
  switch (type) {
    case 'generate':
      return 'Generating'
    case 'similar':
      return 'Similar Tracks'
    case 'similar_style':
      return 'Similar Style'
    default:
      return 'Processing'
  }
}

function formatStage(job) {
  return job.current_stage || 'Processing...'
}

function JobItem({ job, onCancel, onRemove }) {
  const isActive = job.status === 'pending' || job.status === 'processing' || job.status === 'running'
  const isCompleted = job.status === 'completed'
  const isFailed = job.status === 'failed'
  const progress = job.progress_percent !== undefined
    ? job.progress_percent
    : (job.total_tracks > 0 ? (job.completed_tracks / job.total_tracks) * 100 : 0)

  return (
    <motion.div
      initial={{ opacity: 0, y: -10 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: 10 }}
      className={`
        flex flex-col gap-2 p-3 rounded-lg border transition-all
        ${isCompleted ? 'bg-green-500/10 border-green-500/30' : ''}
        ${isFailed ? 'bg-red-500/10 border-red-500/30' : ''}
        ${isActive ? 'bg-purple-500/10 border-purple-500/30' : ''}
      `}
    >
      <div className="flex items-center gap-3">
        <div className="text-2xl w-8 h-8 flex items-center justify-center flex-shrink-0">
          {getJobTypeIcon(job.type)}
        </div>

        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium text-white flex items-center gap-2 mb-1">
            <span className="truncate">{job.query}</span>
            <span className="text-xs px-2 py-0.5 rounded-full bg-white/20 text-white/90 font-medium whitespace-nowrap">
              {getJobTypeLabel(job.type)}
            </span>
          </div>

          <div className="flex items-center gap-2 text-xs text-white/60">
            {isActive && <Loader2 size={12} className="animate-spin flex-shrink-0" />}
            {isCompleted && <CheckCircle2 size={12} className="text-green-400 flex-shrink-0" />}
            {isFailed && <XCircle size={12} className="text-red-400 flex-shrink-0" />}

            <span className="truncate">{formatStage(job)}</span>

            {job.total_tracks > 0 && (
              <span className="ml-auto whitespace-nowrap">
                {job.completed_tracks} / {job.total_tracks} tracks
              </span>
            )}
          </div>
        </div>

        {isActive && (
          <button
            onClick={() => onCancel(job.id)}
            className="p-1.5 hover:bg-red-500/20 rounded transition"
            title="Cancel generation"
          >
            <X size={16} className="text-red-400" />
          </button>
        )}

        {(isCompleted || isFailed) && (
          <button
            onClick={() => onRemove(job.id)}
            className="p-1.5 hover:bg-white/10 rounded transition"
            title="Remove from list"
          >
            <X size={16} className="text-gray-400" />
          </button>
        )}
      </div>

      {job.total_tracks > 0 && (
        <div className="h-1.5 bg-white/10 rounded-full overflow-hidden">
          <motion.div
            className={`h-full ${
              isCompleted ? 'bg-green-500' :
              isFailed ? 'bg-red-500' :
              'bg-gradient-to-r from-purple-600 to-blue-600'
            }`}
            initial={{ width: 0 }}
            animate={{ width: `${progress}%` }}
            transition={{ duration: 0.3 }}
          />
        </div>
      )}

      {isFailed && job.error && (
        <div className="text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded px-2 py-1">
          {job.error}
        </div>
      )}
    </motion.div>
  )
}

export function GenerationQueuePanel() {
  const { jobs, isOpen, setIsOpen, cancelJob, removeJob, clearCompleted } = useGenerationQueue()

  const completedJobs = jobs.filter(j => j.status === 'completed' || j.status === 'failed')

  return (
    <AnimatePresence>
      {isOpen && (
        <motion.div
        initial={{ height: 0, opacity: 0 }}
        animate={{ height: 'auto', opacity: 1 }}
        exit={{ height: 0, opacity: 0 }}
        transition={TRANSITIONS.panel}
        className="w-full border-t border-white/10 shadow-2xl overflow-hidden"
      >
        <div className="max-w-screen-2xl mx-auto px-3 py-2 md:px-4 md:py-3">
          <div className="flex items-center justify-between mb-3 border-b border-white/10 pb-3">
            <h3 className="m-0 text-lg font-semibold text-white">Generation Queue</h3>
            <div className="flex items-center gap-2">
              {completedJobs.length > 0 && (
                <button
                  onClick={clearCompleted}
                  className="flex items-center gap-2 px-3 py-1.5 text-xs text-red-400 hover:text-red-300 hover:bg-red-500/10 rounded-lg transition"
                  title="Clear completed jobs"
                >
                  <Trash2 size={14} />
                  Clear Completed
                </button>
              )}
              <button
                onClick={() => setIsOpen(false)}
                className="bg-transparent border-none text-white/60 text-xl cursor-pointer p-1 leading-none transition-colors hover:text-white"
              >
                <X size={20} />
              </button>
            </div>
          </div>

          {jobs.length === 0 ? (
            <div className="p-8 text-center text-white/50 text-sm">
              No generation jobs
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2 pb-2">
              <AnimatePresence>
                {jobs.map(job => (
                  <JobItem
                    key={job.id}
                    job={job}
                    onCancel={cancelJob}
                    onRemove={removeJob}
                  />
                ))}
              </AnimatePresence>
            </div>
          )}
        </div>
      </motion.div>
      )}
    </AnimatePresence>
  )
}