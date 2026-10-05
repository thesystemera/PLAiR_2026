# usage: both_fps.sh <label> — scene (worker) fps and page fps over the same 4 s
L=$1
node phone.mjs eval "__plairScene.eval(\"const t0 = performance.now(); let n = 0; const orig = scene.frame.bind(scene); scene.frame = d => { n++; orig(d) }; self.__fpsDone = new Promise(r => setTimeout(() => { scene.frame = orig; r((n / ((performance.now() - t0) / 1000)).toFixed(1)) }, 4000)); return 1\")" >/dev/null
p=$(node phone.mjs fps 4 | python -c "import json,sys; print(json.loads(sys.stdin.read())['fps'])")
s=$(node phone.mjs eval "__plairScene.eval('return self.__fpsDone')" | tr -d '"')
echo "$L  scene $s fps  page $p fps"
