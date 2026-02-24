import { logger } from './logger'

export async function retryWithBackoff(fn, options = {}) {
  const {
    maxAttempts = 3,
    baseDelay = 1000,
    maxDelay = 10000,
    shouldRetry = () => true,
    operationName = 'operation'
  } = options

  let lastError

  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      return await fn()
    } catch (error) {
      lastError = error

      if (!shouldRetry(error)) {
        logger.warn(`[Retry] ${operationName} failed with non-retryable error:`, error)
        throw error
      }

      if (attempt === maxAttempts) {
        logger.error(`[Retry] ${operationName} failed after ${maxAttempts} attempts:`, error)
        throw error
      }

      const exponentialDelay = Math.min(baseDelay * Math.pow(2, attempt - 1), maxDelay)
      const jitter = Math.random() * 0.3 * exponentialDelay // Add up to 30% jitter
      const delay = exponentialDelay + jitter

      logger.warn(`[Retry] ${operationName} attempt ${attempt}/${maxAttempts} failed, retrying in ${Math.round(delay)}ms...`, error.message)

      await new Promise(resolve => setTimeout(resolve, delay))
    }
  }

  throw lastError
}

export function isNetworkError(error) {
  if (!error) return false

  const errorMessage = error.message?.toLowerCase() || ''
  const errorName = error.name?.toLowerCase() || ''

  return (
    errorMessage.includes('failed to fetch') ||
    errorMessage.includes('network request failed') ||
    errorMessage.includes('networkerror') ||
    errorMessage.includes('timeout') ||
    errorName === 'networkerror' ||
    error.code === 'ECONNREFUSED' ||
    error.code === 'ETIMEDOUT'
  )
}

export async function retryableAPICall(fn, operationName = 'API call') {
  return retryWithBackoff(fn, {
    maxAttempts: 3,
    baseDelay: 1000,
    maxDelay: 8000,
    shouldRetry: isNetworkError,
    operationName
  })
}