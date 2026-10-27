#!/bin/bash
# Install, update or remove the launchd job that runs the pipeline every hour.
#   scripts/install-hourly.sh           install or update (also runs it once now)
#   scripts/install-hourly.sh --remove  stop and remove it
#
# Runs at :10 past each hour, once the ISO feeds have posted the hour that just
# ended, and at login. A run missed while the Mac sleeps happens once on wake.
set -euo pipefail

LABEL=com.ridgelinemaps.renewables-map
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
 <key>ProgramArguments</key><array><string>/bin/bash</string><string>$REPO/scripts/hourly.sh</string></array>
 <key>StartCalendarInterval</key><dict><key>Minute</key><integer>10</integer></dict>
 <key>RunAtLoad</key><true/>
 <key>ProcessType</key><string>Background</string>
 <key>LowPriorityIO</key><true/>
 <key>Nice</key><integer>10</integer>
 <key>StandardOutPath</key><string>$REPO/logs/launchd.log</string>
 <key>StandardErrorPath</key><string>$REPO/logs/launchd.log</string>
</dict></plist>
EOF
plutil -lint "$PLIST" > /dev/null
launchctl bootstrap "$DOMAIN" "$PLIST"
echo "Installed $LABEL: runs at :10 past every hour and at login."
echo "Log: $REPO/logs/pipeline.log"
