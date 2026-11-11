#!/bin/bash
GPU=${1:-0}
SHARD=${2:-}
SUFFIX=""
[ "$GPU" != "0" ] && SUFFIX="_gpu$GPU"
cd /e/AI_RADIO
echo "PLAiR remaster on GPU $GPU ${SHARD:+(shard $SHARD)} - close this window to stop it"
bash scripts/music_lab/backlog_supervisor.sh "$GPU" "$SHARD" &
sleep 2
tail -n 0 -F "data/logs/backlog$SUFFIX.log" | grep --line-buffered -E "\[Lane [0-9b]+\]|\[Backlog\]|\[ERROR\]|Traceback"
