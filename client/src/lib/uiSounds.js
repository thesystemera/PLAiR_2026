const UI_SOUND_LUFS = -22

const leveled = (file, measuredLufs) => ({ url: `/audio/interface/${file}.mp3`, gainDb: UI_SOUND_LUFS - measuredLufs })

export const UI_SOUNDS = {
  recordPress: leveled('record_button_press_1', -21.3),
  recordRelease: leveled('record_button_release_1', -17.3),
  broadcast: leveled('broadcast', -12.9),
  txt: leveled('txt', -11.2),
  transcript: leveled('transcript', -20.5),
  bot: leveled('bot', -11.4),
  command: leveled('command', -13.4),
  error: leveled('error', -15.3),
  warning: leveled('warning', -15.8),
  info: leveled('info', -32.1),
  trackChange: { url: '/audio/interface/spotify_track_change.mp3', gainDb: -6 },
}
