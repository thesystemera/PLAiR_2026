import { logger } from '../lib/logger'
import { memo, useState, useEffect, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useWebSocketSubscribe } from '../contexts/WebSocketContext'
import { useAuth } from '../contexts/AuthContext'
import { useUISelector } from '../contexts/UIStateContext'
import { usePlaybackShoutout } from '../contexts/PlaybackShoutoutContext'
import { api } from '../lib/api'
import { messageMotion, TWEEN } from '../lib/motion'
import { Expandable, ExpandChevron, FadeSwap } from './Motion'
import { ActivityCard } from './DJActivity'

const USER_MESSAGE_MOTION = messageMotion(true)
const DJ_MESSAGE_MOTION = messageMotion(false)
import {
  MessageCircle,
  User,
  Bot,
  Terminal,
  AlertTriangle,
  Info,
  XCircle,
  Mic,
  MessageSquare,
  Brain,
  Radio,
  Globe,
  Heart,
  Ear
} from 'lucide-react'
import { useUISound } from '../hooks/useUISound'

const DJ_SPEAKERS = {
  jess: { name: 'Jess', color: 'blue' },
  leo: { name: 'Leo', color: 'pink' },
  computer: { name: 'Computer', color: 'green' }
}

const MESSAGE_TYPES = {
  BROADCAST: 'broadcast',
  TXT: 'txt',
  INTERNAL: 'internal',
  TASK: 'task'
}

function cleanMetadata(text) {
  if (!text) return text
  return text
    .replace(/\[HAL11000]/g, '')
    .trim()
}

function parseMessage(message) {
  if (!message) return []

  const parts = message.split(/(\[BROADCAST]|\[TXT]|\[JESS]|\[LEO]|\[INTERNAL DIALOGUE]|\[TASK])/g)
  const result = []

  let currentType = MESSAGE_TYPES.BROADCAST
  let currentSpeaker = null
  let contentBuffer = ''

  const pushCurrentContent = () => {
    const cleanedContent = cleanMetadata(contentBuffer.trim())
    if (cleanedContent) {
      let finalSpeaker = currentSpeaker || 'computer'
      let finalType = currentType

      if (finalType !== MESSAGE_TYPES.INTERNAL && finalType !== MESSAGE_TYPES.TASK) {
        if (!currentSpeaker) {
          finalSpeaker = 'computer'
          finalType = MESSAGE_TYPES.TXT
        }
      }

      const messageData = {
        type: finalType,
        speaker: finalSpeaker,
        content: cleanedContent
      }

      result.push(messageData)
    }
    contentBuffer = ''
  }

  for (const part of parts) {
    if (!part || !part.trim()) continue

    const isDelimiter = part.startsWith('[') && part.endsWith(']')

    if (isDelimiter) {
      pushCurrentContent()
      currentSpeaker = null

      const tag = part.replace(/[[\]]/g, '')

      if (tag === 'BROADCAST') {
        currentType = MESSAGE_TYPES.BROADCAST
      } else if (tag === 'TXT') {
        currentType = MESSAGE_TYPES.TXT
      } else if (tag === 'INTERNAL DIALOGUE') {
        currentType = MESSAGE_TYPES.INTERNAL
      } else if (tag === 'TASK') {
        currentType = MESSAGE_TYPES.TASK
      } else if (tag === 'JESS' || tag === 'LEO') {
        currentSpeaker = tag.toLowerCase()
      } else {
        contentBuffer += part
      }
    } else {
      contentBuffer += part
    }
  }

  pushCurrentContent()

  const mergedResult = []
  for (const item of result) {
    const isSameContext = mergedResult.length > 0 &&
                         mergedResult[mergedResult.length - 1].type === item.type &&
                         mergedResult[mergedResult.length - 1].speaker === item.speaker

    if (isSameContext && item.type !== MESSAGE_TYPES.INTERNAL) {
      mergedResult[mergedResult.length - 1].content += ' ' + item.content
    } else if (item.content.trim()) {
      if (item.type === MESSAGE_TYPES.INTERNAL) {
          const internalParts = item.content.split(/(\[JESS]|\[LEO])\s*/g)

          let internalSpeaker = item.speaker || 'computer';
          let currentInternalContent = '';

          for (let i = 0; i < internalParts.length; i++) {
              const part = internalParts[i].trim();
              if (!part) continue;

              if (part === '[JESS]' || part === '[LEO]') {
                  if (currentInternalContent) {
                       mergedResult.push({
                           type: MESSAGE_TYPES.INTERNAL,
                           speaker: internalSpeaker,
                           content: currentInternalContent
                       })
                  }
                  internalSpeaker = part.replace(/[[\]]/g, '').toLowerCase()
                  currentInternalContent = '';
              } else {
                  currentInternalContent += part
              }
          }
          if (currentInternalContent) {
              mergedResult.push({
                  type: MESSAGE_TYPES.INTERNAL,
                  speaker: internalSpeaker,
                  content: currentInternalContent
              })
          }
      } else {
          mergedResult.push(item)
      }
    }
  }

  const finalCleanedResult = []
  for (const item of mergedResult) {
      const prevItem = finalCleanedResult[finalCleanedResult.length - 1]
      if (prevItem && prevItem.type === item.type && prevItem.speaker === item.speaker && item.speaker !== 'computer') {
           prevItem.content += ' ' + item.content
      } else {
           finalCleanedResult.push(item)
      }
  }

  if (finalCleanedResult.length === 0 && message.trim()) {
    finalCleanedResult.push({ type: 'bot', content: cleanMetadata(message.trim()), speaker: 'computer' })
  }

  return finalCleanedResult.filter(item => item.content.length > 0)
}

