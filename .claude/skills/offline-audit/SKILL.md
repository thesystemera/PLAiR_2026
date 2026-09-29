---
name: offline-audit
description: Offline code-quality audit for PLAiR (dead code, unused/undefined names, UI state selectors that read fields that don't exist, dead exports/files/dependencies) before and after front-end or back-end changes. Use after a batch of edits, before a deploy, or when something "silently does nothing".
---

Run the gate from the repo root:

```
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check-quality.ps1
```

`-SkipBuild` skips the production build during quick repair loops. Run the full gate afterwards.

What it checks (all offline, no server, no paid calls):
- Python syntax (`compileall`), Ruff `F,E902` (undefined names, unused imports/variables) and Vulture at 100% confidence over `server`, `tts_server/server.py` and `tests`. `server/Apollo` is vendored and excluded.
- Frontend ESLint (errors fail; hook-dependency warnings are reported, not failed).
- `npm run check:ui-state` (`client/scripts/check-ui-state.mjs`): every `useUISelector(state => ...)` read must exist on the UIState value, nested reads (`state.settingsState.x`) must be a key that the initial state or a publish call sets, and every `reportEngineStatus({ key })` key must be one it handles (others are silently dropped). This is the check that catches "the button does nothing" bugs such as `state.ttsMuted` instead of `state.settingsState.ttsMuted`.
- `npm run dead-code` (Knip, `client/knip.json`): unused files, exports, duplicate exports and dependencies. `public/sw.js` and `client/scripts/*.mjs` are entry points.

Rules:
- Report every failed check with its output. Fix real findings, and never widen excludes or ignore lists to hide a new one.
- Before deleting an export, check dynamic `import()`/`lazyNamed` users, HTML entry points and runtime URL loads (service worker). An export used only inside its own file loses `export` instead of being deleted.
- Preserve side effects when deleting assignments or imports (module-level registration, calls inside the removed expression).
- Other Claude sessions edit the same tree. Check `git status` before touching a file, and stage only your own files.
- Where feasible, show that an observed bug fails the check before the fix and passes after (for example a probe file with the bad selector).
- An offline audit does not authorise restarts, paid model calls or deployments. Summarise what ran, counts, and anything left unresolved (for example the hook-dependency warnings).
