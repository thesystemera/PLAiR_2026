import { Modal, ModalSection, ModalButton } from './Modal'
import { AlertTriangle } from 'lucide-react'
import { useDynamicTheme } from '../../contexts/DynamicThemeContext'
import { useViewport } from '../../contexts/ViewportContext'

export function CompatibilityWarningModal({ isOpen, onClose }) {
  const { getWhite, getGrey400 } = useDynamicTheme()
  const { isIOS, isSafari } = useViewport()

  const deviceType = isIOS ? 'iOS device' : isSafari ? 'Safari browser' : 'this device'

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Compatibility Warning"
      maxWidth="max-w-md"
      showCloseButton={false}
      closeOnBackdrop={false}
      categoryOverride="discovery"
    >
      <ModalSection>
        <div className="flex flex-col items-center text-center gap-4">
          <div
            className="p-4 rounded-full"
            style={{
              backgroundColor: '#f59e0b20',
              border: '2px solid #f59e0b',
            }}
          >
            <AlertTriangle size={48} style={{ color: '#f59e0b' }} />
          </div>

          <div>
            <h2
              className="text-xl font-bold mb-3"
              style={{ color: getWhite() }}
            >
              Limited Support on {deviceType}
            </h2>
            <p
              className="text-sm leading-relaxed mb-3"
              style={{ color: getGrey400() }}
            >
              PLAiR Radio has limited functionality on iOS/Safari due to Apple&apos;s browser restrictions:
            </p>
            <ul
              className="text-sm text-left space-y-2 mb-4"
              style={{ color: getGrey400() }}
            >
              <li className="flex items-start gap-2">
                <span style={{ color: '#f59e0b' }}>•</span>
                <span>Microphone access restrictions may prevent voice commands</span>
              </li>
              <li className="flex items-start gap-2">
                <span style={{ color: '#f59e0b' }}>•</span>
                <span>Audio playback may have permission issues or delays</span>
              </li>
              <li className="flex items-start gap-2">
                <span style={{ color: '#f59e0b' }}>•</span>
                <span>Background audio may stop when switching tabs</span>
              </li>
            </ul>
            <p
              className="text-sm leading-relaxed font-medium"
              style={{ color: getWhite(), opacity: 0.9 }}
            >
              For the best experience, please use PLAiR on:
            </p>
            <p
              className="text-sm leading-relaxed mt-2"
              style={{ color: '#10b981' }}
            >
              Android devices or Desktop browsers (Chrome, Firefox, Edge)
            </p>
          </div>

          <ModalButton
            onClick={onClose}
            variant="primary"
            className="w-full mt-2"
          >
            I Understand
          </ModalButton>
        </div>
      </ModalSection>
    </Modal>
  )
}