import { browserSupportsWebAuthn, startAuthentication, startRegistration } from '@simplewebauthn/browser'
import { api } from './api'

export const passkeysSupported = () => typeof window !== 'undefined' && window.isSecureContext && browserSupportsWebAuthn()

export const passkeyCancelled = (err) => err?.name === 'NotAllowedError' || err?.name === 'AbortError'

let pendingLogin = null

export function prefetchPasskeyLogin() {
  if (!passkeysSupported()) return
  pendingLogin = api.passkeyLoginOptions().catch(() => null)
}

export async function passkeyLogin() {
  const prefetched = pendingLogin ? await pendingLogin : null
  pendingLogin = null
  const { request_id: requestId, options } = prefetched || await api.passkeyLoginOptions()
  const credential = await startAuthentication({ optionsJSON: options })
  return api.passkeyLogin(requestId, credential)
}

export async function passkeySignup(username) {
  const { request_id: requestId, options } = await api.passkeySignupOptions(username)
  const credential = await startRegistration({ optionsJSON: options })
  return api.passkeySignup(requestId, credential)
}

export async function addPasskey() {
  const { request_id: requestId, options } = await api.addPasskeyOptions()
  try {
    const credential = await startRegistration({ optionsJSON: options })
    return await api.addPasskey(requestId, credential)
  } catch (err) {
    if (err?.name === 'InvalidStateError') throw new Error('This device already has a passkey for your account')
    throw err
  }
}
