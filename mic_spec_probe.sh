#!/bin/bash
# 咪头频谱探测：两档增益下 安静 vs 喊话（区分"没接好"与"信号太弱"）
# 用法: bash mic_spec_probe.sh [安静秒=10] [每档喊话秒=12]
export MSYS2_ARG_CONV_EXCL="*" MSYS_NO_PATHCONV=1
ADB="E:/音乐/档3实现/adb_tools/platform-tools/adb.exe"
LOCAL="E:/音乐/档3实现"
Q="${1:-10}"
S="${2:-12}"

trap '"$ADB" shell "systemctl start guitar.service" >/dev/null 2>&1' EXIT

echo "==== 咪头频谱探测 ===="
echo
echo "[1/4] 停止 guitar.service"
"$ADB" shell "systemctl stop guitar.service >/dev/null 2>&1; sleep 1; echo stopped"

echo
echo "[2/4] 通路 = Main Mic"
"$ADB" shell "amixer -c 0 sset 'Capture MIC Path' 'Main Mic' >/dev/null 2>&1; echo path-set"

echo
echo "[3/4] 开始探测: ${Q}s 安静, 然后持续喊话 ${S}*2 s"
"$ADB" push "$LOCAL/mic_spec_probe.py" /tmp/mic_spec_probe.py >/dev/null 2>&1
"$ADB" shell "python3 -u /tmp/mic_spec_probe.py $Q $S"

echo
echo "[4/4] 恢复 guitar.service"
"$ADB" shell "systemctl start guitar.service >/dev/null 2>&1; echo restored"
