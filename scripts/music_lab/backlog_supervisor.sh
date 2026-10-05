#!/bin/bash
cd /e/AI_RADIO/server
for i in $(seq 1 50); do
  ../.venv/Scripts/python.exe utils/process_backlog.py --rerender >> ../data/logs/backlog_run.out 2>&1
  code=$?
  echo "$(date '+%F %T') backlog exited with $code (run $i)" >> ../data/logs/backlog_supervisor.log
  [ $code -eq 0 ] && break
  sleep 30
done
