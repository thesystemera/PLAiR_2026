import { Component } from 'react'
import { logger } from '../lib/logger'

export class VisualErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { hasError: false }
  }

  static getDerivedStateFromError() {
    return { hasError: true }
  }

  componentDidCatch(error, info) {
    logger.error(`[${this.props.name || 'VisualErrorBoundary'}] Render failed, using fallback:`, error, info?.componentStack)
  }

  render() {
    if (this.state.hasError) return this.props.fallback ?? null
    return this.props.children
  }
}
