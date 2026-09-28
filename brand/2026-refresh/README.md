# PLAiR brand refresh

The cream tabletop radio in `radio-exact-crop.png` is the selected concept. It is a direct crop of the upper-left tile in `concept-sheet-original.png`; no later generated variation is used.

Run `E:/AI_RADIO/.venv/Scripts/python.exe brand/2026-refresh/build_assets.py` from the repository root to rebuild the masters and app files. The script preserves the radio artwork, clears stray transparent edge pixels, and creates the sizes used by the app.

## Masters

- `icon-master.png`: 1024×1024 opaque app icon
- `symbol-transparent.png`: 1024×1024 transparent radio
- `wordmark-white.png`, `wordmark-dark.png`: 2000×600 transparent wordmarks
- `symbol-mono-white.png`: 1024×1024 white notification mark
- `social-card.png`: 1200×630 share card
- `screenshot-frame.png`: optional 1080×1920 portrait frame
- `auth-background-desktop.png`: 1536×1024 login/registration background master
- `auth-background-mobile.png`: 1024×1536 mobile background master

Generated app assets are in `client/public/images/`, including the optimized `auth-bg-desktop.webp` and `auth-bg-mobile.webp`. The screenshot frame is a blank layout asset; the existing manifest screenshot remains in place until a real app capture is added.
