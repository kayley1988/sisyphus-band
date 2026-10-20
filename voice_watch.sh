#!/bin/bash
# 实时语音事件看板 —— 对着板子说「小爱同学」，观察唤醒/识别事件（Ctrl+C 退出）
export MSYS2_ARG_CONV_EXCL="*" MSYS_NO_PATHCONV=1
ADB="E:/音乐/档3实现/adb_tools/platform-tools/adb.exe"
echo ">>> 现在对着板子说: 小爱同学    （Ctrl+C 退出）"
echo ">>> 唤醒成功后屏幕会变化，继续说指令，如: 弹一首歌"
echo
"$ADB" shell "journalctl -u guitar.service -f -n 0" \
  | grep --line-buffered -aE "唤醒词|检测到|识别|Vosk|语音|播放|歌曲|没有听到|音频流|电平|\[AI\]"
