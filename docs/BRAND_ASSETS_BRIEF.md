# PLAiR brand refresh: brief for ChatGPT image generation

Paste this whole document into ChatGPT (image generation on), then use the prompts in section 4 one at a time. You only need the **master images** listed in section 3. Hand them back to Claude, which cuts every size and format the app needs (section 6), so you don't have to ask ChatGPT for exact pixel sizes.

---

## 1. What PLAiR is
- **PLAiR** (on air as "PLAiR.fm"; the station voice says "Play Air") is an AI radio station in your pocket.
  - It plays music, and two AI DJ hosts talk between songs.
  - It reads local news, weather and events for your city.
  - It plays listener shoutouts.
- The name is an acronym: **P**ersonalised **L**ocalised **A**daptive **I**nteractive **R**adio. The lower-case **i** in "PLAiR" is deliberate: it's the "AI" hidden inside the word.
- **Feel:** late-night radio studio, warm and human, a little futuristic. The ON AIR lamp, glowing dials and a vinyl turntable are all part of the app's look.
- **App colours today:**
  - background: near-black navy `#0B1131` / `#121212`;
  - accent: green `#10B981` (playing);
  - status colours: red (recording / ON AIR), amber `#F59E0B` (ON AIR lamp, offline) and blue (AI thinking).
  - The app changes accent colours by music genre, so the logo must work on dark backgrounds and alongside many colours.

## 2. The current logo (what we're refreshing)
- A white line-art mark on a dark navy square: a radio tower / signal symbol (concentric rings around a dot over a triangle mast) sitting inside a phone outline, with the word **PLAiR** across the middle.
- **Problems:**
  - it reads as generic clip-art;
  - the wordmark inside the icon is unreadable at small sizes;
  - the phone outline makes it tall, so it sits awkwardly in round and square icon masks.
- **Keep:**
  - the idea of broadcast / signal;
  - dark background plus a bright mark;
  - the stylised lower-case **i**.

## 3. Master images we need

| # | Master | Size to ask for | Notes |
|---|--------|-----------------|-------|
| A | **App icon master** | 1024×1024, square, **opaque** background (no transparency) | The symbol only, **no text**. Keep everything important inside the central **70%** circle (Android masks cut the corners; iOS rounds them). |
| B | **Symbol on transparent** | 1024×1024 PNG, transparent background | The same symbol as A, no background, for use inside the app. |
| C | **Wordmark** | about 2000×600 PNG, transparent background | "PLAiR" in the brand lettering, white. Optionally a second copy in dark navy for light backgrounds. |
| D | **Monochrome symbol** | 1024×1024 PNG, pure white on transparent | Single colour, no gradients. Used for the Android status bar / notification badge and lock-screen tinting. |
| E | **Social share card** | 1200×630 | Symbol + wordmark + the tagline "Your music, your voice", on the dark studio background. Used when someone shares a plair.live link. |
| F | **Phone screenshots frame** (optional) | 1080×1920 | A tall dark background with a big headline slot, e.g. "An AI radio station that knows your city". Claude drops real app screenshots into it for the install screen. |

**Rules for all masters:**
- **Flat, vector-style shapes** with at most 2-3 colours plus one accent glow. No photographic textures.
- No thin hairlines (they vanish at 48 px).
- **No text** in A, B or D.
- Must read at **32×32 pixels**: test by zooming out until it's thumbnail-size.
- Must look good on both dark and light backgrounds (B and D).

## 4. Prompts (copy one at a time)