const DJ_COLORS = {
  blue: {
    broadcast: 'bg-blue-500/10 border-blue-500/30 text-blue-300',
    txt: 'bg-blue-400/10 border-blue-400/30 text-blue-200'
  },
  pink: {
    broadcast: 'bg-pink-500/10 border-pink-500/30 text-pink-300',
    txt: 'bg-pink-400/10 border-pink-400/30 text-pink-200'
  },
  green: {
    broadcast: 'bg-green-500/10 border-green-500/30 text-green-300',
    txt: 'bg-green-400/10 border-green-400/30 text-green-200'
  }
}

const COMMAND_SOURCES = [
  { tag: '[STUDIO TOOLS]', source: 'tool' },
  { tag: '[HAL11000]', source: 'hal11000' },
]

const InternalDialogueBubble = memo(function InternalDialogueBubble({ speaker, content }) {
  const [open, setOpen] = useState(false)
  const name = DJ_SPEAKERS[speaker]?.name

  return (
    <motion.div
      layout
      transition={TWEEN.layout}
      onClick={() => setOpen(value => !value)}
      role="button"
      aria-expanded={open}
      className={`border bg-gray-500/10 border-gray-500/30 text-gray-400 max-w-[80%] w-fit overflow-hidden cursor-pointer ${open ? 'p-3 rounded-lg' : 'px-3 py-1 rounded-full'}`}
    >
      <motion.div layout="position" className="flex items-center gap-2 text-xs">
        <Brain className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
        <span className="font-medium">Internal dialogue{name ? ` · ${name}` : ''}</span>
        <ExpandChevron open={open} size={14} />
      </motion.div>
      <Expandable open={open}>
        <p className="mt-2 text-sm italic whitespace-pre-wrap break-words">{content}</p>
      </Expandable>
    </motion.div>
  )
})

function commandEntries(raw, extra) {
  if (typeof raw !== 'string' || !raw.trim()) return []
  const pattern = /(\[STUDIO TOOLS]|\[HAL11000])/g
  const pieces = raw.split(pattern)
  const entries = []
  let source = 'hal11000'
  for (const piece of pieces) {
    const known = COMMAND_SOURCES.find(item => item.tag === piece)
    if (known) {
      source = known.source
    } else if (piece.trim()) {
      entries.push({ ...extra, type: 'command', source, content: piece.trim() })
    }
  }
  return entries
}

const getCategoryIcon = (messageType) => {
  if (!messageType) return <MessageCircle className="w-4 h-4" />

  if (messageType === 'announcer') return <Radio className="w-4 h-4" />
  if (messageType === 'shoutouts') return <Heart className="w-4 h-4" />
  if (messageType === 'onboarding') return <MessageCircle className="w-4 h-4" />

  const externalTypes = ['biography', 'lyrics', 'weather', 'news', 'events', 'location_search']
  if (externalTypes.includes(messageType)) return <Globe className="w-4 h-4" />

  return <MessageCircle className="w-4 h-4" />
}

