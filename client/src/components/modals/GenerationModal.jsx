import { Shuffle, User, Users } from 'lucide-react'
import { useState } from 'react'
import { triggerHaptic } from '../../lib/haptics'
import { useDynamicTheme } from '../../contexts/DynamicThemeContext'
import Modal, { ModalSection, ModalButton, ModalOptionButton, ModalTitle, ModalFooter } from './Modal'

const ARTIST_COLORS = [
  { bg: '#8b5cf620', border: '#8b5cf6', text: '#a78bfa' },
  { bg: '#ec489920', border: '#ec4899', text: '#f472b6' },
  { bg: '#3b82f620', border: '#3b82f6', text: '#60a5fa' },
  { bg: '#10b98120', border: '#10b981', text: '#34d399' },
  { bg: '#f59e0b20', border: '#f59e0b', text: '#fbbf24' },
  { bg: '#ef444420', border: '#ef4444', text: '#f87171' },
  { bg: '#06b6d420', border: '#06b6d4', text: '#22d3ee' },
  { bg: '#8b5cf620', border: '#8b5cf6', text: '#a78bfa' },
]

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
          subtitleHighlight={`${trackTitle}${artistName ? ` by ${artistName}` : ''}`}
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
          iconBgColor="#8b5cf620"
          title="Remix This Track"
          description="Generate a remix with similar style"
          isSelected={remixSelected}
        />
      </ModalSection>

      {artistName && (
        <ModalSection title="Generate from Artist">
          <ModalOptionButton
            onClick={() => setArtistSelected(!artistSelected)}
            icon={User}
            iconColor="#34d399"
            iconBgColor="#10b98120"
            title={`More from ${artistName}`}
            description="Generate new tracks in this artist's style"
            isSelected={artistSelected}
          />
        </ModalSection>
      )}

      {similarArtists.length > 0 && (
        <ModalSection title="Similar Artists">
          <div className="grid grid-cols-2 gap-2">
            {similarArtists.map((artist, idx) => {
              const isSelected = selectedSimilarArtists.includes(artist)
              const colorScheme = ARTIST_COLORS[idx % ARTIST_COLORS.length]

              return (
                <ModalOptionButton
                  key={artist}
                  onClick={() => handleToggleSimilarArtist(artist)}
                  icon={Users}
                  iconColor={colorScheme.text}
                  iconBgColor={colorScheme.bg}
                  title={artist}
                  isSelected={isSelected}
                  isMobile={true}
                  className="!p-2"
                />
              )
            })}
          </div>
        </ModalSection>
      )}

      <ModalFooter>
        <div className="text-sm" style={{ color: getGrey400() }}>
          {totalSelected > 0 ? `${totalSelected} job${totalSelected > 1 ? 's' : ''} selected` : 'Select at least one option'}
        </div>
        <ModalButton
          onClick={handleGenerate}
          disabled={totalSelected === 0}
          variant="primary"
        >
          Generate {totalSelected > 0 && `(${totalSelected})`}
        </ModalButton>
      </ModalFooter>
    </Modal>
  )
}