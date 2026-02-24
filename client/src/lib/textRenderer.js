export class TextRenderer {
  constructor() {
    this.canvas = document.createElement('canvas')
    this.ctx = this.canvas.getContext('2d', { willReadFrequently: true })
    this.texture = null
    this.charMap = new Map()
  }

  prepareLyricData(lyricTimestamps) {
    if (!lyricTimestamps || !lyricTimestamps.lyrics) {
      return { words: [], wordData: [] }
    }

    const allWords = []
    const wordData = []

    lyricTimestamps.lyrics.forEach((line, lineIndex) => {
      if (!line.words || !Array.isArray(line.words)) {
        return
      }

      line.words.forEach((wordObj, wordIndex) => {
        const word = wordObj.word.toLowerCase()

        wordData.push({
          text: word,
          start: wordObj.start,
          end: wordObj.end,
          confidence: wordObj.confidence || 0.5,
          lineIndex,
          wordIndex
        })

        if (!allWords.includes(word)) {
          allWords.push(word)
        }
      })
    })

    return {
      words: allWords,
      wordData
    }
  }

  dispose() {
    if (this.texture) {
      this.texture.dispose()
    }
    this.charMap.clear()
  }
}