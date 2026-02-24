import { logger } from './logger'
export class CacheValidator {

  static validateTrackData(trackId, trackData) {
    const errors = []
    const warnings = []

    if (!trackId || typeof trackId !== 'string' || trackId.trim() === '') {
      errors.push('Invalid or missing trackId')
    }

    const audioBlobValidation = this.validateBlob(trackData.audioBlob, 'audioBlob', { required: true, minSize: 1 })
    errors.push(...audioBlobValidation.errors)
    warnings.push(...audioBlobValidation.warnings)

    if (trackData.artworkBlob) {
      const artworkValidation = this.validateBlob(trackData.artworkBlob, 'artworkBlob', { required: false, minSize: 1 })
      errors.push(...artworkValidation.errors)
      warnings.push(...artworkValidation.warnings)
    }

    if (trackData.enrichedArtworkBlob) {
      const enrichedValidation = this.validateBlob(trackData.enrichedArtworkBlob, 'enrichedArtworkBlob', { required: false, minSize: 1 })
      errors.push(...enrichedValidation.errors)
      warnings.push(...enrichedValidation.warnings)
    }

    const metadataValidation = this.validateMetadata(trackData.metadata)
    errors.push(...metadataValidation.errors)
    warnings.push(...metadataValidation.warnings)

    if (trackData.audioFeatures) {
      const featuresValidation = this.validateAudioFeatures(trackData.audioFeatures)
      errors.push(...featuresValidation.errors)
      warnings.push(...featuresValidation.warnings)
    }

    if (trackData.lyricTimestamps) {
      const lyricsValidation = this.validateLyricTimestamps(trackData.lyricTimestamps)
      errors.push(...lyricsValidation.errors)
      warnings.push(...lyricsValidation.warnings)
    }

    if (trackData.bitrate && !['128k', '192k', '256k', 'auto'].includes(trackData.bitrate)) {
      warnings.push(`Invalid bitrate value: ${trackData.bitrate}`)
    }

    const isValid = errors.length === 0

    if (!isValid) {
      logger.error(`[CacheValidator] Validation failed for ${trackId}:`, errors)
    }
    if (warnings.length > 0) {
      logger.warn(`[CacheValidator] Validation warnings for ${trackId}:`, warnings)
    }

    return { isValid, errors, warnings }
  }

  static validateBlob(blob, name, options = {}) {
    const errors = []
    const warnings = []
    const { required = false, minSize = 0 } = options

    if (!blob) {
      if (required) {
        errors.push(`${name} is required but missing`)
      }
      return { errors, warnings }
    }

    if (!(blob instanceof Blob)) {
      errors.push(`${name} is not a valid Blob instance`)
      return { errors, warnings }
    }

    if (blob.size === 0) {
      errors.push(`${name} is empty (0 bytes)`)
    } else if (minSize > 0 && blob.size < minSize) {
      warnings.push(`${name} size (${blob.size} bytes) is smaller than minimum (${minSize} bytes)`)
    }

    return { errors, warnings }
  }

  static validateMetadata(metadata) {
    const errors = []
    const warnings = []

    if (!metadata || typeof metadata !== 'object') {
      errors.push('Metadata is required and must be an object')
      return { errors, warnings }
    }

    if (!metadata.id || typeof metadata.id !== 'string') {
      errors.push('Metadata must contain a valid id')
    }

    const essentialFields = ['title', 'generation_params', 'created_at']
    essentialFields.forEach(field => {
      if (!metadata[field] && !metadata.generation_params?.[field]) {
        warnings.push(`Missing recommended metadata field: ${field}`)
      }
    })

    return { errors, warnings }
  }

  static validateAudioFeatures(audioFeatures) {
    const errors = []
    const warnings = []

    if (typeof audioFeatures !== 'object' || audioFeatures === null) {
      errors.push('audioFeatures must be an object')
      return { errors, warnings }
    }

    const expectedFields = ['tempo', 'key', 'energy', 'danceability', 'valence']
    const missingFields = expectedFields.filter(field => !(field in audioFeatures))

    if (missingFields.length === expectedFields.length) {
      warnings.push('audioFeatures missing all expected fields - may be invalid')
    }

    return { errors, warnings }
  }

  static validateLyricTimestamps(lyricTimestamps) {
    const errors = []
    const warnings = []

    if (!lyricTimestamps) {
      return { errors, warnings }
    }

    if (typeof lyricTimestamps !== 'object') {
      errors.push('lyricTimestamps must be an object or array')
      return { errors, warnings }
    }

    if (Array.isArray(lyricTimestamps)) {
      if (lyricTimestamps.length === 0) {
        warnings.push('lyricTimestamps is an empty array')
      } else {
        const first = lyricTimestamps[0]
        if (!first.timestamp && !first.time && !first.start_time) {
          warnings.push('lyricTimestamps entries may be malformed (missing timestamp field)')
        }
      }
    }

    return { errors, warnings }
  }

  static logValidationResult(trackId, trackData, result) {
    if (result.isValid) {
      const parts = [`${(trackData.audioBlob.size / 1024 / 1024).toFixed(2)} MB audio`]

      if (trackData.artworkBlob) {
        parts.push(`${(trackData.artworkBlob.size / 1024).toFixed(1)} KB artwork`)
      }

      if (trackData.enrichedArtworkBlob) {
        parts.push(`${(trackData.enrichedArtworkBlob.size / 1024).toFixed(1)} KB enriched artwork`)
      }

      if (trackData.audioFeatures) {
        parts.push('audio features')
      }

      if (trackData.lyricTimestamps) {
        parts.push('lyrics')
      }

      logger.info(`[CacheValidator] ✅ Validation passed for ${trackId}: ${parts.join(', ')}`)
    }
  }
}