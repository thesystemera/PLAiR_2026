#!/bin/bash
GPU=${1:-0}
SHARD=${2:-}
SUFFIX=""
[ "$GPU" != "0" ] && SUFFIX="_gpu$GPU"
cd /e/AI_RADIO/server
for i in $(seq 1 50); do
  ../.venv/Scripts/python.exe utils/process_backlog.py --rerender --gpu "$GPU" ${SHARD:+--shard "$SHARD"} >> "../data/logs/backlog_run$SUFFIX.out" 2>&1
  code=$?
  echo "$(date '+%F %T') backlog on GPU $GPU ${SHARD:+(shard $SHARD) }exited with $code (run $i)" >> "../data/logs/backlog_supervisor$SUFFIX.log"
  [ $code -eq 0 ] && break
  sleep 30
done
