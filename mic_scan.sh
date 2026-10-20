#!/bin/bash
# Silent 8-channel capture scan + MIC path comparison
export MSYS2_ARG_CONV_EXCL="*" MSYS_NO_PATHCONV=1
A="E:/音乐/档3实现/adb_tools/platform-tools/adb.exe"

echo "== asound pcm/devices =="
"$A" shell "cat /proc/asound/pcm; echo '--- devices ---'; cat /proc/asound/devices"
echo "== pdm-mic-array status =="
"$A" shell "cat /proc/device-tree/pdm-mic-array/status 2>/dev/null; echo; cat /proc/device-tree/pdm-mic-array/compatible 2>/dev/null; echo"

echo "== stop service =="
"$A" shell "systemctl stop guitar.service"
sleep 2

echo "== rec 8ch @48000, path=1 Main Mic (8s silent) =="
"$A" shell "arecord -D hw:0,0 -c 8 -r 48000 -f S16_LE -d 8 /tmp/ch8_main.wav 2>&1 | tail -4"

echo "== rec 8ch @48000, path=2 HandsFree (6s) =="
"$A" shell "amixer -c 0 cset numid=13 2 >/dev/null 2>&1; arecord -D hw:0,0 -c 8 -r 48000 -f S16_LE -d 6 /tmp/ch8_hf.wav 2>&1 | tail -4; amixer -c 0 cset numid=13 1 >/dev/null 2>&1; echo path-restored"

echo "== pull =="
"$A" pull /tmp/ch8_main.wav "E:/音乐/档3实现/_preview/ch8_main.wav"
"$A" pull /tmp/ch8_hf.wav "E:/音乐/档3实现/_preview/ch8_hf.wav"

echo "== restore service =="
"$A" shell "systemctl start guitar.service"
echo "== DONE =="
