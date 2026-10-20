#!/bin/bash
# KWS probe: stop service -> run probe -> pull wav -> restore service
export MSYS2_ARG_CONV_EXCL="*" MSYS_NO_PATHCONV=1
A="E:/音乐/档3实现/adb_tools/platform-tools/adb.exe"

echo "== stop service =="
"$A" shell "systemctl stop guitar.service"
sleep 2

echo "== push probe =="
"$A" push "E:/音乐/档3实现/kws_probe.py" /root/kws_probe.py

echo "== run probe =="
"$A" shell "cd /root && python3 kws_probe.py 2>&1"

echo "== pull wav =="
"$A" pull /root/kws_probe.wav "E:/音乐/档3实现/_preview/kws_probe.wav"

echo "== restore service =="
"$A" shell "systemctl start guitar.service"

echo "== DONE (service needs ~19s to reach main screen) =="