export function Conversation({ isOpen, messageFilter = 'all', onFilterCounts, shouldAutoScroll = false }) {
  const [conversations, setConversations] = useState([])
  const [loading, setLoading] = useState(false)
  const chatEndRef = useRef(null)
  const prevConversationLengthRef = useRef(0)
  const scrollTimeoutRef = useRef(null)
  const loadingRef = useRef(false)
  const liveTurnsRef = useRef(new Set())
  const sayCountRef = useRef(0)
  const uiSound = useUISound(window.audioEngine)
  const { token } = useAuth()
  const { toastError, radioInput } = useUISelector(state => ({ toastError: state.toastError, radioInput: state.interfaceState.radioInput }))
  const { playShoutout } = usePlaybackShoutout()

  const formatTimestamp = (ts) => {
    if (!ts) return ''
    const date = new Date(ts)
    return date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })
  }

  const handleShoutoutClick = async (audioUrl) => {
    try {
      const match = audioUrl.match(/\/api\/user_content\/shoutouts\/audio\/(\d+)\/(.+)\.mp3/)
      if (!match) {
        toastError('Invalid shoutout URL')
        return
      }

      const userId = match[1]
      const timestamp = match[2]
      const shoutoutId = `${userId}_${timestamp}`

      const shoutout = await api.getShoutout(shoutoutId)

      if (!shoutout) {
        toastError('Shoutout not found')
        return
      }

      playShoutout(shoutout)
    } catch (error) {
      logger.error('Failed to load shoutout:', error)
      toastError('Failed to load shoutout')
    }
  }

  const parseShoutoutLinks = (content) => {
    if (typeof content !== 'string') return content

    const shoutoutUrlPattern = /\$(\/api\/user_content\/shoutouts\/audio\/[^$]+)\$/g

    const parts = []
    let lastIndex = 0
    let match

    while ((match = shoutoutUrlPattern.exec(content)) !== null) {
      if (match.index > lastIndex) {
        parts.push(content.substring(lastIndex, match.index))
      }

      const url = match[1]
      parts.push(
        <button
          key={match.index}
          onClick={() => handleShoutoutClick(url)}
          className="text-blue-400 hover:text-blue-300 underline cursor-pointer transition-colors"
        >
          [Play Shoutout]
        </button>
      )

      lastIndex = match.index + match[0].length
    }

    if (lastIndex < content.length) {
      parts.push(content.substring(lastIndex))
    }

    return parts.length > 0 ? parts : content
  }

  const renderDJMessage = ({ type, speaker, content, timestamp, messageType, filler, key }) => {
    let displayContent = content;
    if (typeof displayContent === 'string') {
        displayContent = displayContent.replace(/\s{2,}/g, ' ').trim();
    }

    if (type === MESSAGE_TYPES.INTERNAL) {
      return <InternalDialogueBubble key={key} speaker={speaker} content={displayContent} />
    }

    if (type === MESSAGE_TYPES.TASK) {
      const text = String(displayContent)
      const complete = /\b(complete|completed|done)\b/i.test(text) && !/\b(partial|partly|incomplete)\b/i.test(text)
      const summary = text.replace(/^\s*(status\s*[:-]\s*)?(complete|completed|done|partial|partly done|incomplete)\b[\s.;:,|-]*(action\s*[:-]\s*)?/i, '')
      return (
        <ActivityCard
          key={key}
          call={{
            source: 'review',
            tool: 'review',
            label: complete ? 'Task complete' : 'Task partly done',
            summary: summary || text,
            state: complete ? 'done' : 'empty'
          }}
        />
      )
    }

    const getMessageStyle = () => {
      if (type === MESSAGE_TYPES.INTERNAL) {
        return 'bg-gray-500/10 border-gray-500/30 text-gray-400 italic'
      }

      if (speaker === 'computer') {
          const colorScheme = DJ_COLORS.green
          return type === MESSAGE_TYPES.BROADCAST ? colorScheme.broadcast : colorScheme.txt
      }

      if (!speaker) {
        return 'bg-purple-500/10 border-purple-500/30 text-purple-300'
      }

      const speakerData = DJ_SPEAKERS[speaker]
      if (!speakerData) {
        return 'bg-purple-500/10 border-purple-500/30 text-purple-300'
      }

      const colorScheme = DJ_COLORS[speakerData.color]
      return type === MESSAGE_TYPES.BROADCAST ? colorScheme.broadcast : colorScheme.txt
    }

    const getFormatIcon = () => {
      if (filler) return <Ear className="w-4 h-4" />
      if (type === MESSAGE_TYPES.INTERNAL) return <Brain className="w-4 h-4" />
      if (type === MESSAGE_TYPES.BROADCAST) return <Mic className="w-4 h-4" />
      return <MessageSquare className="w-4 h-4" />
    }

    const getLabel = () => {
      if (type === MESSAGE_TYPES.INTERNAL) return 'Internal Dialogue'
      if (speaker) {
        const speakerData = DJ_SPEAKERS[speaker]
        const name = speakerData?.name || speaker
        return filler ? `${name} · off mic` : name
      }
      return type === MESSAGE_TYPES.BROADCAST ? 'Broadcast' : 'Message'
    }

    return (
      <div key={key} className={`rounded-lg border ${getMessageStyle()} max-w-[80%] w-fit ${filler ? 'px-3 py-2 border-dashed opacity-70 italic' : 'p-3'}`}>
        <div className="flex items-start gap-2">
          <div className="flex items-center gap-1.5 mt-0.5">
            {getFormatIcon()}
            {getCategoryIcon(messageType)}
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center justify-between gap-2 mb-1">
              <span className="text-xs font-medium tracking-wide opacity-70">
                {getLabel()}
              </span>
              {timestamp && (
                <span className="text-xs opacity-50">
                  {formatTimestamp(timestamp)}
                </span>
              )}
            </div>
            <p className="text-sm whitespace-pre-wrap break-words">
              {parseShoutoutLinks(displayContent)}
            </p>
          </div>
        </div>
      </div>
    )
  }

  useWebSocketSubscribe('conversation_cleared', () => {
    setConversations([])
  })

  useWebSocketSubscribe('transcription_complete', (data) => {
    if (data.text) {
      setConversations(prev => {
        const betterIndex = prev.findIndex(c =>
          c.type === 'user' && c.awaitingFast && Date.now() - new Date(c.timestamp) < 30000)
        if (betterIndex !== -1) {
          const updated = [...prev]
          updated[betterIndex] = { ...updated[betterIndex], awaitingFast: false }
          return updated
        }
        return [...prev, {
          type: 'user',
          content: data.text,
          timestamp: new Date().toISOString(),
          isFastTranscription: true,
          inputMethod: 'voice'
        }]
      })
      uiSound.playTranscript()
    }
  })

  useEffect(() => {
    if (isOpen && token) {
      void loadConversations()
    }
  }, [isOpen, token])

  const loadConversations = async () => {
    if (loadingRef.current) {
      logger.info('[Conversation] Already loading, skipping duplicate call')
      return
    }

    try {
      loadingRef.current = true
      setLoading(true)
      const data = await api.getConversationHistory()
      const rawConversations = data.conversations || []
      const processedConversations = []

      for (const conv of rawConversations) {
        if (conv.type === 'bot' && typeof conv.content === 'string') {
          const parsedMessages = parseMessage(conv.content)

          for (let i = 0; i < parsedMessages.length; i++) {
            const msg = parsedMessages[i]
            processedConversations.push({
              type: 'bot',
              content: msg.content,
              timestamp: i === 0 ? conv.timestamp : conv.timestamp,
              messageType: conv.messageType,
              djMessageType: msg.type,
              djSpeaker: msg.speaker
            })
          }
        } else if (conv.type === 'command' && typeof conv.content === 'string') {
          processedConversations.push(...commandEntries(conv.content, { timestamp: conv.timestamp, messageType: conv.messageType }))
        } else if (conv.type === 'user' && typeof conv.content === 'string') {
           processedConversations.push({
            ...conv,
            content: conv.content.replace(/\[LISTENER TXT]\s*/, '').trim(),
            messageType: conv.messageType,
            inputMethod: conv.inputMethod || 'voice'
          })
        } else {
          processedConversations.push(conv)
        }
      }

      setConversations(processedConversations)
      logger.info(`[Conversation] Loaded ${processedConversations.length} messages`)
    } catch (error) {
      logger.error('Failed to load conversations:', error)
    } finally {
      setLoading(false)
      loadingRef.current = false
    }
  }

  const upsertUser = (text, messageType, inputMethod) => {
    const cleaned = text.replace(/\[LISTENER TXT]\s*/, '').trim()
    if (!cleaned) return
    setConversations(prev => {
      const fastIndex = prev.findIndex(c =>
        c.type === 'user' &&
        c.isFastTranscription === true &&
        Math.abs(new Date(c.timestamp) - new Date()) < 30000
      )
      if (fastIndex !== -1) {
        const updated = [...prev]
        updated[fastIndex] = {
          ...updated[fastIndex],
          content: cleaned,
          isFastTranscription: false,
          messageType,
        }
        return updated
      }
      return [...prev, {
        type: 'user',
        content: cleaned,
        timestamp: new Date().toISOString(),
        isFastTranscription: false,
        awaitingFast: (inputMethod || 'voice') === 'voice',
        messageType,
        inputMethod: inputMethod || 'voice'
      }]
    })
  }

  const botEntries = (content, messageType, idPrefix) => {
    const parsed = typeof content === 'string' ? parseMessage(content) : []
    const timestamp = new Date().toISOString()
    if (parsed.length === 0) {
      return [{ id: idPrefix && `${idPrefix}:0`, type: 'bot', content, timestamp, messageType }]
    }
    return parsed.map((msg, idx) => ({
      id: idPrefix && `${idPrefix}:${idx}`,
      type: 'bot',
      content: msg.content,
      timestamp,
      messageType,
      djMessageType: msg.type,
      djSpeaker: msg.speaker
    }))
  }

  const playBotSound = (content) => {
    if (content.includes('[BROADCAST]')) uiSound.playBroadcast()
    else if (content.includes('[TXT]')) uiSound.playTxt()
    else uiSound.playBot()
  }

  useWebSocketSubscribe('dj_activity', (data) => {
    if (!data?.phase || !data.turn_id) return
    const turnId = data.turn_id
    if (data.phase === 'turn') {
      liveTurnsRef.current.add(turnId)
      if (data.input) upsertUser(data.input, 'interactive', data.origin === 'text' ? 'text' : 'voice')
      return
    }
    if (!liveTurnsRef.current.has(turnId)) return
    if (data.phase === 'say' && data.text) {
      sayCountRef.current += 1
      const entries = botEntries(data.text, 'interactive', `${turnId}:say${sayCountRef.current}`)
        .map(entry => (data.kind ? { ...entry, filler: data.kind } : entry))
      setConversations(prev => [...prev, ...entries])
      playBotSound(data.text)
    } else if (data.phase === 'start' && data.call_id) {
      const entry = {
        id: data.call_id,
        turnId,
        type: 'activity',
        source: data.source || 'tool',
        tool: data.tool,
        kinds: data.kinds || [],
        cost: data.cost || '',
        label: data.label || data.tool,
        command: data.command || '',
        state: 'running',
        summary: '',
        live: false,
        startedAt: Date.now(),
        timestamp: new Date().toISOString(),
        messageType: 'interactive'
      }
      setConversations(prev => prev.some(c => c.id === entry.id)
        ? prev.map(c => c.id === entry.id ? { ...c, ...entry } : c)
        : [...prev, entry])
      uiSound.playCommand()
    } else if (data.phase === 'result' && data.call_id) {
      setConversations(prev => prev.map(c => c.id === data.call_id
        ? { ...c, state: data.outcome || 'done', summary: data.summary || '', live: !!data.live, finishedAt: Date.now() }
        : c))
    } else if (data.phase === 'done') {
      setConversations(prev => prev.map(c => c.turnId === turnId && c.state === 'running'
        ? { ...c, state: 'done', finishedAt: Date.now() }
        : c))
    }
  })

  useWebSocketSubscribe('conversation_update', (data) => {
    const newMessages = []
    const messageType = data.message_type || 'interactive'
    const streamed = !!data.turn_id && liveTurnsRef.current.has(data.turn_id)

    if (data.user_input && !streamed) {
      upsertUser(data.user_input, messageType, data.input_method)
    }

    if (data.bot_response && !streamed) {
      newMessages.push(...botEntries(data.bot_response, messageType))
      playBotSound(data.bot_response)
    }

    if (data.commands && !streamed) {
      newMessages.push(...commandEntries(data.commands, { timestamp: new Date().toISOString(), messageType }))
      uiSound.playCommand()
    }

    if (data.warning) {
      newMessages.push({
        type: 'warning',
        content: data.warning,
        timestamp: new Date().toISOString(),
        messageType: messageType
      })
      uiSound.playWarning()
    }

    if (data.error) {
      newMessages.push({
        type: 'error',
        content: data.error,
        timestamp: new Date().toISOString(),
        messageType: messageType
      })
      uiSound.playError()
    }

    if (data.info) {
      newMessages.push({
        type: 'info',
        content: data.info,
        timestamp: new Date().toISOString(),
        messageType: messageType
      })
      uiSound.playInfo()
    }

    if (newMessages.length > 0) {
      setConversations(prev => [...prev, ...newMessages])
    }
  })

  const renderMessage = (conv, idx) => {
    if (conv.type === 'user') {
      const inputMethod = conv.inputMethod || 'voice'
      const inputIcon = inputMethod === 'voice' ? <Mic className="w-4 h-4" /> : <MessageSquare className="w-4 h-4" />

      return (
        <div className={`p-3 rounded-lg border ${getMessageColors('user')} max-w-[80%] ml-auto w-fit`}>
          <div className="flex items-start gap-2 flex-row-reverse">
            <div className="flex items-center gap-1.5 mt-0.5">
              {inputIcon}
              <User className="w-4 h-4" />
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center justify-between gap-2 mb-1 flex-row-reverse">
                <span className="text-xs font-medium uppercase tracking-wide opacity-70">
                  YOU
                </span>
                <span className="text-xs opacity-50">{formatTimestamp(conv.timestamp)}</span>
              </div>
              <p className="text-sm whitespace-pre-wrap break-words text-right">{parseShoutoutLinks(conv.content)}</p>
            </div>
          </div>
        </div>
      )
    }

    if (conv.type === 'activity') {
      return <ActivityCard call={conv} />
    }

    if (conv.type === 'command') {
      const lines = conv.content.split('\n').filter(line => line.trim())
      return (
        <ActivityCard
          call={{
            source: conv.source || 'hal11000',
            tool: conv.source === 'tool' ? 'tool' : 'hal11000',
            label: conv.source === 'tool' ? 'Actions and lookups' : 'Commands sent',
            summary: `${lines.length} command${lines.length === 1 ? '' : 's'}`,
            command: lines.join('\n'),
            state: 'done'
          }}
        />
      )
    }

    if (conv.type === 'bot') {
      return renderDJMessage({
        key: `${idx}`,
        type: conv.djMessageType,
        speaker: conv.djSpeaker,
        content: conv.content,
        timestamp: conv.timestamp,
        messageType: conv.messageType,
        filler: conv.filler
      })
    }

    const colors = getMessageColors(conv.type)

    const displayContent = String(conv.content ?? '').replace(/\s{2,}/g, ' ').trim()

    return (
      <div className={`border ${colors} max-w-[80%] w-fit ${['info', 'warning', 'error'].includes(conv.type) ? 'mx-auto px-3 py-1.5 rounded-full' : 'p-3 rounded-lg'}`}>
        {['info', 'warning', 'error'].includes(conv.type) ? (
          <div className="flex items-center gap-2 text-xs">
            <span className="shrink-0 opacity-80">{getMessageIcon(conv.type)}</span>
            <span className="break-words">{parseShoutoutLinks(displayContent)}</span>
          </div>
        ) : (
          <div className={`flex items-start gap-2`}>
            <div className="flex-1 min-w-0">
              <div className={`flex items-center gap-2 mb-1 justify-between`}>
                <span className="flex items-center gap-2 text-xs font-medium uppercase tracking-wide opacity-70">
                  {getMessageIcon(conv.type)}
                  {conv.type}
                </span>
                <span className="text-xs opacity-50">{formatTimestamp(conv.timestamp)}</span>
              </div>
              <p className="text-sm whitespace-pre-wrap break-words">{parseShoutoutLinks(displayContent)}</p>
            </div>
          </div>
        )}
      </div>
    )
  }

  const getMessageIcon = (type) => {
    switch (type) {
      case 'user': return <User className="w-4 h-4" />
      case 'bot': return <Bot className="w-4 h-4" />
      case 'command': return <Terminal className="w-4 h-4" />
      case 'warning': return <AlertTriangle className="w-4 h-4" />
      case 'error': return <XCircle className="w-4 h-4" />
      case 'info': return <Info className="w-4 h-4" />
      default: return <MessageCircle className="w-4 h-4" />
    }
  }

  const getMessageColors = (type) => {
    switch (type) {
      case 'user': return 'bg-amber-500/10 border-amber-500/30 text-amber-300'
      case 'bot': return 'bg-purple-500/10 border-purple-500/30 text-purple-300'
      case 'command': return 'bg-green-500/10 border-green-500/30 text-green-300'
      case 'warning': return 'bg-yellow-500/10 border-yellow-500/30 text-yellow-300'
      case 'error': return 'bg-red-500/10 border-red-500/30 text-red-300'
      case 'info': return 'bg-white/10 border-white/30 text-white'
      default: return 'bg-gray-500/10 border-gray-500/30 text-gray-300'
    }
  }

  const getFilterCategory = (conv) => {
    if (conv.type === 'info' || conv.type === 'warning' || conv.type === 'error') {
      return 'system'
    }

    const messageType = conv.messageType
    if (!messageType) return 'interactive'

    const externalTypes = ['biography', 'lyrics', 'weather', 'news', 'events', 'location_search']
    if (externalTypes.includes(messageType)) return 'external'

    return messageType
  }

  const filterConversations = (convs) => {
    if (messageFilter === 'all') return convs

    return convs.filter(conv => {
      const category = getFilterCategory(conv)
      if (messageFilter === 'interactive') {
        return category === 'interactive' || category === 'onboarding'
      }
      return category === messageFilter
    })
  }

  const filteredConversations = filterConversations(conversations)

  useEffect(() => {
    if (!onFilterCounts) return

    const counts = {
      all: conversations.length,
      interactive: 0,
      announcer: 0,
      external: 0,
      shoutouts: 0,
      system: 0
    }

    conversations.forEach(conv => {
      const category = getFilterCategory(conv)
      if (category === 'interactive' || category === 'onboarding') {
        counts.interactive++
      } else if (category === 'announcer') {
        counts.announcer++
      } else if (category === 'external') {
        counts.external++
      } else if (category === 'shoutouts') {
        counts.shoutouts++
      } else if (category === 'system') {
        counts.system++
      }
    })

    onFilterCounts(counts)
  }, [conversations, onFilterCounts])

  useEffect(() => {
    if (
      shouldAutoScroll &&
      conversations.length > 0 &&
      conversations.length > prevConversationLengthRef.current &&
      chatEndRef.current
    ) {
      if (scrollTimeoutRef.current) {
        clearTimeout(scrollTimeoutRef.current)
      }

      scrollTimeoutRef.current = setTimeout(() => {
        requestAnimationFrame(() => {
          if (chatEndRef.current) {
            chatEndRef.current.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
          }
        })
      }, 100)
    }

    prevConversationLengthRef.current = conversations.length
  }, [conversations.length, shouldAutoScroll])

  useEffect(() => {
    return () => {
      if (scrollTimeoutRef.current) {
        clearTimeout(scrollTimeoutRef.current)
      }
    }
  }, [])

  if (!isOpen) return null

  const contentState = loading ? 'loading' : filteredConversations.length === 0 ? 'empty' : `messages-${messageFilter}`

  return (
    <div className="w-full relative pb-3">
      <FadeSwap swapKey={contentState} className="space-y-3">
        {loading ? (
          <div className="flex items-center justify-center py-12">
            <div className="text-gray-400">Loading conversation...</div>
          </div>
        ) : filteredConversations.length === 0 ? (
          <div className={radioInput === 'text'
            ? 'w-full min-h-[55vh] flex items-center justify-center'
            : 'w-full flex justify-center pt-16'}>
            <div className="text-center text-gray-400 max-w-xs mx-auto px-4">
              <MessageCircle className="w-8 h-8 sm:w-10 sm:h-10 mx-auto mb-2 opacity-30" />
              {conversations.length === 0 ? (
                <>
                  <p className="text-xs sm:text-sm font-medium">No conversations yet</p>
                  <p className="text-[10px] sm:text-xs mt-1 opacity-70">Start talking with the DJs!</p>
                </>
              ) : (
                <>
                  <p className="text-xs sm:text-sm font-medium">No {messageFilter} messages</p>
                  <p className="text-[10px] sm:text-xs mt-1 opacity-70">Try selecting a different filter</p>
                </>
              )}
            </div>
          </div>
        ) : (
          <AnimatePresence initial={false}>
            {filteredConversations.map((conv, idx) => (
              <motion.div
                key={conv.id || idx}
                {...(conv.type === 'user' ? USER_MESSAGE_MOTION : DJ_MESSAGE_MOTION)}
              >
                {renderMessage(conv, idx)}
              </motion.div>
            ))}
            <div ref={chatEndRef} />
          </AnimatePresence>
        )}
      </FadeSwap>
    </div>
  )
}