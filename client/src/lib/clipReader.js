const START_IN_FLIGHT = 12
const MAX_IN_FLIGHT = 64
const OUTPUT_WAIT_MS = 100

function boxesIn(view, start, end) {
  const found = []
  let at = start
  while (at + 8 <= end) {
    let size = view.getUint32(at)
    const type = String.fromCharCode(view.getUint8(at + 4), view.getUint8(at + 5), view.getUint8(at + 6), view.getUint8(at + 7))
    let header = 8
    if (size === 1) {
      size = Number(view.getBigUint64(at + 8))
      header = 16
    } else if (size === 0) {
      size = end - at
    }
    if (size < header || at + size > end) break
    found.push({ type, start: at + header, end: at + size })
    at += size
  }
  return found
}

function child(view, box, type) {
  return boxesIn(view, box.start, box.end).find(b => b.type === type) || null
}

function path(view, box, ...types) {
  let current = box
  for (const type of types) {
    current = current && child(view, current, type)
  }
  return current
}

function handlerOf(view, trak) {
  const hdlr = path(view, trak, 'mdia', 'hdlr')
  if (!hdlr) return ''
  const at = hdlr.start + 8
  return String.fromCharCode(view.getUint8(at), view.getUint8(at + 1), view.getUint8(at + 2), view.getUint8(at + 3))
}

function readTimescale(view, mdhd) {
  const version = view.getUint8(mdhd.start)
  return view.getUint32(mdhd.start + (version === 1 ? 20 : 12))
}

function readMediaStart(view, trak) {
  const elst = path(view, trak, 'edts', 'elst')
  if (!elst) return 0
  const version = view.getUint8(elst.start)
  const count = view.getUint32(elst.start + 4)
  let at = elst.start + 8
  for (let i = 0; i < count; i++) {
    const mediaTime = version === 1 ? Number(view.getBigInt64(at + 8)) : view.getInt32(at + 4)
    if (mediaTime >= 0) return mediaTime
    at += version === 1 ? 20 : 12
  }
  return 0
}

function readTable(view, box, entrySize, read) {
  if (!box) return null
  const count = view.getUint32(box.start + 4)
  const out = new Array(count)
  for (let i = 0; i < count; i++) out[i] = read(box.start + 8 + i * entrySize)
  return out
}

function hex(byte) {
  return byte.toString(16).padStart(2, '0')
}

function demuxMp4(bytes) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
  const moov = boxesIn(view, 0, bytes.byteLength).find(b => b.type === 'moov')
  if (!moov) throw new Error('Clip has no movie header')
  const trak = boxesIn(view, moov.start, moov.end).find(b => b.type === 'trak' && handlerOf(view, b) === 'vide')
  if (!trak) throw new Error('Clip has no video track')

  const timescale = readTimescale(view, path(view, trak, 'mdia', 'mdhd'))
  const mediaStart = readMediaStart(view, trak)
  const stbl = path(view, trak, 'mdia', 'minf', 'stbl')
  const stsd = child(view, stbl, 'stsd')
  const entry = boxesIn(view, stsd.start + 8, stsd.end)[0]
  if (!entry || (entry.type !== 'avc1' && entry.type !== 'avc3')) throw new Error(`Clip codec ${entry?.type || 'unknown'} is not H.264`)
  const width = view.getUint16(entry.start + 24)
  const height = view.getUint16(entry.start + 26)
  const avcC = boxesIn(view, entry.start + 78, entry.end).find(b => b.type === 'avcC')
  if (!avcC) throw new Error('Clip has no decoder settings')
  const description = bytes.slice(avcC.start, avcC.end)
  const codec = `avc1.${hex(description[1])}${hex(description[2])}${hex(description[3])}`

  const stts = readTable(view, child(view, stbl, 'stts'), 8, at => [view.getUint32(at), view.getUint32(at + 4)])
  const ctts = readTable(view, child(view, stbl, 'ctts'), 8, at => [view.getUint32(at), view.getInt32(at + 4)])
  const stss = readTable(view, child(view, stbl, 'stss'), 4, at => view.getUint32(at))
  const stsc = readTable(view, child(view, stbl, 'stsc'), 12, at => [view.getUint32(at), view.getUint32(at + 4)])
  const stco = child(view, stbl, 'stco')
  const co64 = child(view, stbl, 'co64')
  const chunkOffsets = stco
    ? readTable(view, stco, 4, at => view.getUint32(at))
    : readTable(view, co64, 8, at => Number(view.getBigUint64(at)))
  const stsz = child(view, stbl, 'stsz')
  const fixedSize = view.getUint32(stsz.start + 4)
  const sampleCount = view.getUint32(stsz.start + 8)
  const sizes = new Array(sampleCount)
  for (let i = 0; i < sampleCount; i++) sizes[i] = fixedSize || view.getUint32(stsz.start + 12 + i * 4)

  const keys = stss ? new Set(stss) : null
  const samples = new Array(sampleCount)
  const toMicros = value => Math.round((value / timescale) * 1_000_000)
  let sample = 0
  let decodeTime = 0
  let sttsRun = 0
  let sttsLeft = stts[0]?.[0] ?? 0
  let cttsRun = 0
  let cttsLeft = ctts?.[0]?.[0] ?? 0
  for (let c = 0; c < chunkOffsets.length && sample < sampleCount; c++) {
    let run = 0
    while (run + 1 < stsc.length && stsc[run + 1][0] <= c + 1) run++
    let offset = chunkOffsets[c]
    for (let s = 0; s < stsc[run][1] && sample < sampleCount; s++) {
      while (sttsLeft === 0 && sttsRun + 1 < stts.length) sttsLeft = stts[++sttsRun][0]
      const delta = stts[sttsRun][1]
      let composition = 0
      if (ctts) {
        while (cttsLeft === 0 && cttsRun + 1 < ctts.length) cttsLeft = ctts[++cttsRun][0]
        composition = ctts[cttsRun][1]
        cttsLeft--
      }
      samples[sample] = {
        offset,
        size: sizes[sample],
        timestamp: toMicros(decodeTime + composition - mediaStart),
        duration: toMicros(delta),
        key: keys ? keys.has(sample + 1) : true,
      }
      offset += sizes[sample]
      decodeTime += delta
      sttsLeft--
      sample++
    }
  }

  const last = samples.reduce((latest, s) => Math.max(latest, s.timestamp + s.duration), 0)
  return { codec, description, width, height, samples, duration: last / 1_000_000 }
}

