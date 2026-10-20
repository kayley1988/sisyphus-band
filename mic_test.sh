#!/bin/bash
# One-click mic test: stop guitar.service -> capture level -> auto restart.
# Usage:  bash mic_test.sh [seconds, default 12]
export MSYS2_ARG_CONV_EXCL="*" MSYS_NO_PATHCONV=1
ADB="E:/音乐/档3实现/adb_tools/platform-tools/adb.exe"
LOCAL="E:/音乐/档3实现"
SEC="${1:-12}"

# Always bring the service back, whatever happens.
trap '"$ADB" shell "systemctl start guitar.service" >/dev/null 2>&1' EXIT

"$ADB" push "$LOCAL/mic_level_test.py" /tmp/mic_level_test.py >/dev/null 2>&1 || {
  echo "push failed - check adb connection"; exit 1; }

echo "== stop guitar.service (release mic) =="
"$ADB" shell "systemctl stop guitar.service && sleep 1 && echo stopped"
echo
"$ADB" shell "python3 -u /tmp/mic_level_test.py $SEC"
echo
echo "== restart guitar.service =="
"$ADB" shell "systemctl start guitar.service && echo restarted - about 19s to main screen"
