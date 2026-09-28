const fallback = new Map()

function get(key) {
  try {
    return localStorage.getItem(key)
  } catch {
    return fallback.get(key) ?? null
  }
}

function set(key, value) {
  try {
    localStorage.setItem(key, value)
  } catch {
    fallback.set(key, value)
  }
}

function remove(key) {
  try {
    localStorage.removeItem(key)
  } catch {
    fallback.delete(key)
  }
}

export const safeStorage = { get, set, remove }
