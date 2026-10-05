# usage: catalog_run.sh <worker|main>  — reload with the scene on that thread, open Catalog (not Shoutouts), scroll down continuously
T=$1
node phone.mjs eval "localStorage.setItem('plair_scene_thread','$T'); 1" >/dev/null
node phone.mjs reload >/dev/null; node phone.mjs tap 37,35 >/dev/null; sleep 4
VIS='(() => { const p = [...document.querySelectorAll("[data-shader-panel]")].find(e => { const r = e.getBoundingClientRect(); return r.left >= -5 && r.left < 50 && r.width > 200 && r.height > 300 }); return p ? (p.innerText.includes("TRACKS") ? "catalog" : p.dataset.shaderPanel + (/shoutout/i.test(p.innerText) ? "/shoutouts" : "")) : "none" })()'
for attempt in 1 2 3; do
  v=$(node phone.mjs eval "$VIS")
  [ "$v" = '"catalog"' ] && break
  node phone.mjs tap 112,659 >/dev/null; sleep 2
done
st=$(node phone.mjs eval 'JSON.stringify({thread: window.__plairScene?.thread?.(), webgl: !!document.createElement("canvas").getContext("webgl2")})')
echo "$T on $(node phone.mjs eval "$VIS") $st"
for i in 1 2 3; do node phone.mjs scrollfps 187,400 "$T scroll"; done
