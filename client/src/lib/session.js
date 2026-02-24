import { logger } from './logger'
function generateUUID() {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
    const r = Math.random() * 16 | 0
    const v = c === 'x' ? r : (r & 0x3 | 0x8)
    return v.toString(16)
  })
}

export function getDeviceInfo() {
  const ua = navigator.userAgent
  let deviceType = 'desktop'
  let osName = 'Unknown'
  let browserName = 'Browser'

  if (/iPhone/.test(ua)) {
    deviceType = 'mobile'
    osName = 'iPhone'
  } else if (/iPad/.test(ua)) {
    deviceType = 'tablet'
    osName = 'iPad'
  } else if (/Android/.test(ua)) {
    deviceType = /Mobile/.test(ua) ? 'mobile' : 'tablet'
    osName = deviceType === 'mobile' ? 'Android Phone' : 'Android Tablet'
  } else if (/Mac/.test(ua)) {
    deviceType = 'desktop'
    osName = 'Mac'
  } else if (/Windows/.test(ua)) {
    deviceType = 'desktop'
    osName = 'Windows PC'
  } else if (/Linux/.test(ua)) {
    deviceType = 'desktop'
    osName = 'Linux PC'
  } else if (/CrOS/.test(ua)) {
    deviceType = 'desktop'
    osName = 'Chromebook'
  }

  if (/Edg/.test(ua)) {
    browserName = 'Edge'
  } else if (/Chrome/.test(ua)) {
    browserName = 'Chrome'
  } else if (/Firefox/.test(ua)) {
    browserName = 'Firefox'
  } else if (/Safari/.test(ua) && !/Chrome/.test(ua)) {
    browserName = 'Safari'
  } else if (/Opera|OPR/.test(ua)) {
    browserName = 'Opera'
  }

  return {
    type: deviceType,
    name: `${browserName} on ${osName}`,
    browser: browserName,
    os: osName
  }
}

export function getGuestId() {
  let guestId = localStorage.getItem('guest_id')
  if (!guestId) {
    guestId = `guest_${generateUUID()}`
    localStorage.setItem('guest_id', guestId)
    logger.info('[Session] Created new guest ID:', guestId)
  }
  return guestId
}

export function getDeviceId() {
  let deviceId = localStorage.getItem('device_id')
  if (!deviceId) {
    deviceId = generateUUID()
    localStorage.setItem('device_id', deviceId)
    logger.info('[Session] Created new device ID:', deviceId)

    const deviceInfo = getDeviceInfo()
    localStorage.setItem('device_name', deviceInfo.name)
    localStorage.setItem('device_type', deviceInfo.type)
  }
  return deviceId
}

export function getDeviceName() {
  let deviceName = localStorage.getItem('device_name')
  if (!deviceName) {
    const deviceInfo = getDeviceInfo()
    deviceName = deviceInfo.name
    localStorage.setItem('device_name', deviceName)
  }
  return deviceName
}

export function getDeviceType() {
  let deviceType = localStorage.getItem('device_type')
  if (!deviceType) {
    const deviceInfo = getDeviceInfo()
    deviceType = deviceInfo.type
    localStorage.setItem('device_type', deviceType)
  }
  return deviceType
}

export function getSessionIds() {
  return {
    guestId: getGuestId(),
    deviceId: getDeviceId(),
    deviceName: getDeviceName(),
    deviceType: getDeviceType()
  }
}