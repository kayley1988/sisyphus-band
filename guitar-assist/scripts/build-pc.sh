#!/usr/bin/env bash
#
# build-pc.sh - 在 PC (x86_64) 上构建, 用于算法验证
#
# 这是开发流程的第一步: 所有算法先在 PC 上跑通, 再交叉编译到板子。
# 因为 PC 上调试方便(printf/gdb/valgrind 都能用), 而板子上只能串口看日志。
#
set -e

cd "$(dirname "$0")/.."
ROOT=$(pwd)
BUILD="$ROOT/build-pc"

echo "==> 构建目录: $BUILD"
mkdir -p "$BUILD"
cd "$BUILD"

cmake -DCMAKE_BUILD_TYPE=Release .. > /dev/null
make -j"$(nproc)"

echo
echo "==> 构建完成, 产物在 $BUILD/"
ls -1 tuner_demo test_yin test_chord 2>/dev/null | sed 's/^/    /'

echo
echo "==> 运行自测"
./test_yin | tail -5
echo
echo "下一步:"
echo "  实时调音:  $BUILD/tuner_demo live hw:1,0"
echo "  离线分析:  $BUILD/tuner_demo file your_guitar.wav"
echo "  设备扫描:  $BUILD/dev_scan"
