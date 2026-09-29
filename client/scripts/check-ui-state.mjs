import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('..', import.meta.url))
const srcDir = join(root, 'src')
const contextFile = join(srcDir, 'contexts', 'UIStateContext.jsx')

const NESTED = {
  engineState: { init: 'engineState', publish: 'reportEngineStatus' },
  audioState: { init: 'audioState', publish: 'publishAudioState' },
  queueState: { init: 'queueState', publish: 'publishQueueState' },
  authState: { init: 'authState', publish: 'publishAuthState' },
  radioState: { init: 'radioState', publish: 'publishRadioState' },
  downloadState: { init: 'downloadState', publish: 'publishDownloadState' },
  settingsState: { init: 'settingsState', publish: 'publishSettings' },
  interfaceState: { init: 'interfaceState', publish: 'reportInterfaceState' },
}

function sourceFiles(dir) {
  return readdirSync(dir).flatMap(name => {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) return sourceFiles(path)
    return /\.(jsx?|mjs)$/.test(name) ? [path] : []
  })
}

function balanced(text, openIndex) {
  const open = text[openIndex]
  const close = { '{': '}', '(': ')' }[open]
  let depth = 0
  for (let i = openIndex; i < text.length; i++) {
    if (text[i] === open) depth++
    else if (text[i] === close && --depth === 0) return text.slice(openIndex + 1, i)
  }
  return ''
}

function topLevelKeys(objectBody) {
  const keys = new Set()
  let depth = 0
  let token = ''
  for (let i = 0; i < objectBody.length; i++) {
    const ch = objectBody[i]
    if ('{[('.includes(ch)) depth++
    else if ('}])'.includes(ch)) depth--
    if (depth === 0 && (ch === ',' || i === objectBody.length - 1)) {
      const entry = (ch === ',' ? token : token + ch).trim()
      const match = entry.match(/^(?:\.\.\.)?([A-Za-z_$][\w$]*)\s*(?::|$|\()/)
      if (match && !entry.startsWith('...')) keys.add(match[1])
      token = ''
    } else {
      token += ch
    }
  }
  return keys
}

const context = readFileSync(contextFile, 'utf8')
const files = sourceFiles(srcDir)
const sources = files.map(path => ({ path, text: readFileSync(path, 'utf8') }))

const valueStart = context.indexOf('const value = useMemo(() => ({')
const valueKeys = topLevelKeys(balanced(context, context.indexOf('({', valueStart) + 1))

const nestedKeys = {}
for (const [field, { init, publish }] of Object.entries(NESTED)) {
  const keys = new Set()
  const initMatch = context.match(new RegExp(`const \\[${init}, set\\w+\\] = useState\\((?:\\(\\) => )?\\(?\\{`))
  if (initMatch) topLevelKeys(balanced(context, initMatch.index + initMatch[0].length - 1)).forEach(key => keys.add(key))
  for (const { text } of sources) {
    for (const call of text.matchAll(new RegExp(`\\b${publish}\\(\\s*\\{`, 'g'))) {
      topLevelKeys(balanced(text, call.index + call[0].length - 1)).forEach(key => keys.add(key))
    }
  }
  nestedKeys[field] = keys
}

const handledEngineKeys = new Set([...context.matchAll(/updates\.(\w+) !== undefined/g)].map(match => match[1]))

const problems = []
for (const { path, text } of sources) {
  const file = relative(root, path)
  for (const call of text.matchAll(/useUISelector\(\s*\(?\s*state\s*\)?\s*=>/g)) {
    const after = text.slice(call.index + call[0].length)
    const body = after.trimStart().startsWith('(')
      ? balanced(after, after.indexOf('('))
      : after.slice(0, after.indexOf(')'))
    for (const access of body.matchAll(/\bstate\.(\w+)(?:\.(\w+))?/g)) {
      const [, top, nested] = access
      const line = text.slice(0, call.index).split('\n').length
      if (!valueKeys.has(top)) problems.push(`${file}:${line} reads state.${top}, which UIState does not provide`)
      else if (nested && nestedKeys[top] && !nestedKeys[top].has(nested)) {
        problems.push(`${file}:${line} reads state.${top}.${nested}, which nothing ever sets`)
      }
    }
  }
  if (path === contextFile) continue
  for (const call of text.matchAll(/\breportEngineStatus\(\s*\{/g)) {
    const line = text.slice(0, call.index).split('\n').length
    for (const key of topLevelKeys(balanced(text, call.index + call[0].length - 1))) {
      if (!handledEngineKeys.has(key)) problems.push(`${file}:${line} reports engine key '${key}', which reportEngineStatus drops`)
    }
  }
}

if (problems.length) {
  console.error(problems.join('\n'))
  console.error(`\n${problems.length} UI state problem(s)`)
  process.exit(1)
}
console.log(`UI state check passed (${valueKeys.size} fields, ${files.length} files)`)
