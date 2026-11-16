import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'

const ROOT = new URL('../src', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1')
const SHEET_HOME = join('components', 'PlayerSheet.jsx')

const RULES = [
  {
    test: /\p{Extended_Pictographic}/u,
    why: 'emoji used as an icon or label: use a lucide icon',
  },
  {
    test: /onMouse(Enter|Leave)=\{[^}]*\.style\b/,
    why: 'hover written by hand into element.style: use CSS hover (ui-row, ui-card, Tailwind hover:)',
  },
  {
    test: /(^|[\s`'"])hover:scale-/,
    why: 'card that grows on hover: interactive cards are OptionCard (ui-card)',
  },
  {
    test: /GLASS\.expandPanel\b/,
    why: 'pull-up panel built by hand: use PlayerSheet',
    except: (file) => file === SHEET_HOME,
  },
]

function files(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) return files(path)
    return path.endsWith('.jsx') ? [path] : []
  })
}

const problems = []
for (const path of files(join(ROOT, 'components'))) {
  const file = relative(ROOT, path)
  readFileSync(path, 'utf8').split('\n').forEach((line, index) => {
    for (const rule of RULES) {
      if (rule.except?.(file)) continue
      if (rule.test.test(line)) problems.push(`${file}:${index + 1} ${rule.why}`)
    }
  })
}

if (problems.length) {
  console.error('UI building blocks check failed:')
  for (const problem of problems) console.error(`  ${problem}`)
  process.exit(1)
}
console.log('UI blocks check passed')
