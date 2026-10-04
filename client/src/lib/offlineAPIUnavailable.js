import { logger } from './logger'
import { OFFLINE_MESSAGES } from './offlineAPI'

export const unavailableMethods = {
  async searchShoutouts(_query, _nResults = 20) {
    return {
      results: [],
      count: 0,
      message: 'Shoutouts need a connection',
      offline: true
    }
  },

  async getShoutout() {
    throw new Error(OFFLINE_MESSAGES.shoutouts)
  },

  async getShoutoutStats() {
    throw new Error(OFFLINE_MESSAGES.shoutouts)
  },

  async getShoutoutReplies() {
    return { replies: [], offline: true }
  },

  async uploadShoutoutReply() {
    throw new Error(OFFLINE_MESSAGES.reply)
  },

  async typeShoutoutReply() {
    throw new Error(OFFLINE_MESSAGES.reply)
  },

  async getMyCommunityPosts() {
    return { shoutouts: [], replies: [], reviews: [], offline: true }
  },

  async getTrackReviews(trackId) {
    return { reviews: [], count: 0, track_id: trackId, offline: true }
  },

  async uploadTrackReview() {
    throw new Error(OFFLINE_MESSAGES.review)
  },

  async typeTrackReview() {
    throw new Error(OFFLINE_MESSAGES.review)
  },

  async deleteShoutout() {
    throw new Error(OFFLINE_MESSAGES.deleteShoutout)
  },

  async getRadioMode() {
    throw new Error(OFFLINE_MESSAGES.radioMode)
  },

  async updateRadioMode() {
    throw new Error(OFFLINE_MESSAGES.radioMode)
  },

  async getVideoClips(trackId) {
    logger.info(`[OfflineBackend] Video clips not available offline for ${trackId}`)
    return { clips: [], reason: 'offline', offline: true }
  },

  async getUserUploads() {
    logger.info('[OfflineBackend] User uploads not available offline')
    return { tracks: [], offline: true }
  },

  async trackShoutoutPlay() {
    return { ok: true, offline: true }
  },

  async getTrackAnalytics() {
    return null
  },

  async getShoutoutAnalytics() {
    return null
  },

  async transcribe() {
    throw new Error(OFFLINE_MESSAGES.voiceSearch)
  },

  async deleteUserTrack() {
    throw new Error('Deleting tracks requires an internet connection')
  },

  async uploadProfilePicture() {
    throw new Error('Uploading profile picture requires an internet connection')
  },

  async deleteProfilePicture() {
    throw new Error('Deleting profile picture requires an internet connection')
  },

  async createStripeCheckout() {
    throw new Error('Payment requires an internet connection')
  },

  async createBillingPortal() {
    throw new Error('Managing your subscription requires an internet connection')
  },

  async getBillingStatus() {
    throw new Error('Subscription status requires an internet connection')
  },

  async uploadTrackArtwork() {
    throw new Error('Uploading artwork requires an internet connection')
  },

  async uploadMusic() {
    throw new Error('Uploading music requires an internet connection')
  },

  async listUploadJobs() {
    return { uploads: [], offline: true }
  },

  async getUploadJob() {
    throw new Error('Checking an upload requires an internet connection')
  },

  async cancelUploadJob() {
    throw new Error('Cancelling an upload requires an internet connection')
  },

  async getUploadSetup() {
    return { artists: [], last_artist_profile_id: null, upload_enhance: false, offline: true }
  },

  async createArtist() {
    throw new Error('Adding an artist requires an internet connection')
  },

  async updateArtist() {
    throw new Error('Editing an artist requires an internet connection')
  },

  async deleteArtist() {
    throw new Error('Deleting an artist requires an internet connection')
  },

  async getArtist() {
    return null
  },

  async updateUserTrack() {
    throw new Error('Editing a track requires an internet connection')
  },

  async updateUsername(_username) {
    logger.warn('[OfflineBackend] Username updates require an internet connection')
    return {
      status: 'error',
      error: 'Username updates require an internet connection',
      offline: true
    }
  },

  async register(_username, _password) {
    throw new Error('Creating an account needs a connection to PLAiR. Please try again when you are back online.')
  },

  async login(_username, _password) {
    throw new Error('Signing in needs a connection to PLAiR. Please try again when you are back online.')
  },

  async generate(_params) {
    logger.warn('[OfflineBackend] Music generation requires an internet connection')
    return {
      status: 'error',
      error: '🎵 Music generation requires an internet connection',
      offline: true
    }
  },

  async cancelGenerationJob(_jobId) {
    logger.warn('[OfflineBackend] Generation job cancellation requires an internet connection')
    return {
      status: 'error',
      error: 'Generation job cancellation requires an internet connection',
      offline: true
    }
  },

  async getGenerationJobs() {
    logger.info('[OfflineBackend] Generation jobs not available offline')
    return {
      jobs: [],
      message: 'Generation jobs require an internet connection',
      offline: true
    }
  },

  async djTalk(_params) {
    const offlineResponses = [
      "We're off air while PLAiR is offline, so we can't hear you right now. Your downloads keep playing, and we'll be back the moment the connection is.",
      "The studio line is down for a bit. Keep enjoying your downloads, and talk to us again when you're back online.",
      "No signal to the studio right now. The music keeps going from your downloads, and we'll pick up the chat when PLAiR is reachable again.",
    ]

    const randomResponse = offlineResponses[Math.floor(Math.random() * offlineResponses.length)]

    logger.info('[OfflineBackend] DJ chat attempted offline:', randomResponse)

    return {
      response: randomResponse,
      offline: true,
      sentiment: 'apologetic',
    }
  },

  async getConversationHistory(_limit = 3) {
    // Return empty history offline
    return {
      conversations: [],
      offline: true,
    }
  },

  async deleteConversationHistory() {
    logger.warn('[OfflineBackend] Conversation management requires an internet connection')
    return {
      status: 'error',
      error: 'Conversation management requires an internet connection',
      offline: true
    }
  },

  async resetPersona() {
    logger.warn('[OfflineBackend] Persona reset requires an internet connection')
    return {
      status: 'error',
      error: 'Persona reset requires an internet connection',
      offline: true
    }
  },

  async getDevices() {
    logger.info('[OfflineBackend] Device management not available offline')
    return {
      devices: [],
      message: 'Device management requires an internet connection',
      offline: true
    }
  },

  async activateDevice(_deviceId) {
    logger.warn('[OfflineBackend] Device activation requires an internet connection')
    return {
      status: 'error',
      error: 'Device activation requires an internet connection',
      offline: true
    }
  },

  async renameDevice(_deviceId, _newName) {
    logger.warn('[OfflineBackend] Device management requires an internet connection')
    return {
      status: 'error',
      error: 'Device management requires an internet connection',
      offline: true
    }
  },

  async removeDevice(_deviceId) {
    logger.warn('[OfflineBackend] Device management requires an internet connection')
    return {
      status: 'error',
      error: 'Device management requires an internet connection',
      offline: true
    }
  },
}
