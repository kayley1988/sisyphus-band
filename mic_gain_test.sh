#!/bin/bash
# Gain-boost mic test: stop service -> max out codec capture gains -> capture -> restore.
# Usage:  bash mic_gain_test.sh [seconds, default 25]
export MSYS2_ARG_CONV_EXCL="*" MSYS_NO_PATHCONV=1
ADB="E:/音乐/档3实现/adb_tools/platform-tools/adb.exe"
LOCAL="E:/音乐/档3实现"
SEC="${1:-25}"

# numid 16 = ADC Capture Volume (0..255)
# numid 17 = MIC Boost Gain      (0..3, 10dB/step)
# numid 18 = ADC PGA Gain        (0..15, 3dB/step)
restore() {
  "$ADB" shell "amixer -c 0 cset numid=17 2,2 >/dev/null 2>&1; \
                amixer -c 0 cset numid=18 10,10 >/dev/null 2>&1; \
                amixer -c 0 cset numid=16 255,255 >/dev/null 2>&1; \
                systemctl start guitar.service" >/dev/null 2>&1
  echo "== gains restored (Boost=2 PGA=10) + service restarted =="
}
trap restore EXIT

"$ADB" push "$LOCAL/mic_level_test.py" /tmp/mic_level_test.py >/dev/null 2>&1 || {
  echo "push failed"; exit 1; }

echo "== stop guitar.service =="
"$ADB" shell "systemctl stop guitar.service; sleep 1; echo stopped"
echo
echo "== boost capture gains to max =="
"$ADB" shell "amixer -c 0 cset numid=17 3,3 >/dev/null; amixer -c 0 cset numid=18 15,15 >/dev/null; amixer -c 0 cset numid=16 255,255 >/dev/null; amixer -c 0 cget numid=17 | tail -1; amixer -c 0 cget numid=18 | tail -1"
echo
"$ADB" shell "python3 -u /tmp/mic_level_test.py $SEC"
