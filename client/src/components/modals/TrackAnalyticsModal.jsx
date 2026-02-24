import { useDynamicTheme } from '../../contexts/DynamicThemeContext'
import Modal, { ModalSection, ModalOptionButton } from './Modal'

const ANALYTICS_OPTIONS = [
  { id: 'top_hits_all', description: 'Most popular tracks ever' },
  { id: 'top_hits_week', description: 'Hot tracks from the last 7 days' },
  { id: 'top_hits_day', description: 'Trending tracks from today' },
]

export function TrackAnalyticsModal({ isOpen, onClose, onSelect }) {
  const { getCategoryMetadata } = useDynamicTheme()

  const handleSelect = (categoryId) => {
    onSelect(categoryId)
    onClose()
  }

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Station Analytics"
      maxWidth="max-w-md"
    >
      <ModalSection>
        <div className="grid gap-3 grid-cols-1">
          {ANALYTICS_OPTIONS.map((option) => {
            const metadata = getCategoryMetadata(option.id)

            return (
              <ModalOptionButton
                key={option.id}
                onClick={() => handleSelect(option.id)}
                icon={metadata.icon}
                iconColor={metadata.color}
                title={metadata.label}
                description={option.description}
              />
            )
          })}
        </div>
      </ModalSection>
    </Modal>
  )
}