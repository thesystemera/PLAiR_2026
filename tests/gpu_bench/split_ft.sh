# usage: split_ft.sh single|split  — per-pass GPU ms with the glass drawn as one full-screen pass or split (slope between 2 and 10 extra passes)
M=$1
if [ "$M" = single ]; then
  node phone.mjs eval "__plairScene.eval(\"const s = scene; if (!s.__origSplit) s.__origSplit = s.updateGlassSplit; s.updateGlassSplit = function () { this.glassMesh.geometry = this.geometry; this.splitHasOuter = true; this.splitHasInterior = false }; return 1\")" >/dev/null
else
  node phone.mjs eval "__plairScene.eval(\"const s = scene; if (s.__origSplit) { s.updateGlassSplit = s.__origSplit; s.glassMesh.geometry = s.glassGeometry; s.splitCount = -1 } return 1\")" >/dev/null
fi
sft() { node phone.mjs eval "__plairScene.eval(\"const t0 = performance.now(); let n = 0; const orig = scene.frame.bind(scene); scene.frame = d => { n++; orig(d) }; return new Promise(r => setTimeout(() => { scene.frame = orig; r(((performance.now() - t0) / n).toFixed(2)) }, 3500))\")" | tr -d '"'; }
node phone.mjs eval "__plairScene.set({ force: true, extra: 2 })" >/dev/null; sleep 1.5; a=$(sft)
node phone.mjs eval "__plairScene.set({ extra: 10 })" >/dev/null; sleep 1.5; b=$(sft)
python -c "print('%-8s extra2 %6.2f  extra10 %6.2f  -> per pass %5.2f ms' % ('$M', $a, $b, ($b-$a)/8))"
