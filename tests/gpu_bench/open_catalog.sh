# opens the track Catalog (not Shoutouts) in any orientation; prints "catalog" when it is on screen
VIS='(() => { const e = document.querySelector("[data-shader-panel=catalog]"); const r = e.getBoundingClientRect(); return r.left > -10 && r.left < 120 && e.innerText.includes("TRACKS") ? "catalog" : (r.left > -10 && r.left < 120 ? "shoutouts" : "hidden") })()'
BTN='JSON.stringify((() => { const b = [...document.querySelectorAll("button")].find(b => /^(Catalog|Shoutouts)$/.test(b.innerText.trim())); const r = b.getBoundingClientRect(); return [Math.round(r.x + r.width / 2), Math.round(r.y + r.height / 2)] })())'
for attempt in 1 2 3 4; do
  v=$(node phone.mjs eval "$VIS" | tr -d '"')
  [ "$v" = catalog ] && break
  xy=$(node phone.mjs eval "$BTN" | tr -d '"[]')
  node phone.mjs tap "$xy" >/dev/null; sleep 2
done
v=$(node phone.mjs eval "$VIS" | tr -d '"')
SP=$(node phone.mjs eval 'JSON.stringify((() => { const r = document.querySelector("[data-shader-panel=catalog]").getBoundingClientRect(); return [Math.round(r.left + r.width / 2), Math.round(r.top + r.height / 2)] })())' | tr -d '"[]')
echo "$v $SP"
