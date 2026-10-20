#!/bin/bash
# 音频自检 —— 焊完喇叭/咪头后一键验证（喇叭放音 + 咪头电平）
# 用法: bash audio_check.sh [录音秒数, 默认 6]
#
# 流程: 停服务(释放声卡) -> 设音频通路 -> 播测试音(听喇叭)
#       -> 录音电平表(测咪头) -> 自动恢复服务
export MSYS2_ARG_CONV_EXCL="*" MSYS_NO_PATHCONV=1
ADB="E:/音乐/档3实现/adb_tools/platform-tools/adb.exe"
LOCAL="E:/音乐/档3实现"
SEC="${1:-6}"

# 无论如何都把服务拉回来
trap '"$ADB" shell "systemctl start guitar.service" >/dev/null 2>&1' EXIT

echo "==== 音频自检 ===="
echo
echo "[1/5] 停止 guitar.service (释放声卡)"
"$ADB" shell "systemctl stop guitar.service >/dev/null 2>&1; sleep 1; echo done"

echo
echo "[2/5] 设置音频通路 (采集 Main Mic + 播放 SPK)"
"$ADB" shell "sh /usr/local/bin/mic-path-main.sh"

echo
echo "[3/5] 喇叭测试 —— 播放中文语音 2 遍, 请仔细听板子"
"$ADB" shell "espeak -v zh '你好我是拨云现在测试喇叭一二三四五' -w /tmp/ac_spk.wav 2>/dev/null; aplay /tmp/ac_spk.wav 2>/dev/null; sleep 1; aplay /tmp/ac_spk.wav 2>/dev/null; echo played-2-times"

echo
echo "[4/5] 咪头测试 —— 录 ${SEC} 秒, 请对咪头大声数 1 2 3 4 5"
"$ADB" push "$LOCAL/mic_level_test.py" /tmp/mic_level_test.py >/dev/null 2>&1
"$ADB" shell "python3 -u /tmp/mic_level_test.py $SEC"

echo
echo "[5/5] 恢复 guitar.service"
"$ADB" shell "systemctl start guitar.service >/dev/null 2>&1; echo done"

echo
echo "==== 判读 ===="
echo "喇叭: 第 3 步听到中文语音 = 通路正常; 没声音 = 检查 SPKP/SPKN 焊点"
echo "咪头: 看第 4 步最后一行 Verdict"
