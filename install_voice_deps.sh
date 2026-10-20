#!/bin/sh
# 无障碍吉他改造 —— 在泰山派上装「语音点歌」所需依赖
# 背景：pypi.org 直连只有 ~100kB/s 且会截断；清华源对包文件 403；
#      腾讯云源可用但不稳。故对大包改用 curl 断点续传。
set -x

MIRROR=https://mirrors.cloud.tencent.com/pypi/simple
PKG=https://mirrors.cloud.tencent.com/pypi/packages
W=/tmp/sherpa_onnx-1.13.8-cp310-cp310-manylinux2014_aarch64.manylinux_2_17_aarch64.whl
URL=$PKG/3b/a6/a737e37411ff653a12570447964fb9ab98285f3520e32d725a60364eb7b1/sherpa_onnx-1.13.8-cp310-cp310-manylinux2014_aarch64.manylinux_2_17_aarch64.whl

echo "=== 1/2 断点续传拉 sherpa-onnx 轮子（4.2MB）==="
for i in 1 2 3 4 5 6 7 8 9 10; do
    curl -L --retry 5 --retry-delay 3 --connect-timeout 20 -C - -o "$W" "$URL" && break
    echo "第 $i 次未完成，重试…"
    sleep 2
done
ls -la "$W"

echo "=== 2/2 安装全部依赖 ==="
pip3 install --no-input --timeout 60 --retries 10 -i "$MIRROR" \
    sounddevice edge-tts vosk webrtcvad "$W"

echo "=== 结果 ==="
pip3 list 2>/dev/null | grep -iE 'sounddevice|vosk|sherpa|webrtc|edge'
echo "DONE"
