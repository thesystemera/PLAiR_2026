import { useDynamicTheme } from '../../contexts/DynamicThemeContext'
import { useViewport } from '../../contexts/ViewportContext'
import { useAuth } from '../../contexts/AuthContext'
import Modal, { ModalSection, ModalOptionButton, ModalTitle } from './Modal'

const SEED_OPTIONS = [
  { id: 'favorites', label: 'My Favorites', description: 'Your library on shuffle' },
  { id: 'discovery', label: 'Smart Discovery', description: 'Favorites + new gems' },
  { id: 'primary_genre', label: 'Main Genre', description: 'Same primary genre' },
  { id: 'secondary_genres', label: 'Sub-Genres', description: 'Similar sub-genres' },
  { id: 'mood', label: 'Mood', description: 'Similar vibes & feelings' },
  { id: 'primary_artist', label: 'More from Artist', description: 'Same artist' },
  { id: 'similar_artists', label: 'Similar Artists', description: 'Artists like this' },
  { id: 'style', label: 'Style', description: 'Production style' },
  { id: 'theme', label: 'Theme', description: 'Lyrical themes' },
  { id: 'lyrics', label: 'Lyrics', description: 'Similar lyrics' },
  { id: 'vocal', label: 'Vocal Style', description: 'Voice & delivery' },
  { id: 'all', label: 'All Categories', description: 'Balanced mix' },
]

export function SeedRadioModal({ isOpen, onClose, onSelect, track }) {
  const { getCategoryMetadata } = useDynamicTheme()
  const { isAuthenticated } = useAuth()
  const { isMobile } = useViewport()

  const handleSelect = (categoryId) => {
    onSelect(categoryId)
  }

  const isInstrumental = track?.generation_params?.instrumental || track?.instrumental
  const hasArtist = track?.generation_params?.artist_name || track?.artist_name

  const availableCategories = SEED_OPTIONS.filter(option => {
    if (isInstrumental && (option.id === 'vocal' || option.id === 'lyrics')) {
      return false
    }
    if (option.id === 'favorites' || option.id === 'discovery') {
      return true
    }
    return !(!hasArtist && (option.id === 'primary_artist' || option.id === 'similar_artists'));
  })

  const trackTitle = track?.title || track?.generation_params?.title

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={
        <ModalTitle
          title="Seed Radio"
          subtitle={trackTitle ? "Based on: " : null}
          subtitleHighlight={trackTitle}
        />
      }
      maxWidth="max-w-3xl"
    >
      <ModalSection>
        <div className={`grid gap-3 ${isMobile ? 'grid-cols-3' : 'grid-cols-1 sm:grid-cols-2'}`}>
          {availableCategories.map((option) => {
            const metadata = getCategoryMetadata(option.id)
            const requiresAuth = option.id === 'favorites' || option.id === 'discovery'
            const isDisabled = requiresAuth && !isAuthenticated

            return (
              <ModalOptionButton
                key={option.id}
                onClick={() => handleSelect(option.id)}
                icon={metadata.icon}
                iconColor={metadata.color}
                title={option.label}
                description={option.description}
                isDisabled={isDisabled}
                isMobile={isMobile}
              />
            )
          })}
        </div>
      </ModalSection>
    </Modal>
  )
}