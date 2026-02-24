import { Modal, ModalButton } from './Modal'
import { Sparkles } from 'lucide-react'
import { useDynamicTheme } from '../../contexts/DynamicThemeContext'
import { memo } from 'react'
import demoContent from '../../content/DEMO_MODE_INFO.md?raw'

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
      const content = line.slice(2).replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
      currentList.push(<span dangerouslySetInnerHTML={{ __html: content }} />)
    }
    else if (/^\d+\.\s/.test(line)) {
      if (listType !== 'ol') {
        flushList()
        listType = 'ol'
      }
      const content = line.replace(/^\d+\.\s/, '').replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
      currentList.push(<span dangerouslySetInnerHTML={{ __html: content }} />)
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
      let content = line
        .replace(/\*\*(.*?)\*\*/g, '<strong style="color: white; opacity: 0.95;">$1</strong>')
        .replace(/`(.*?)`/g, '<code style="background: rgba(255,255,255,0.1); padding: 2px 6px; border-radius: 4px; font-size: 0.9em;">$1</code>')

      elements.push(
        <p
          key={`p-${key++}`}
          className="text-sm leading-relaxed mb-3"
          style={{ color: getGrey400() }}
          dangerouslySetInnerHTML={{ __html: content }}
        />
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
          <span>Welcome to AI Radio</span>
        </div>
      }
      maxWidth="max-w-3xl"
      maxHeight="max-h-[90vh]"
      categoryOverride="discovery"
      gradientOpacity={0.75}
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