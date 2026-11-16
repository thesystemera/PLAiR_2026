import { Modal, ModalButton } from './Modal'
import { Sparkles } from 'lucide-react'
import { useDynamicTheme } from '../../contexts/DynamicThemeContext'
import { memo } from 'react'
import demoContent from '../../content/DEMO_MODE_INFO.md?raw'

const INLINE = /\*\*(.+?)\*\*|`(.+?)`|\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g

function inline(text) {
  const parts = []
  let last = 0
  for (const match of text.matchAll(INLINE)) {
    if (match.index > last) parts.push(text.slice(last, match.index))
    const [, bold, code, label, href] = match
    if (bold !== undefined) parts.push(<strong key={match.index} className="font-semibold text-white/95">{bold}</strong>)
    else if (code !== undefined) parts.push(<code key={match.index} className="rounded bg-white/10 px-1.5 py-0.5 text-[0.9em]">{code}</code>)
    else parts.push(<a key={match.index} href={href} target="_blank" rel="noopener noreferrer" className="text-white/90 underline decoration-white/30 underline-offset-2 transition-colors hover:decoration-white">{label}</a>)
    last = match.index + match[0].length
  }
  if (last < text.length) parts.push(text.slice(last))
  return parts
}

function parseMarkdownToJSX(markdown, getWhite, getGrey400) {
  const lines = markdown.split('\n')
  const elements = []
  let currentList = []
  let listType = null
  let key = 0

  const flushList = () => {
    if (currentList.length > 0) {
      const ListTag = listType === 'ul' ? 'ul' : 'ol'
      elements.push(
        <ListTag key={`list-${key++}`} className="space-y-2 mb-4 ml-4" style={{ color: getGrey400() }}>
          {currentList.map((item, idx) => (
            <li key={idx} className="text-sm leading-relaxed">{item}</li>
          ))}
        </ListTag>
      )
      currentList = []
      listType = null
    }
  }

  lines.forEach((line) => {
    // H1
    if (line.startsWith('# ')) {
      flushList()
      elements.push(
        <h1 key={`h1-${key++}`} className="text-3xl font-bold mb-4 mt-6" style={{ color: getWhite() }}>
          {line.slice(2)}
        </h1>
      )
    }
    else if (line.startsWith('## ')) {
      flushList()
      elements.push(
        <h2 key={`h2-${key++}`} className="text-xl font-bold mb-3 mt-5" style={{ color: getWhite(), opacity: 0.95 }}>
          {line.slice(3)}
        </h2>
      )
    }
    else if (line.startsWith('### ')) {
      flushList()
      elements.push(
        <h3 key={`h3-${key++}`} className="text-lg font-semibold mb-2 mt-4" style={{ color: getWhite(), opacity: 0.9 }}>
          {line.slice(4)}
        </h3>
      )
    }
    else if (line.startsWith('- ') || line.startsWith('* ')) {
      if (listType !== 'ul') {
        flushList()
        listType = 'ul'
      }
      currentList.push(inline(line.slice(2)))
    }
    else if (/^\d+\.\s/.test(line)) {
      if (listType !== 'ol') {
        flushList()
        listType = 'ol'
      }
      currentList.push(inline(line.replace(/^\d+\.\s/, '')))
    }
    else if (line.trim() === '---') {
      flushList()
      elements.push(
        <hr key={`hr-${key++}`} className="my-4 opacity-20" style={{ borderColor: getWhite() }} />
      )
    }
    else if (line.trim() === '') {
      flushList()
    }
    else if (line.trim()) {
      flushList()
      elements.push(
        <p key={`p-${key++}`} className="text-sm leading-relaxed mb-3" style={{ color: getGrey400() }}>
          {inline(line)}
        </p>
      )
    }
  })

  flushList()
  return elements
}

export const DemoModeModal = memo(function DemoModeModal({ isOpen, onClose }) {
  const { getWhite, getGrey400 } = useDynamicTheme()

  const content = parseMarkdownToJSX(demoContent, getWhite, getGrey400)

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={
        <div className="flex items-center gap-3">
          <Sparkles size={24} style={{ color: '#f59e0b' }} />
          <span>Welcome to PLAiR</span>
        </div>
      }
      maxWidth="max-w-3xl"
      maxHeight="max-h-[90vh]"
      categoryOverride="discovery"
    >
      <div className="space-y-2">
        {content}
      </div>

      <div className="mt-8 flex justify-center">
        <ModalButton onClick={onClose} variant="primary" className="px-8">
          Got it, let&apos;s explore!
        </ModalButton>
      </div>
    </Modal>
  )
})