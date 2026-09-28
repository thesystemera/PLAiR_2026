# PLAiR brand refresh handover

## User's chosen direction

Use the cream tabletop radio in `brand/2026-refresh/radio-exact-crop.png`. It was cropped directly from the upper-left tile of `brand/2026-refresh/concept-sheet-original.png`. The user chose this exact design after rejecting later variations. Keep **PLAiR** in the icon, with the amber broadcast signal rising from the lower-case **i**. This choice overrides the older no-text icon rule in `docs/BRAND_ASSETS_BRIEF.md`. Avoid navy or corporate-looking blue branding.

## Completed in this workspace

- Brand masters and rebuild script: `brand/2026-refresh/` and its `README.md`.
- PWA icon sizes, maskable icons, Apple touch icon, favicon, monochrome badge, and social image: `client/public/images/`.
- Manifest colors and icons: `client/public/manifest.json`.
- Open Graph and Twitter share metadata: `client/index.html`.
- Offline precache entries for the new images: `client/vite.config.js`.
- Login and Register backgrounds with warm amber actions: `client/src/components/Auth/Login.jsx`, `Register.jsx`, and `auth-background.css`.
- Desktop and portrait auth background PNG masters in `brand/2026-refresh/`; optimized WebP copies in `client/public/images/`.

The auth artwork was generated with the built-in image tool. Desktop prompt direction: a wide warm charcoal/amber illustrated late-night radio studio, vintage dials and record motifs at the edges, with a dark central 45% for the form. Mobile prompt direction: the same studio palette recomposed for portrait, with detail in the corners and a dark central 60% width and 65% height. Neither image contains text or UI.

## Verified

- PNG, WebP, JPEG, and ICO dimensions and alpha channels checked.
- `npm run build -- --outDir ../brand/2026-refresh/build-check` passed. The temporary build output was removed after checking.
- Local browser preview checked at desktop width and 390 CSS px phone width. Login and Register use the correct responsive background; the mobile Register dialog has no horizontal overflow and its submit button remains visible. The backend was not running, so no auth submission was tested.

## For the other AI

- Continue broader UI polish without replacing the chosen cream radio artwork. The working tree contains other unrelated in-progress UI edits, so inspect diffs before editing those files.
- Capture current real app screenshots if the PWA manifest screenshot should be refreshed. `screenshot-frame.png` is only an empty frame; the existing manifest screenshot was left as-is.
- Review the login/register styling in the context of any larger theme changes. The warm amber buttons and background are already wired.
- No production build or backend restart was run. `CLAUDE.md` says the live nginx site serves `client/dist` directly, so `npm run build` publishes the frontend to plair.live. Obtain the user's live-publish approval before that step. Never run `external_components/restart_all.bat` for this work.
