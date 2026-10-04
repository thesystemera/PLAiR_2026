import { useDynamicTheme } from '../../contexts/DynamicThemeContext'
import { Modal, ModalSection, ModalOptionButton } from './Modal'

const LIST_MODALS = {
  mine: {
    title: 'Your Music',
    options: [
      { id: 'favorites', description: 'Your library on shuffle' },
      { id: 'discovery', description: 'Favorites + new gems' },
    ],
  },
  charts: {
    title: 'Charts',
    options: [
      { id: 'top_hits_all', description: 'Most popular tracks ever' },
      { id: 'top_hits_week', description: 'Hot tracks from the last 7 days' },
      { id: 'top_hits_day', description: 'Trending tracks from today' },
    ],
  },
}

export function ListModal({ isOpen, kind, onClose, onSelect }) {
  const { getCategoryMetadata } = useDynamicTheme()
  const list = LIST_MODALS[kind]

  const handleSelect = (categoryId) => {
    onSelect(categoryId)
    onClose()
  }

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={list?.title}
      maxWidth="max-w-md"
    >
      <ModalSection>
        <div className="grid gap-3 grid-cols-1">
          {(list?.options || []).map((option) => {
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