**Prompt 1: explore directions (do this first)**
> Create 4 different app-icon concepts for "PLAiR", an AI radio station app. It plays music, and two AI DJs talk between songs about your city. Each concept is a single bold symbol on a dark navy (#0B1131) square background, flat vector style, 2-3 colours, one warm amber (#F59E0B) or emerald (#10B981) accent glow, no text, no thin lines, readable at 32 px. Directions: (1) a broadcast signal (radio waves) forming a lower-case "i"; (2) a vinyl record whose centre label is an ON AIR lamp; (3) a microphone grille made of sound waves; (4) a stylised speech bubble merged with radio waves (the DJ talking to you). Show them as a 2×2 grid, labelled 1-4.

**Prompt 2: refine the chosen one** (replace N with your pick)
> Take concept N and make a final app icon master: 1024×1024, opaque dark navy (#0B1131) background with a very subtle radial glow behind the symbol. Keep the entire symbol inside the central 70% circle with generous padding. Flat vector look, crisp edges, no text, no thin hairlines, high contrast so it stays readable at 32×32 px.

**Prompt 3: transparent symbol**
> Same symbol as the final icon, alone on a fully transparent background, 1024×1024 PNG, no background shape, no glow, crisp flat vector edges.

**Prompt 4: monochrome symbol**
> Same symbol, pure white (#FFFFFF), single flat colour, no gradients, no glow, on a transparent background, 1024×1024 PNG. It must stay recognisable as a tiny white silhouette.

**Prompt 5: wordmark**
> A wordmark reading "PLAiR" (capital P, L, A, R with a lower-case "i"). The "i" should echo the icon's symbol (for example its dot is the signal dot or the ON AIR lamp). Modern geometric sans-serif, bold but friendly, white on transparent background, wide format about 2000×600. Keep the letters clean enough to print small.

**Prompt 6: social share card**
> A 1200×630 social sharing image for PLAiR: dark late-night radio-studio atmosphere in navy and black with a soft amber ON AIR glow, the final PLAiR symbol on the left, the PLAiR wordmark and the line "Your music, your voice" on the right. Minimal, lots of breathing room, no extra text.

**Prompt 7 (optional): screenshot frame**
> A 1080×1920 portrait background for an app-store style screenshot: dark navy gradient with a soft amber glow at the top, a large empty rounded-rectangle area in the lower 75% for placing a phone screenshot, and space at the top for a two-line headline. No text, no phone mock-up.

**Tips:**
- If ChatGPT adds text to an icon, reply "remove all text".
- If lines get thin, reply "make the strokes 30% thicker".
- Download the **PNG** files at the largest size offered.

## 5. What to send back to Claude
Put the masters in a folder, for example `E:\AI_RADIO\brand\2026-refresh\`:
- `icon-master.png` (A)
- `symbol-transparent.png` (B)
- `wordmark-white.png` (C), plus `wordmark-dark.png` if you have it
- `symbol-mono-white.png` (D)
- `social-card.png` (E)
- optional `screenshot-frame.png` (F)

Then ask Claude: "Make the app assets from the brand masters in that folder."

## 6. What Claude will generate from the masters (for reference)
All files go in `client/public/images/`. The manifest (`client/public/manifest.json`), `client/index.html` and the offline precache list in `client/vite.config.js` are updated to match.

| File | Size | From | Used by |
|------|------|------|---------|
| `plair_icon.png` | 1024 | A | master copy |
| `plair_icon_512.png` / `plair_icon_192.png` | 512 / 192 | A | manifest "any" icons, Android install |
| `plair_icon_maskable.png` / `plair_icon_maskable_192.png` | 512 / 192 | A, extra padding | Android adaptive (round/squircle) icons |
| `apple-touch-icon.png` | 180, no transparency | A | iPhone/iPad home screen |
| `favicon.ico` | 16 / 32 / 48 | A | browser tab |
| `badge-mono-96.png` | 96, white on transparent | D | Android notification / lock screen |
| `og-image.jpg` | 1200×630 | E | link previews (plus `og:`/`twitter:` meta tags in `index.html`) |
| `screenshot-narrow-*.png` | 1080×1920 | F + real app captures | install prompt screenshots (`form_factor: narrow`) |
| `screenshot-wide-*.png` | 1920×1080 | real app captures | desktop install prompt (`form_factor: wide`) |
| optional iOS splash images | per device size | A + background colour | launch screen on older iPhones |

**Current state (28 Sep 2026):**
- The existing icon has been re-cut:
  - a padded maskable version;
  - a proper 180 px Apple touch icon.
- The manifest has been fixed: an `id` was added, and the screenshot sizes were wrong and have been corrected.

This means the app installs cleanly today. The refresh above is purely about a better-looking mark.
