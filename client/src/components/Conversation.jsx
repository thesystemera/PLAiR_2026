import { logger } from '../lib/logger'
import { useState, useEffect, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useWebSocketSubscribe } from '../contexts/WebSocketContext'
import { useAuth } from '../contexts/AuthContext'
import { useUIState } from '../contexts/UIStateContext'
import { usePlaybackShoutout } from '../contexts/PlaybackShoutoutContext'
import { api } from '../lib/api'
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
  Heart
} from 'lucide-react'
import { useUISound } from '../hooks/useUISound'

const DJ_SPEAKERS = {
  terry: { name: 'Terry', color: 'blue' },
  shaquille: { name: 'Shaquille', color: 'pink' },
  computer: { name: 'Computer', color: 'green' }
}

const MESSAGE_TYPES = {
  BROADCAST: 'broadcast',
  TXT: 'txt',
  INTERNAL: 'internal'
}

function cleanMetadata(text) {
  if (!text) return text
  return text
    .replace(/\*.*?\*|%.*?%|@.*?@|&.*?&/g, '')
    .replace(/\[HAL11000]/g, '')
    .trim()
}

function parseMessage(message) {
  if (!message) return []

  const parts = message.split(/(\[BROADCAST]|\[TXT]|\[TERRY]|\[SHAQUILLE]|\[INTERNAL DIALOGUE])/g)
  const result = []

  let currentType = MESSAGE_TYPES.BROADCAST
  let currentSpeaker = null
  let contentBuffer = ''

  const pushCurrentContent = () => {
    const cleanedContent = cleanMetadata(contentBuffer.trim())
    if (cleanedContent) {
      let finalSpeaker = currentSpeaker || 'computer'
      let finalType = currentType

      if (finalType !== MESSAGE_TYPES.INTERNAL) {
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
      } else if (tag === 'TERRY' || tag === 'SHAQUILLE') {
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
          const internalParts = item.content.split(/(\[TERRY]|\[SHAQUILLE])\s*/g)

          let internalSpeaker = item.speaker || 'computer';
          let currentInternalContent = '';

          for (let i = 0; i < internalParts.length; i++) {
              const part = internalParts[i].trim();
              if (!part) continue;

              if (part === '[TERRY]' || part === '[SHAQUILLE]') {
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

const cleanCommandContent = (content) => {
    return content.replace(/\[HAL11000]\s*/g, '').trim();
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
  const uiSound = useUISound(window.audioEngine)
  const { token } = useAuth()
  const { toastError } = useUIState()
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
      console.error('Failed to load shoutout:', error)
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

  const renderDJMessage = ({ type, speaker, content, timestamp, messageType, key }) => {
    let displayContent = content;
    if (typeof displayContent === 'string') {
        displayContent = displayContent.replace(/\*.*?\*|%.*?%|@.*?@|&.*?&/g, ' ');
        displayContent = displayContent.replace(/\s{2,}/g, ' ').trim();
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
      if (type === MESSAGE_TYPES.INTERNAL) return <Brain className="w-4 h-4" />
      if (type === MESSAGE_TYPES.BROADCAST) return <Mic className="w-4 h-4" />
      return <MessageSquare className="w-4 h-4" />
    }

    const getLabel = () => {
      if (type === MESSAGE_TYPES.INTERNAL) return 'Internal Dialogue'
      if (speaker) {
        const speakerData = DJ_SPEAKERS[speaker]
        return speakerData?.name || speaker
      }
      return type === MESSAGE_TYPES.BROADCAST ? 'Broadcast' : 'Message'
    }

    return (
      <div key={key} className={`p-3 rounded-lg border ${getMessageStyle()} max-w-[80%] w-fit`}>
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
      setConversations(prev => [...prev, {
        type: 'user',
        content: data.text,
        timestamp: new Date().toISOString(),
        isFastTranscription: true,
        inputMethod: 'voice'
      }])
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
          processedConversations.push({
            ...conv,
            content: cleanCommandContent(conv.content),
            messageType: conv.messageType
          })
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

  useWebSocketSubscribe('conversation_update', (data) => {
    const newMessages = []
    const messageType = data.message_type || 'interactive'

    if (data.user_input) {
      setConversations(prev => {
        const fastMessageIndex = prev.findIndex(c =>
          c.type === 'user' &&
          c.isFastTranscription === true &&
          Math.abs(new Date(c.timestamp) - new Date()) < 30000
        )

        const cleanedUserInput = data.user_input.replace(/\[LISTENER TXT]\s*/, '').trim()

        if (fastMessageIndex !== -1) {
          const updated = [...prev]
          updated[fastMessageIndex] = {
            type: 'user',
            content: cleanedUserInput,
            timestamp: updated[fastMessageIndex].timestamp,
            isFastTranscription: false,
            messageType: messageType,
            inputMethod: updated[fastMessageIndex].inputMethod || 'voice'
          }
          return updated
        } else {
          return [...prev, {
            type: 'user',
            content: cleanedUserInput,
            timestamp: new Date().toISOString(),
            isFastTranscription: false,
            messageType: messageType,
            inputMethod: data.input_method || 'voice'
          }]
        }
      })
    }

    if (data.bot_response) {
      const content = data.bot_response
      const parsedMessages = typeof content === 'string' ? parseMessage(content) : []

      if (parsedMessages.length > 1) {
        let cumulativeDelay = 0

        parsedMessages.forEach((msg, idx) => {
          if (idx > 0) {
            cumulativeDelay += 200 + Math.random() * 600
          }
          const delay = cumulativeDelay

          setTimeout(() => {
            setConversations(prev => [...prev, {
              type: 'bot',
              content: msg.content,
              timestamp: new Date().toISOString(),
              messageType: messageType,
              djMessageType: msg.type,
              djSpeaker: msg.speaker
            }])

            if (msg.type === MESSAGE_TYPES.BROADCAST) {
              uiSound.playBroadcast()
            } else if (msg.type === MESSAGE_TYPES.TXT) {
              uiSound.playTxt()
            } else {
              uiSound.playBot()
            }
          }, delay)
        })

        if (data.commands) {
          setTimeout(() => {
            setConversations(prev => [...prev, {
              type: 'command',
              content: cleanCommandContent(data.commands),
              timestamp: new Date().toISOString(),
              messageType: messageType
            }])
            uiSound.playCommand()
          }, cumulativeDelay + 300)
        }
      } else {
        if (parsedMessages.length === 1) {
             const msg = parsedMessages[0]
             newMessages.push({
                type: 'bot',
                content: msg.content,
                timestamp: new Date().toISOString(),
                messageType: messageType,
                djMessageType: msg.type,
                djSpeaker: msg.speaker
             })
        } else {
            newMessages.push({
                type: 'bot',
                content: content,
                timestamp: new Date().toISOString(),
                messageType: messageType
            })
        }

        if (content.includes('[BROADCAST]')) {
          uiSound.playBroadcast()
        } else if (content.includes('[TXT]')) {
          uiSound.playTxt()
        } else {
          uiSound.playBot()
        }

        if (data.commands) {
          newMessages.push({
            type: 'command',
            content: cleanCommandContent(data.commands),
            timestamp: new Date().toISOString(),
            messageType: messageType
          })
          uiSound.playCommand()
        }
      }
    } else if (data.commands) {
      newMessages.push({
        type: 'command',
        content: cleanCommandContent(data.commands),
        timestamp: new Date().toISOString(),
        messageType: messageType
      })
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

    if (conv.type === 'bot') {
      return renderDJMessage({
        key: `${idx}`,
        type: conv.djMessageType,
        speaker: conv.djSpeaker,
        content: conv.content,
        timestamp: conv.timestamp,
        messageType: conv.messageType
      })
    }

    const colors = getMessageColors(conv.type)

    let displayContent = conv.content;

    if (conv.type === 'command') {
        displayContent = cleanCommandContent(conv.content);
    } else {
        displayContent = displayContent.replace(/\*.*?\*|%.*?%|@.*?@|&.*?&/g, ' ');
        displayContent = displayContent.replace(/\s{2,}/g, ' ').trim();
    }

    return (
      <div className={`p-3 rounded-lg border ${colors} max-w-[80%] w-fit ${['command', 'info', 'warning', 'error'].includes(conv.type) ? 'mx-auto' : ''}`}>
        {['command', 'info', 'warning', 'error'].includes(conv.type) ? (
          <div className="text-center">
            <div className="flex items-center justify-center gap-2 mb-1">
              <span className="flex items-center gap-2 text-xs font-medium uppercase tracking-wide opacity-70">
                {conv.type === 'command' && <span className="mr-1">&gt;_</span>}
                {getMessageIcon(conv.type)}
                {conv.type}
              </span>
            </div>
            <p className="text-sm whitespace-pre-wrap break-words">{parseShoutoutLinks(displayContent)}</p>
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

  return (
    <div className="w-full space-y-3 pb-3">
      {loading ? (
        <div className="flex items-center justify-center py-12">
          <div className="text-gray-400">Loading conversation...</div>
        </div>
      ) : filteredConversations.length === 0 ? (
        <div className="w-full min-h-[60vh] flex items-end justify-center pb-24 sm:pb-32">
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
          {filteredConversations.map((conv, idx) => {
            const isUserMessage = conv.type === 'user'
            const randomX = (Math.random() - 0.5) * 40
            const randomY = 30 + Math.random() * 30
            const randomRotate = (Math.random() - 0.5) * 8
            const randomScale = 0.2 + Math.random() * 0.3
            const damping = 12 + Math.random() * 8

            return (
              <motion.div
                key={conv.id || idx}
                initial={{
                  opacity: 0,
                  scale: randomScale,
                  y: randomY,
                  x: isUserMessage ? randomX : -randomX,
                  rotateZ: randomRotate
                }}
                animate={{
                  opacity: 1,
                  scale: 1,
                  y: 0,
                  x: 0,
                  rotateZ: 0
                }}
                exit={{ opacity: 0, scale: 0.85, y: -10 }}
                transition={{
                  type: "spring",
                  stiffness: 500,
                  damping,
                  mass: 0.6
                }}
              >
                {renderMessage(conv, idx)}
              </motion.div>
            )
          })}
          <div ref={chatEndRef} />
        </AnimatePresence>
      )}
    </div>
  )
}