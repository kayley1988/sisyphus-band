#!/bin/bash
# 麦克风增益链修正部署：硬件 mid 档 + 软件 mic_gain / AGC 收敛
# 用法: bash deploy_micgain.sh [deploy|verify]
export MSYS2_ARG_CONV_EXCL="*" MSYS_NO_PATHCONV=1
ADB="E:/音乐/档3实现/adb_tools/platform-tools/adb.exe"
SRC="E:/音乐/拨云_开源参考/liguanxi_Automatic_guitar/src/guitarist"
DST="/root/guitarist_app/src/guitarist"
BAK=".bak_1011_micgain"
FILES="ai/audio_utils.py ai/listener.py ai/asr_engine.py ai/context.py"
LSH="E:/音乐/档3实现/mic-path-main.sh"
BSH="/usr/local/bin/mic-path-main.sh"

case "$1" in
deploy)
  echo "=== 1. 备份板端 ==="
  for f in $FILES; do
    "$ADB" shell "cp $DST/$f $DST/$f$BAK" >/dev/null 2>&1 && echo "  backed: $f"
  done
  "$ADB" shell "cp $BSH $BSH$BAK" >/dev/null 2>&1 && echo "  backed: mic-path-main.sh"
  "$ADB" shell "cp /root/guitarist_app/config/ai_config.json /root/guitarist_app/config/ai_config.json$BAK" >/dev/null 2>&1 && echo "  backed: ai_config.json"

  echo "=== 2. 推送 ==="
  for f in $FILES; do
    "$ADB" push "$SRC/$f" "$DST/$f" 2>&1 | tail -1
  done
  "$ADB" push "$LSH" "$BSH" 2>&1 | tail -1
  "$ADB" push "E:/音乐/档3实现/_set_micgain.py" /tmp/_set_micgain.py >/dev/null 2>&1 && echo "  pushed: _set_micgain.py"

  echo "=== 3. md5 校验 ==="
  for f in $FILES; do
    L=$(md5sum "$SRC/$f" | awk '{print $1}')
    B=$("$ADB" shell "md5sum $DST/$f | awk '{print \$1}'" | tr -d '\r')
    if [ "$L" = "$B" ]; then echo "  MATCH    $f"; else echo "  MISMATCH $f local=$L board=$B"; fi
  done
  L=$(md5sum "$LSH" | awk '{print $1}')
  B=$("$ADB" shell "md5sum $BSH | awk '{print \$1}'" | tr -d '\r')
  if [ "$L" = "$B" ]; then echo "  MATCH    mic-path-main.sh"; else echo "  MISMATCH mic-path-main.sh local=$L board=$B"; fi

  echo "=== 4. 板上编译 ==="
  "$ADB" shell "cd $DST && python3 -m py_compile ai/audio_utils.py ai/listener.py ai/asr_engine.py ai/context.py && echo BOARD_COMPILE_OK"

  echo "=== 5. 改 ai_config.json mic_gain -> 1.0 ==="
  "$ADB" shell "python3 /tmp/_set_micgain.py"
  "$ADB" shell "grep mic_gain /root/guitarist_app/config/ai_config.json" | tr -d '\r'
  ;;
verify)
  echo "=== 重启服务 ==="
  "$ADB" shell "systemctl restart guitar.service; echo restarted"
  echo "=== 等待启动（约 30s）==="
  sleep 32
  "$ADB" shell "systemctl is-active guitar.service" | tr -d '\r'
  "$ADB" shell "journalctl -u guitar.service --no-pager -n 400 2>/dev/null | grep -aE '\[audio\]|麦克风诊断|麦克风电平|唤醒监听流|系统就绪|Traceback|ERROR' | tail -30" | tr -d '\r'
  ;;
*)
  echo "用法: bash deploy_micgain.sh {deploy|verify}"
  ;;
esac
