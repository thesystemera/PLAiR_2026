import { Component } from 'react'
import { reportClientEvent } from '../lib/errorReporter'

export class AppErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { failed: false }
  }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  componentDidCatch(error, info) {
    reportClientEvent('render_crash', `${error?.name || 'Error'}: ${error?.message || String(error)}`, {
      stack: `${error?.stack || ''}\n${info?.componentStack || ''}`,
    })
  }

  render() {
    if (!this.state.failed) return this.props.children
    return (
      <div className="fixed inset-0 flex flex-col items-center justify-center gap-4 bg-black text-white p-6 text-center" role="alert">
        <img src="/images/plair_icon_192.png" alt="" className="w-16 h-16 opacity-80" />
        <div className="text-lg font-semibold">Something went wrong</div>
        <div className="text-sm text-gray-400 max-w-xs">PLAiR hit a problem. Reloading usually fixes it, and your downloads and settings are safe.</div>
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="px-5 py-2 rounded-full bg-emerald-500 text-black font-semibold"
        >
          Reload PLAiR
        </button>
      </div>
    )
  }
}
