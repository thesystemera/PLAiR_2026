# opens the Catalog panel (not Shoutouts) by its nav button, any orientation
VIS='(() => { const p = [...document.querySelectorAll("[data-shader-panel]")].find(e => { const r = e.getBoundingClientRect(); return r.width > 200 && r.height > 200 && r.left >= -5 && r.right <= innerWidth + 5 && e.innerText.includes("TRACKS") }); return p ? "catalog" : "other" })()'
BTN='JSON.stringify((() => { const b = [...document.querySelectorAll("button")].find(b => b.innerText.trim() === "Catalog" || b.innerText.trim() === "Shoutouts"); const r = b.getBoundingClientRect(); return [Math.round(r.x + r.width / 2), Math.round(r.y + r.height / 2)] })())'
for attempt in 1 2 3 4; do
  v=$(node phone.mjs eval "$VIS")
  [ "$v" = '"catalog"' ] && break
  xy=$(node phone.mjs eval "$BTN" | tr -d '"[]')
  node phone.mjs tap "$xy" >/dev/null; sleep 2
done
echo "panel $(node phone.mjs eval "$VIS")"
