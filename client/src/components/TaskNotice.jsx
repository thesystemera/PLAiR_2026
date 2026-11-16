import { useCallback, useMemo } from 'react'
import { useUISelector } from '../contexts/UIStateContext'
import { taskKind } from '../lib/tasks'
import { NoticeAction, NoticeClose } from './Notice'

const doneKey = (jobType) => `task-done-${jobType}`

export function useTaskNotices() {
  const { showNotice, hideNotice } = useUISelector(state => ({
    showNotice: state.showNotice,
    hideNotice: state.hideNotice,
  }))

  const taskDone = useCallback(({ jobType, text, actionLabel, onAction }) => {
    const kind = taskKind(jobType)
    const key = doneKey(jobType)
    showNotice({
      key,
      sticky: true,
      dismissible: false,
      tone: 'success',
      icon: kind.icon,
      iconColor: kind.color,
      text,
      content: (
        <>
          {onAction && (
            <NoticeAction onClick={() => { hideNotice(key); onAction() }}>{actionLabel}</NoticeAction>
          )}
          <NoticeClose onClick={() => hideNotice(key)} />
        </>
      ),
    })
  }, [showNotice, hideNotice])

  const taskFailed = useCallback(({ jobType, key, text }) => {
    showNotice({ key: key || `task-failed-${jobType}`, tone: 'error', icon: taskKind(jobType).icon, text })
  }, [showNotice])

  const taskNote = useCallback(({ jobType, key, text }) => {
    showNotice({ key: key || `task-note-${jobType}`, tone: 'info', icon: taskKind(jobType).icon, text })
  }, [showNotice])

  const hideTaskDone = useCallback((jobType) => hideNotice(doneKey(jobType)), [hideNotice])

  return useMemo(() => ({ taskDone, taskFailed, taskNote, hideTaskDone }), [taskDone, taskFailed, taskNote, hideTaskDone])
}
