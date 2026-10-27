#!/bin/bash
# One scheduled pipeline run (launchd calls this every hour; see install-hourly.sh).
# Appends a timestamped section to logs/pipeline.log and keeps about a week of it.

cd "$(dirname "$0")/.." || exit 1
LOG=logs/pipeline.log
mkdir -p logs

# ~40 lines a run, 24 runs a day: trim to the last ~5 days once past ~8 days.
if [ -f "$LOG" ] && [ "$(wc -l < "$LOG")" -gt 8000 ]; then
  tail -n 5000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi

{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S %Z') start"
  # gridstatus logs every URL it fetches at INFO level; those lines are noise here.
  .venv/bin/python -m pipeline.run 2>&1 | grep -v -- " - INFO - "
  status=${PIPESTATUS[0]}
  echo "=== $(date '+%Y-%m-%d %H:%M:%S %Z') exit $status"
} >> "$LOG"
exit "$status"
