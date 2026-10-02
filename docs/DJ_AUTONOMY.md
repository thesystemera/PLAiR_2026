# DJ autonomy: the station knows what you're up to

Future plan, written down 2 Oct 2026 from the owner's notes. Nothing here is built yet. The open item lives in
`docs/KNOWN_ISSUES.md` (section 7, Large jobs); this doc holds the idea.

## The idea

PLAiR should behave like a real radio station that happens to be made for one listener. Today the listener has to
think for themselves: they ask, the DJs play a few tracks, and the queue drifts back to whatever station mode was
running. The goal is a mode where the DJs have the authority to change the music on their own, because they know
what the listener is doing: walking, cycling, driving, studying, at the beach, at a party, winding down at night.

This is the owner's original 2010 thesis: music chosen by the activity you're in, with heart rate from a wearable
as the long-term signal. Part of the work is finding out how much of it still holds up today.

## Where we are now

- The DJs can already act: `search_and_play`, `seed_radio`, `play_playlist`, `radio_settings` (`dj_tools.py`).
  But actions only happen in reply to the listener's own message (`dj_tools.authorize_tool_call`), so the DJs
  never change the music on their own.
- A request plays its picks after the current track, then the station fills in behind them (`_auto_fill_queue`
  for the current `radio_mode`). Nothing remembers why the listener asked, so the mood of the request is lost after
  a few tracks.
- Radio Mode already gives the DJs scheduled airtime (talk breaks, For You narratives from `pulse_agent.py`), but
  those breaks talk; they don't steer the music.
- Signals we already have: the listener's location and local time (`listener_location`), weather, what aired
  (`listener_timeline`), likes, bans and skips (`play_events`), the conversation, and City Pulse for what's
  happening nearby.

## What "knowing what you're up to" could use

Strongest first, by what we can get today:

1. **What the listener says.** "Heading out for a run", "trying to focus", "people coming over". The hosts already
   read every message; they need somewhere to write down the activity they inferred and how long it is likely
   to last.
2. **Time and place.** Daypart, weekday or weekend, local weather, and the kind of place the listener is at
   (beach, park, gym, venue) from the places data we already store (`place_cache`, `geo.Where`).
3. **Movement.** Speed and steadiness from GPS (`watchPosition`): still, walking, running, cycling, driving. The
   accelerometer can add cadence (steps per minute) and separate walking from running. On the web, motion
   permission is only asked from Settings (Tilt Effects), and browsers pause pages in the background, so this
   only works properly in the native app (Android Activity Recognition, iOS Core Motion; see
   `docs/MOBILE_LAUNCH_READINESS.md`).
4. **Listening behaviour.** Skips, volume changes, sessions that start at the same time each day (commute, study
   block), and devices (car Bluetooth, headphones, desktop).
5. **Body signals (long term, the 2010 goal).** Heart rate and workout state from a watch through Health Connect
   (Android) or HealthKit (iOS). Native app only, always opt-in.

## How the DJs would act on it

- **An activity context node.** The Producer and the DJ get a live node with the inferred activity, its
  confidence and where each clue came from (said, place, movement, time). It is one input next to the others,
  never a rule, in line with "the DJs decide" (no keyword gates).
- **A DJ mode with authority.** A listener setting (next to Radio Mode) that lets the DJs change the music
  without being asked: reseed the station, shift energy or tempo, swap in a playlist. Every change is announced on
  air like a real host would ("you're on the move, let's pick it up"), shows in the activity cards, and stays
  undoable (one tap back to what was playing). The tool limits in `authorize_tool_call` would allow these actions
  only in that mode.
- **Requests that stick.** When the listener asks for something, the station remembers the intent ("focus music,
  for the next hour") and keeps filling to it, instead of going back to the previous mode after a few tracks.
- **Matching music to the activity.** The catalog already has tempo, energy, danceability and mood
  (`audio_features_service`), plus vector categories for mood and style. Walking or running cadence can be
  matched to tempo; study or wind-down to low energy and few vocals.

## Privacy

- Off by default and explained when switched on; each signal (movement, wearable) has its own opt-in.
- Work out the activity on the device where possible, and send the server only the label ("walking, outdoors"),
  never a raw track of positions. Same rule as `geo.Where`: a listener's own coordinates are never stored.
- The listener can see what the station thinks they're doing, and correct it.

## Open questions

- How often to re-check the activity, and how sure the station must be before changing the music on its own.
- How much a native app is needed before this is worth building (movement and wearables mostly need it).
- Whether the 2010 thesis (activity drives music choice) beats simple listening history, measured by skips and
  session length.
