# usage: mem_bench.sh <label> [scroll seconds]: reload, open Catalog, GPU memory + lit-art stats, scroll, again
L=$1; S=${2:-20}
gl() { "$ADB" shell dumpsys meminfo --package com.android.chrome | awk '/MEMINFO in pid/ {p=$0} /^ *GL mtrack/ && p ~ /privileged/ {print int($3/1024)}' | head -1; }
node phone.mjs reload >/dev/null; node phone.mjs tap 37,35 >/dev/null; sleep 4
read st sp < <(bash open_catalog.sh)
[ "$st" != catalog ] && { echo "$L: could not open Catalog ($st)"; exit 1; }
sleep 3
echo "$L open:     GL $(gl) MB  $(node phone.mjs eval 'JSON.stringify(__plairArt.stats())')"
end=$((SECONDS + S)); while [ $SECONDS -lt $end ]; do node phone.mjs scrollfps $sp "$L" >/dev/null; done
sleep 2
echo "$L scrolled: GL $(gl) MB  $(node phone.mjs eval 'JSON.stringify(__plairArt.stats())')"
