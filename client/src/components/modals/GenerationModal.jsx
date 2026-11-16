import { Shuffle, User, Users } from 'lucide-react'
import { useState } from 'react'
import { triggerHaptic } from '../../lib/haptics'
import { useDynamicTheme } from '../../contexts/DynamicThemeContext'
import { Modal, ModalSection, ModalButton, ModalOptionButton, ModalTitle, ModalFooter } from './Modal'
import { FadeSwap, Pop } from '../Motion'
import { PRESETS } from '../../lib/motion'
import { artistByline } from '../../lib/utils'

const ARTIST_COLORS = ['#a78bfa', '#f472b6', '#60a5fa', '#34d399', '#fbbf24', '#f87171', '#22d3ee']

export function GenerationModal({ isOpen, onClose, track, onGenerate }) {
  const [remixSelected, setRemixSelected] = useState(false)
  const [artistSelected, setArtistSelected] = useState(false)
  const [selectedSimilarArtists, setSelectedSimilarArtists] = useState([])

  const { getGrey400 } = useDynamicTheme()

  const handleToggleSimilarArtist = (artist) => {
    setSelectedSimilarArtists(prev =>
      prev.includes(artist)
        ? prev.filter(a => a !== artist)
        : [...prev, artist]
    )
  }

  const handleGenerate = () => {
    const jobs = []

    if (remixSelected) {
      jobs.push({
        type: 'remix',
        trackId: track.id
      })
    }

    if (artistSelected) {
      const artistName = track.generation_params?.artist_name || track.derived_tags?.inspired_artist
      if (artistName) {
        jobs.push({
          type: 'artist',
          artistName
        })
      }
    }

    selectedSimilarArtists.forEach(artist => {
      jobs.push({
        type: 'similar_artist',
        artistName: artist
      })
    })

    if (jobs.length === 0) return

    triggerHaptic('medium')
    onGenerate(jobs)
    onClose()
  }

  const trackTitle = track?.generation_params?.title || track?.title || 'Unknown Track'
  const artistName = track?.generation_params?.artist_name || track?.derived_tags?.inspired_artist
  const similarArtists = track?.derived_tags?.similar_artists || []

  const totalSelected = (remixSelected ? 1 : 0) +
                        (artistSelected ? 1 : 0) +
                        selectedSimilarArtists.length

  return (
    <Modal
      isOpen={isOpen && !!track}
      onClose={onClose}
      title={
        <ModalTitle
          title="Generate Music"
          subtitle="Based on: "
          subtitleHighlight={`${trackTitle}${artistName ? ` ${artistByline(track)}` : ''}`}
        />
      }
      maxWidth="max-w-2xl"
      maxHeight="max-h-[85vh]"
    >
      <ModalSection title="Remix Track">
        <ModalOptionButton
          onClick={() => setRemixSelected(!remixSelected)}
          icon={Shuffle}
          iconColor="#a78bfa"
          title="Remix This Track"
          description="Generate a remix with similar style"
          isSelected={remixSelected}
          toggle
        />
      </ModalSection>

      {artistName && (
        <ModalSection title="Generate from Artist">
          <ModalOptionButton
            onClick={() => setArtistSelected(!artistSelected)}
            icon={User}
            iconColor="#34d399"
            title={`More like ${artistName}`}
            description="Generate new tracks in this artist's style"
            isSelected={artistSelected}
            toggle
          />
        </ModalSection>
      )}

      {similarArtists.length > 0 && (
        <ModalSection title="Similar Artists">
          <div className="grid grid-cols-2 gap-2">
            {similarArtists.map((artist, idx) => {
              const isSelected = selectedSimilarArtists.includes(artist)

              return (
                <ModalOptionButton
                  key={artist}
                  onClick={() => handleToggleSimilarArtist(artist)}
                  icon={Users}
                  iconColor={ARTIST_COLORS[idx % ARTIST_COLORS.length]}
                  title={artist}
                  isSelected={isSelected}
                  isMobile
                  toggle
                />
              )
            })}
          </div>
        </ModalSection>
      )}

      <ModalFooter>
        <div className="relative text-sm" style={{ color: getGrey400() }}>
          <FadeSwap swapKey={totalSelected > 0 ? 'selected' : 'none'} preset={PRESETS.fade}>
            {totalSelected > 0 ? `${totalSelected} job${totalSelected > 1 ? 's' : ''} selected` : 'Select at least one option'}
          </FadeSwap>
        </div>
        <ModalButton
          onClick={handleGenerate}
          disabled={totalSelected === 0}
          variant="primary"
        >
          Generate <Pop show={totalSelected > 0} className="inline-block">{`(${totalSelected})`}</Pop>
        </ModalButton>
      </ModalFooter>
    </Modal>
  )
}