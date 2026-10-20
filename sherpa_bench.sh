#!/bin/bash
# 离线 TTS 冒烟 + 基准：停服务(腾CPU) -> 合成 -> 播放 -> 恢复服务
# 用法: bash sherpa_bench.sh [模型目录] [线程数]
export MSYS2_ARG_CONV_EXCL="*" MSYS_NO_PATHCONV=1
ADB="E:/音乐/档3实现/adb_tools/platform-tools/adb.exe"
MODEL="${1:-/root/guitarist_app/models/tts_piper_xiaoya}"
THREADS="${2:-4}"

trap '"$ADB" shell "systemctl start guitar.service" >/dev/null 2>&1' EXIT

echo "== [1/4] 停止 guitar.service（腾出 CPU）=="
"$ADB" shell "systemctl stop guitar.service; sleep 1; echo stopped"

echo "== [2/4] 推送测试脚本 + 合成（模型=$MODEL 线程=$THREADS）=="
"$ADB" push "E:/音乐/档3实现/sherpa_tts_test.py" /tmp/sherpa_tts_test.py >/dev/null 2>&1
"$ADB" shell "python3 -u /tmp/sherpa_tts_test.py $MODEL /tmp/sherpa_tts.wav $THREADS 2>&1"

echo "== [3/4] 播放合成结果（听板子）=="
"$ADB" shell "amixer -c 0 sset 'Playback Path' SPK >/dev/null 2>&1; aplay -q /tmp/sherpa_tts.wav && echo played-ok"

echo "== [4/4] 恢复 guitar.service =="
"$ADB" shell "systemctl start guitar.service >/dev/null 2>&1; echo restarted"
