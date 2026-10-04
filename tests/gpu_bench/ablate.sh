#!/usr/bin/env bash
# Runs gpubench with one component switched off at a time and prints one line per variant and tab.
# Usage: bash ablate.sh <out_dir> <tabs> <variant>...   (variants listed in the case below)
OUT="$1"; TABS="$2"; shift 2
mkdir -p "$OUT"
COMMON=(${PACED:+--cpu 4} ${PACED:---uncapped 1} --quality high --trace 1 --urls https://plair.live --luid "${LUID:-0,86686}" --gpu P6000 --seconds 5 --tabs "$TABS")
for v in "$@"; do
  extra=(--modes light)
  case "$v" in
    base) ;;
    nobackdrop) extra+=(--css '*{backdrop-filter:none!important;-webkit-backdrop-filter:none!important}') ;;
    nomask) extra+=(--css '*{mask-image:none!important;-webkit-mask-image:none!important}') ;;
    nofilter) extra+=(--css '*{filter:none!important}') ;;
    noanim) extra+=(--css '*,*::before,*::after{animation:none!important;transition:none!important}') ;;
    noshadow) extra+=(--css '*{box-shadow:none!important;text-shadow:none!important}') ;;
    nolit) extra+=(--lit off) ;;
    noglass) extra+=(--uniforms 'u_glass_taps=1,u_glass_blur_factor=0,u_enable_refraction=0') ;;
    nobgfx) extra+=(--uniforms 'u_max_blur=0,u_chromatic=0') ;;
    novideo) extra+=(--block '*video-clips*') ;;
    still) extra=(--modes still) ;;
    *) echo "unknown variant $v"; continue ;;
  esac
  node "$(dirname "$0")/gpubench.mjs" "${COMMON[@]}" "${extra[@]}" > "$OUT/$v.txt" 2>&1
  python - "$OUT/$v.txt" "$v" <<'PY'
import json, re, sys
path, name = sys.argv[1], sys.argv[2]
text = open(path, encoding='utf-8', errors='replace').read()
mains = re.findall(r'CrRendererMain busy ([\d.]+) ms/frame \| self ms/frame: FunctionCall ([\d.]+)', text)
gpus = re.findall(r'CrGpuMain busy ([\d.]+) ms/frame', text)
rows = [json.loads(l) for l in text.splitlines() if l.startswith('{')]
for i, d in enumerate(rows):
    m = mains[i] if i < len(mains) else ('?', '?')
    g = gpus[i] if i < len(gpus) else '?'
    passes = ' '.join(f"{k}={v}" for k, v in sorted(d['gpu'].items()))
    print(f"{name:11} {d['tab']:8} fps {d['fps']:6} gpuBusy {d.get('gpuBusyMs')!s:6} webgl {d['gpuTotalMs']:6} main {m[0]:5} js {m[1]:5} gpuproc {g:5} | {passes}")
if not rows:
    print(name, 'FAILED', text[-300:])
PY
done