class ClipReader {
  constructor(bytes, track, config) {
    this.bytes = bytes
    this.track = track
    this.config = config
    this.decoder = null
    this.frames = []
    this.next = 0
    this.inFlight = 0
    this.allowance = START_IN_FLIGHT
    this.flushed = false
    this.flushing = false
    this.generation = 0
    this.error = null
    this.wake = null
    this.target = 0
    this.usedAt = -Infinity
    this.lastFrame = Math.max(0, ...track.samples.map(s => s.timestamp))
  }

  get duration() {
    return this.track.duration
  }

  open() {
    this.decoder = new VideoDecoder({
      output: frame => {
        this.frames.push(frame)
        this.inFlight = Math.max(0, this.inFlight - 1)
        this.trim()
        this.wake?.()
      },
      error: error => {
        this.error = error
        this.wake?.()
      },
    })
    this.decoder.configure(this.config)
  }

  trim() {
    while (this.frames.length > 1 && this.frames[1].timestamp <= this.target) this.frames.shift().close()
  }

  dropFrames() {
    for (const frame of this.frames) frame.close()
    this.frames = []
  }

  close() {
    this.generation++
    this.dropFrames()
    if (this.decoder && this.decoder.state !== 'closed') this.decoder.close()
    this.decoder = null
    this.flushed = false
    this.flushing = false
  }

  keyIndexFor(micros) {
    const { samples } = this.track
    let found = 0
    for (let i = 0; i < samples.length; i++) {
      if (samples[i].key && samples[i].timestamp <= micros) found = i
    }
    return found
  }

  startAt(micros) {
    if (this.decoder) {
      this.decoder.reset()
      this.decoder.configure(this.config)
    } else {
      this.open()
    }
    this.dropFrames()
    this.next = this.keyIndexFor(micros)
    this.inFlight = 0
    this.flushed = false
    this.flushing = false
    this.generation++
  }

  flush() {
    const generation = this.generation
    this.flushing = true
    this.decoder.flush().then(
      () => { if (generation === this.generation) this.flushed = true },
      error => { if (generation === this.generation) this.error = error },
    ).finally(() => this.wake?.())
  }

  feed() {
    const sample = this.track.samples[this.next++]
    this.decoder.decode(new EncodedVideoChunk({
      type: sample.key ? 'key' : 'delta',
      timestamp: sample.timestamp,
      duration: sample.duration,
      data: this.bytes.subarray(sample.offset, sample.offset + sample.size),
    }))
    this.inFlight++
  }

  output() {
    return new Promise(resolve => {
      const timer = setTimeout(() => {
        this.wake = null
        resolve(false)
      }, OUTPUT_WAIT_MS)
      this.wake = () => {
        clearTimeout(timer)
        this.wake = null
        resolve(true)
      }
    })
  }

  async frameAt(seconds) {
    const micros = Math.min(Math.max(0, Math.round(seconds * 1_000_000)), this.lastFrame)
    const shown = this.frames[0]
    if (!this.decoder || (shown && micros < shown.timestamp) || this.keyIndexFor(micros) > this.next) {
      this.startAt(micros)
    }
    this.target = micros

    for (;;) {
      if (this.error) throw this.error
      this.trim()
      const first = this.frames[0]
      if (first && (first.timestamp > micros || this.frames.length > 1 || this.flushed)) return first.clone()
      if (this.next < this.track.samples.length) {
        if (this.inFlight + this.frames.length < this.allowance) {
          this.feed()
          continue
        }
        if (!(await this.output()) && this.allowance < MAX_IN_FLIGHT) this.allowance += 4
      } else if (!this.flushing) {
        this.flush()
      } else if (!this.flushed) {
        await this.output()
      } else {
        return first ? first.clone() : null
      }
    }
  }
}

export async function openClip(url) {
  const response = await fetch(url)
  if (!response.ok) throw new Error(`Clip download failed (${response.status})`)
  const bytes = new Uint8Array(await response.arrayBuffer())
  const track = demuxMp4(bytes)
  const config = {
    codec: track.codec,
    codedWidth: track.width,
    codedHeight: track.height,
    description: track.description,
    optimizeForLatency: true,
  }
  const { supported } = await VideoDecoder.isConfigSupported(config)
  if (!supported) throw new Error(`This browser can't decode ${track.codec}`)
  return new ClipReader(bytes, track, config)
}
