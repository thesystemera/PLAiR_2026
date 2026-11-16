import { Clapperboard, Sparkles, Upload } from 'lucide-react'

const TASK_KINDS = {
  generation: {
    label: 'New songs',
    badge: 'Generating',
    fallbackTitle: 'Generating tracks...',
    cancelTitle: 'Cancel generation',
    icon: Sparkles,
    color: '#c084fc',
  },
  share_video: {
    label: 'Share video',
    badge: 'Video',
    fallbackTitle: 'Share video',
    cancelTitle: 'Cancel video',
    icon: Clapperboard,
    color: '#38bdf8',
  },
  upload: {
    label: 'Upload',
    badge: 'Upload',
    fallbackTitle: 'Your track',
    cancelTitle: 'Cancel upload',
    icon: Upload,
    color: '#34d399',
  },
}

export const jobTypeOf = (job) => job.job_type || 'generation'

export const taskKind = (jobType) => TASK_KINDS[jobType] || TASK_KINDS.generation

export const isActiveTask = (job) => job.status === 'processing'

export function taskProgress(jobs, jobType) {
  const active = jobs.filter(job => jobTypeOf(job) === jobType && isActiveTask(job))
  if (active.length === 0) return { active: false, progress: null }
  const known = active.filter(job => job.progress_percent > 0)
  if (known.length === 0) return { active: true, progress: null }
  const percent = known.reduce((sum, job) => sum + job.progress_percent, 0) / known.length
  return { active: true, progress: Math.min(1, percent / 100) }
}
