# usage: catalog_bench.sh <label> — reload, open Catalog, 3 scroll runs from the top, then a short profile
L=$1
node phone.mjs reload >/dev/null; node phone.mjs tap 37,35 >/dev/null; sleep 4
read st sp < <(bash open_catalog.sh)
[ "$st" != catalog ] && { echo "$L: could not open Catalog ($st)"; exit 1; }
for i in 1 2 3; do node phone.mjs scrollfps $sp "$L"; done
SX=${sp%,*} SY=${sp#*,} node phone.mjs profile 4 ${PROFILE_TOP:-8}
