import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'

const ROOT = new URL('../src', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1')
const CSS_TRANSITION_CLASSES = ['ui-press', 'ui-press-soft', 'ui-tap', 'ui-hover', 'ui-hover-lg', 'transition', 'transition-opacity', 'transition-transform', 'transition-all']
const ANIMATED = /\b(initial|animate|exit|whileHover|whileTap|whileInView|layout|variants)\b|\{\.\.\.\s*(PRESETS|VARIANTS|[A-Z_]+MOTION)/

function files(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) return files(path)
    return path.endsWith('.jsx') ? [path] : []
  })
}

function openingTag(source, start) {
  let depth = 0
  for (let i = start; i < source.length; i++) {
    const c = source[i]
    if (c === '{') depth++
    else if (c === '}') depth--
    else if (c === '>' && depth === 0) return source.slice(start, i)
  }
  return source.slice(start)
}

const problems = []
for (const file of files(ROOT)) {
  const source = readFileSync(file, 'utf8')
  for (const match of source.matchAll(/<motion\.(\w+)\b/g)) {
    const tag = openingTag(source, match.index)
    if (!ANIMATED.test(tag)) continue
    const classes = [...tag.matchAll(/className=\{?[`"']([^`"']*)/g)].flatMap(found => found[1].split(/\s+/))
    const clashing = CSS_TRANSITION_CLASSES.filter(name => classes.includes(name))
    if (clashing.length) {
      const line = source.slice(0, match.index).split('\n').length
      problems.push(`${relative(ROOT, file)}:${line} motion.${match[1]} carries ${clashing.join(', ')}`)
    }
  }
}

if (problems.length) {
  console.error('Animated elements must not carry CSS transition classes (they fight framer-motion and make fades stutter or pop). Put the class on an inner element:')
  for (const problem of problems) console.error(`  ${problem}`)
  process.exit(1)
}
console.log('Motion check passed')
