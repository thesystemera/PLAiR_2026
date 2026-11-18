const UI_SOUND_LEVEL = -18.6

const leveled = (file, loudest100ms) => ({ url: `/audio/interface/${file}.mp3`, gainDb: UI_SOUND_LEVEL - loudest100ms })

export const UI_SOUNDS = {
  recordPress: leveled('record_button_press_1', -17.9),
  recordRelease: leveled('record_button_release_1', -14.5),
  broadcast: leveled('broadcast', -8.2),
  txt: leveled('txt', -7.9),
  transcript: leveled('transcript', -15.1),
  bot: leveled('bot', -9.2),
  command: leveled('command', -11.5),
  error: leveled('error', -11.3),
  warning: leveled('warning', -14.2),
  info: leveled('info', -26.2),
  trackChange: leveled('spotify_track_change', -19.7),
}
