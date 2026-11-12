#!/bin/bash
# Install, update or remove the launchd job that starts the hourly GitHub Actions run.
#   scripts/install-trigger.sh           install or update (also starts one run now)
#   scripts/install-trigger.sh --remove  stop and remove it
#
# GitHub's own cron (hourly.yml's `schedule`) is best-effort and, under load, skips runs;
# in Nov 2026 it was starting one about every 8 hours. This asks for a run at :08 past each
# hour with `gh workflow run`, so it starts around :10, once the ISO feeds have posted the
# hour that just ended. The pipeline itself still runs on GitHub; the Mac only has to be
# awake to send the request (a run missed during sleep is sent once on wake). If a run
# is already going, GitHub queues this one behind it.
set -euo pipefail

LABEL=com.ridgelinemaps.renewables-trigger
GH="$(command -v gh)"
REPO_SLUG=cedarcanvas/na-renewables-map
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
if [ "${1:-}" = "--remove" ]; then
  rm -f "$PLIST"
  echo "Removed $LABEL"
  exit 0
fi

mkdir -p "$REPO/logs"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
 <key>Label</key><string>$LABEL</string>
 <key>ProgramArguments</key><array>
  <string>/bin/bash</string><string>-c</string>
  <string>echo "=== \$(date '+%F %T %Z')"; $GH workflow run hourly.yml -R $REPO_SLUG</string>
 </array>
 <key>StartCalendarInterval</key><dict><key>Minute</key><integer>8</integer></dict>
 <key>RunAtLoad</key><true/>
 <key>ProcessType</key><string>Background</string>
 <key>StandardOutPath</key><string>$REPO/logs/trigger.log</string>
 <key>StandardErrorPath</key><string>$REPO/logs/trigger.log</string>
</dict></plist>
EOF
plutil -lint "$PLIST" > /dev/null
launchctl bootstrap "$DOMAIN" "$PLIST"
echo "Installed $LABEL: asks GitHub for a run at :08 past every hour and at login."
echo "Log: $REPO/logs/trigger.log"
