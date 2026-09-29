import { memo, useCallback, useEffect, useState } from 'react'
import { ExternalLink, Loader2, Play } from 'lucide-react'
import { useDynamicTheme, getCategoryMetadata } from '../../contexts/DynamicThemeContext'
import { useArtworkThumb, useUISelector } from '../../contexts/UIStateContext'
import { usePlaybackActions } from '../../contexts/PlaybackContext'
import { api } from '../../lib/api'
import { logger } from '../../lib/logger'
import { triggerHaptic } from '../../lib/haptics'
import { getFallbackGradientClass } from '../../lib/themeManager'
import { noteImageMount, revealOnLoad } from '../../lib/microMotion'
import { HumanBadge } from '../MediaShared'
import { Modal, ModalSection, ModalTitle, ModalButton, ModalErrorState } from './Modal'

const HUMAN_COLOR = getCategoryMetadata('human').color
const EMPTY_RESULT = { slug: null, artist: null, error: null }
const NO_TRACKS = []

function linkLabel(url) {
  try {
    const { hostname, pathname } = new URL(url)
    const host = hostname.replace(/^www\./, '')
    const path = pathname.replace(/\/+$/, '')
    return path ? `${host}${path}` : host
  } catch {
    return url
  }
}

function safeLinks(links) {
  return (Array.isArray(links) ? links : []).filter(link => /^https?:\/\//i.test(link || ''))
}

const ArtistTrackRow = memo(function ArtistTrackRow({ track, isCurrent, onPlay }) {
  const { getWhite, getGrey400, getBorder } = useDynamicTheme()
  const artworkUrl = useArtworkThumb(track.id, track.has_artwork)
  const [artworkError, setArtworkError] = useState(false)

  return (
    <button
      type="button"
      onClick={() => onPlay(track.id)}
      className="ui-press w-full flex items-center gap-3 p-2 rounded-lg text-left transition-colors hover:bg-white/10"
      style={{
        backgroundColor: isCurrent ? `${HUMAN_COLOR}26` : getBorder(0.05),
        boxShadow: isCurrent ? `inset 0 0 0 1px ${HUMAN_COLOR}80` : 'none'
      }}
    >
      {track.has_artwork && !artworkError ? (
        <img
          ref={noteImageMount}
          src={artworkUrl}
          alt={track.title || 'Track artwork'}
          className="w-12 h-12 rounded object-cover flex-shrink-0"
          onLoad={revealOnLoad}
          onError={() => setArtworkError(true)}
        />
      ) : (
        <div className={`w-12 h-12 rounded flex-shrink-0 bg-gradient-to-br ${getFallbackGradientClass(track.id)} flex items-center justify-center text-2xl`}>
          🎵
        </div>
      )}
      <div className="flex-1 min-w-0">
        <div className="font-medium truncate" style={{ color: getWhite() }}>{track.title || 'Untitled'}</div>
        {(track.genre || isCurrent) && (
          <div className="text-xs truncate" style={{ color: isCurrent ? HUMAN_COLOR : getGrey400() }}>
            {isCurrent ? 'Playing now' : track.genre}
          </div>
        )}
      </div>
      <Play size={16} className="flex-shrink-0" style={{ color: getGrey400() }} />
    </button>
  )
})

export function ArtistModal({ isOpen, onClose, slug }) {
  const [result, setResult] = useState(EMPTY_RESULT)
  const [reloadKey, setReloadKey] = useState(0)
  const { playTrack, addToQueue } = usePlaybackActions()
  const { getWhite, getGrey300, getGrey400 } = useDynamicTheme()
  const { currentTrackId, toastError } = useUISelector(state => ({
    currentTrackId: state.engineState.currentTrack?.id,
    toastError: state.toastError,
  }))

  useEffect(() => {
    if (!isOpen || !slug) return
    let cancelled = false
    api.getArtist(slug)
      .then(data => {
        if (!cancelled) setResult({ slug, artist: data, error: null })
      })
      .catch(error => {
        logger.error('[ArtistModal] Failed to load artist:', error)
        if (!cancelled) setResult({ slug, artist: null, error: error?.message || 'Could not load this artist' })
      })
    return () => { cancelled = true }
  }, [isOpen, slug, reloadKey])

  const handleRetry = useCallback(() => {
    setResult(EMPTY_RESULT)
    setReloadKey(key => key + 1)
  }, [])

  const handlePlay = useCallback((trackId) => {
    triggerHaptic('medium')
    void playTrack(trackId)
  }, [playTrack])

  const artist = result.slug === slug ? result.artist : null
  const error = result.slug === slug ? result.error : null
  const loading = result.slug !== slug
  const tracks = artist?.tracks || NO_TRACKS
  const links = safeLinks(artist?.links)

  const handlePlayAll = useCallback(async () => {
    const ids = tracks.map(track => track.id).filter(Boolean)
    if (!ids.length) return
    try {
      if (ids.length > 1) await addToQueue(ids)
      await playTrack(ids[0])
      onClose()
    } catch (err) {
      logger.error('[ArtistModal] Play all failed:', err)
      toastError('Could not play this artist right now', 4000)
    }
  }, [tracks, addToQueue, playTrack, onClose, toastError])

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      categoryOverride="human"
      maxWidth="max-w-lg"
      title={
        <ModalTitle
          title={artist?.name || (loading ? 'Artist' : 'Artist not found')}
          subtitle={artist ? `${tracks.length} track${tracks.length === 1 ? '' : 's'} on PLAiR` : null}
        />
      }
    >
      {loading ? (
        <div className="ui-fade-in flex items-center justify-center py-12">
          <Loader2 size={28} className="animate-spin" style={{ color: HUMAN_COLOR }} />
        </div>
      ) : error ? (
        <ModalErrorState
          title="Could not load this artist"
          message={error}
          onRetry={handleRetry}
          className="py-6"
        />
      ) : artist && (
        <>
          <ModalSection>
            <div className="flex items-center gap-2 mb-3">
              <HumanBadge />
              <span className="text-xs" style={{ color: getGrey400() }}>Real artist, uploaded to PLAiR</span>
            </div>
            {artist.bio ? (
              <p className="text-sm whitespace-pre-line break-words" style={{ color: getGrey300() }}>{artist.bio}</p>
            ) : (
              <p className="text-sm" style={{ color: getGrey400() }}>No bio yet.</p>
            )}
            {links.length > 0 && (
              <div className="flex flex-wrap gap-2 mt-4">
                {links.map(link => (
                  <a
                    key={link}
                    href={link}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="ui-press inline-flex items-center gap-1.5 max-w-full px-3 py-1.5 rounded-full text-xs font-medium bg-white/10 hover:bg-white/15 transition-colors"
                    style={{ color: getWhite() }}
                  >
                    <ExternalLink size={12} className="flex-shrink-0" />
                    <span className="truncate">{linkLabel(link)}</span>
                  </a>
                ))}
              </div>
            )}
          </ModalSection>

          <ModalSection title="Tracks">
            {tracks.length === 0 ? (
              <p className="text-sm" style={{ color: getGrey400() }}>No public tracks yet.</p>
            ) : (
              <>
                <ModalButton onClick={handlePlayAll} className="w-full mb-3 flex items-center justify-center gap-2">
                  <Play size={16} />
                  Play all
                </ModalButton>
                <div className="space-y-2">
                  {tracks.map(track => (
                    <ArtistTrackRow
                      key={track.id}
                      track={track}
                      isCurrent={track.id === currentTrackId}
                      onPlay={handlePlay}
                    />
                  ))}
                </div>
              </>
            )}
          </ModalSection>
        </>
      )}
    </Modal>
  )
}
