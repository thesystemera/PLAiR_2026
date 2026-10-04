const work = new Map()
let watchers = 0
let gpuTimersSeen = false

export function frameStatsActive() {
  return watchers > 0
}

export function watchFrameStats() {
  watchers++
  return () => {
    watchers = Math.max(0, watchers - 1)
  }
}

export function addFrameWork(name, ms) {
  if (!watchers) return
  const entry = work.get(name)
  if (entry) {
    entry.ms += ms
    entry.count++
  } else {
    work.set(name, { ms, count: 1 })
  }
}

export function takeFrameWork() {
  const result = { gpuTimers: gpuTimersSeen }
  for (const [name, entry] of work) result[name] = { avg: entry.ms / entry.count, count: entry.count }
  work.clear()
  return result
}

export function createGpuTimer(gl, name) {
  const ext = gl?.getExtension?.('EXT_disjoint_timer_query_webgl2')
  if (!ext) {
    return { begin() {}, end() {}, poll() {}, dispose() {} }
  }
  gpuTimersSeen = true
  const pending = []
  let active = null
  return {
    begin() {
      if (!watchers || active || pending.length > 4) return
      active = gl.createQuery()
      gl.beginQuery(ext.TIME_ELAPSED_EXT, active)
    },
    end() {
      if (!active) return
      gl.endQuery(ext.TIME_ELAPSED_EXT)
      pending.push(active)
      active = null
    },
    poll() {
      if (!pending.length || !gl.getQueryParameter(pending[0], gl.QUERY_RESULT_AVAILABLE)) return
      const disjoint = gl.getParameter(ext.GPU_DISJOINT_EXT)
      while (pending.length && gl.getQueryParameter(pending[0], gl.QUERY_RESULT_AVAILABLE)) {
        const query = pending.shift()
        if (!disjoint) addFrameWork(name, gl.getQueryParameter(query, gl.QUERY_RESULT) / 1e6)
        gl.deleteQuery(query)
      }
    },
    dispose() {
      if (gl.isContextLost()) return
      if (active) gl.endQuery(ext.TIME_ELAPSED_EXT)
      for (const query of pending) gl.deleteQuery(query)
      if (active) gl.deleteQuery(active)
      pending.length = 0
      active = null
    },
  }
}
