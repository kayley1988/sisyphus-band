#!/bin/bash
# 焊后原始电平对比：安静基线 vs 喊话（低硬件增益，不削波）
# 用法: bash mic_raw_cmp.sh [安静秒=6] [喊话秒=14]
#
# 流程: 停服务(释放声卡) -> 设低硬件增益 -> 跑对比 -> 自动恢复服务
export MSYS2_ARG_CONV_EXCL="*" MSYS_NO_PATHCONV=1
ADB="E:/音乐/档3实现/adb_tools/platform-tools/adb.exe"
LOCAL="E:/音乐/档3实现"
Q="${1:-6}"
S="${2:-14}"

trap '"$ADB" shell "systemctl start guitar.service" >/dev/null 2>&1' EXIT

echo "==== 原始电平对比 (安静 vs 喊话) ===="
echo
echo "[1/4] 停止 guitar.service"
"$ADB" shell "systemctl stop guitar.service >/dev/null 2>&1; sleep 1; echo stopped"

echo
echo "[2/4] 设低硬件增益 (看原始信号): Boost=0 PGA=0 ADC=200"
"$ADB" shell "amixer -c 0 sset 'Capture MIC Path' 'Main Mic' >/dev/null 2>&1; amixer -c 0 sset 'MIC Boost Gain' 0,0 >/dev/null 2>&1; amixer -c 0 sset 'ADC PGA Gain' 0,0 >/dev/null 2>&1; amixer -c 0 sset 'ADC Capture Volume' 200,200 >/dev/null 2>&1; echo gain-set"

echo
echo "[3/4] 开始测量 ${Q}s 安静 + ${S}s 喊话"
"$ADB" push "$LOCAL/mic_raw_cmp.py" /tmp/mic_raw_cmp.py >/dev/null 2>&1
"$ADB" shell "python3 -u /tmp/mic_raw_cmp.py $Q $S"

echo
echo "[4/4] 恢复 guitar.service"
"$ADB" shell "systemctl start guitar.service >/dev/null 2>&1; echo restored"
