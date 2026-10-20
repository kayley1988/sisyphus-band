#!/bin/bash
# TTS 引擎接入的部署脚本：拉板端版本 diff -> 备份 -> 推送 -> md5 校验 -> 板上编译
export MSYS2_ARG_CONV_EXCL="*" MSYS_NO_PATHCONV=1
ADB="E:/音乐/档3实现/adb_tools/platform-tools/adb.exe"
SRC="E:/音乐/拨云_开源参考/liguanxi_Automatic_guitar/src/guitarist"
DST="/root/guitarist_app/src/guitarist"
TMP="E:/音乐/档3实现/_preview/board_1011d"
BAK=".bak_1011_tts"

FILES="ai/tts_engine.py ai/context.py ai/__init__.py screen_functions.py web_ui/templates/ai_settings.html"

case "$1" in
diff)
  mkdir -p "$TMP"
  echo "=== 拉板端版本并 diff（看一下差异是否只含本次改动）==="
  for f in $FILES; do
    mkdir -p "$TMP/$(dirname "$f")"
    "$ADB" pull "$DST/$f" "$TMP/$f" >/dev/null 2>&1
    if diff -q "$SRC/$f" "$TMP/$f" >/dev/null 2>&1; then
      echo "  [完全相同] $f"
    else
      echo "  [有差异] $f"
      diff "$TMP/$f" "$SRC/$f" | head -30
    fi
  done
  ;;
deploy)
  echo "=== 备份板端原文件 ==="
  for f in $FILES; do
    "$ADB" shell "cp $DST/$f $DST/$f$BAK" && echo "  backed: $f"
  done
  echo "=== 推送 ==="
  for f in $FILES; do
    "$ADB" push "$SRC/$f" "$DST/$f" 2>&1 | tail -1
  done
  echo "=== md5 校验 ==="
  for f in $FILES; do
    L=$(md5sum "$SRC/$f" | awk '{print $1}')
    B=$("$ADB" shell "md5sum $DST/$f | awk '{print \$1}'" | tr -d '\r')
    if [ "$L" = "$B" ]; then echo "  MATCH  $f"; else echo "  MISMATCH $f  local=$L board=$B"; fi
  done
  echo "=== 板上编译 ==="
  "$ADB" shell "cd $DST && python3 -m py_compile ai/tts_engine.py ai/context.py ai/__init__.py screen_functions.py && echo BOARD_COMPILE_OK"
  ;;
*)
  echo "用法: bash deploy_tts.sh {diff|deploy}"
  ;;
esac
