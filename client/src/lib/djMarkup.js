const DJ_MARKUP = /~[^~\n]+~|%[A-Za-z][^%\n\d]*%|\$[^$\s]+\$|@\d+@|&\d+(?:\.\d+)?&/g

export function stripDJMarkup(text, replacement = ' ') {
  if (!text) return text
  return text.replace(DJ_MARKUP, replacement)
}
