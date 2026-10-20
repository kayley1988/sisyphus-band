#!/usr/bin/env bash
#
# build-rk3566.sh - 交叉编译到泰山派 RK3566
#
# 前置条件:
#   1. 安装 aarch64 交叉编译工具链
#   2. 准备 ALSA 的 aarch64 版本库和头文件(见下方说明)
#
# 关于 ALSA 交叉编译:
#   板子上虽然有 libasound.so.2, 但编译需要 .so 符号链接和头文件。
#   两个办法:
#     A. 从板子 /usr/lib/aarch64-linux-gnu/ 拷 libasound.so.2 过来,
#        手动建符号链接 ln -s libasound.so.2 libasound.so
#     B. 用泰山派 SDK 里的 sysroot(推荐, 最省事)
#
set -e

cd "$(dirname "$0")/.."
ROOT=$(pwd)

# ---------------- 配置区(按你的环境修改) ----------------
CROSS_PREFIX="${CROSS_PREFIX:-aarch64-linux-gnu-}"
# 如果泰山派 SDK 提供了 sysroot, 指向它, 会省掉手动准备 ALSA 的麻烦
SYSROOT="${SYSROOT:-}"
# 手动指定 ALSA 路径(没有 sysroot 时用)
ALSA_INC="${ALSA_INC:-}"
ALSA_LIB="${ALSA_LIB:-}"
# RKNN 路径(可选, 启用 NPU 推理时需要)
RKNN_DIR="${RKNN_DIR:-}"
# --------------------------------------------------------

BUILD="$ROOT/build-rk3566"

echo "======================================"
echo " 交叉编译目标: RK3566 (aarch64)"
echo "======================================"

# 检查工具链
if ! command -v "${CROSS_PREFIX}gcc" > /dev/null 2>&1; then
    echo "错误: 找不到 ${CROSS_PREFIX}gcc" >&2
    echo
    echo "安装工具链:"
    echo "  sudo apt install gcc-aarch64-linux-gnu g++-aarch64-linux-gnu"
    echo
    echo "或者用泰山派 SDK 里的工具链, 然后:"
    echo "  export CROSS_PREFIX=/path/to/toolchain/bin/aarch64-linux-gnu-"
    exit 1
fi

echo "工具链: $(${CROSS_PREFIX}gcc --version | head -1)"

mkdir -p "$BUILD"
cd "$BUILD"

CMAKE_ARGS=(
    -DCMAKE_SYSTEM_NAME=Linux
    -DCMAKE_SYSTEM_PROCESSOR=aarch64
    -DCMAKE_C_COMPILER="${CROSS_PREFIX}gcc"
    -DCMAKE_BUILD_TYPE=Release
    -DGA_TARGET_RK3566=ON
)

if [ -n "$SYSROOT" ]; then
    echo "使用 sysroot: $SYSROOT"
    CMAKE_ARGS+=(
        -DCMAKE_SYSROOT="$SYSROOT"
        -DCMAKE_FIND_ROOT_PATH="$SYSROOT"
    )
fi

if [ -n "$ALSA_INC" ]; then
    CMAKE_ARGS+=(-DALSA_INCLUDE_DIR="$ALSA_INC")
fi
if [ -n "$ALSA_LIB" ]; then
    CMAKE_ARGS+=(-DALSA_LIBRARY="$ALSA_LIB")
fi

if [ -n "$RKNN_DIR" ]; then
    echo "启用 RKNN: $RKNN_DIR"
    CMAKE_ARGS+=(
        -DGA_HAVE_RKNN=ON
        -DRKNN_INCLUDE_DIR="$RKNN_DIR/include"
        -DRKNN_LIB_DIR="$RKNN_DIR/lib"
    )
fi

echo
echo "配置 cmake..."
cmake "${CMAKE_ARGS[@]}" .. > /dev/null

echo "编译中(用了 -j$(nproc))..."
make -j"$(nproc)" 2>&1 | grep -E "error|warning|Error" || true
make -j"$(nproc)" > /dev/null 2>&1 || {
    echo "编译失败, 重跑一次看详细错误:"
    make
    exit 1
}

echo
echo "编译完成。产物:"
for f in guitar_assist guitar_assist_ui tuner_demo mech_selftest \
         test_yin test_chord test_chroma test_recog test_feedback dev_scan; do
    if [ -f "$f" ]; then
        printf "  %-18s %s\n" "$f" "$(file "$f" | cut -d, -f1-2)"
    fi
done

echo
echo "部署到板子(假设板子 IP 192.168.1.100):"
echo "  scp guitar_assist_ui mech_selftest dev_scan root@192.168.1.100:/root/guitar/"
echo
echo "板子上运行(带屏版, 推荐第一步先点屏):"
echo "  ./guitar_assist_ui --test-display          # 红绿蓝白黑整屏, 确认屏通了"
echo "  ./guitar_assist_ui                         # 全默认接线跑完整界面"
echo "  ./guitar_assist_ui --no-mech --no-audio    # 只看界面(不接舵机/声卡)"
echo "  ./guitar_assist_ui -h                      # 全部参数(接线可按实物覆盖)"
echo
echo "命令行版(无屏调试用):"
echo "  ./dev_scan                    # 先找采集设备号"
echo "  ./mech_selftest               # 机构驱动自检(离线, 不碰硬件)"
echo "  ./mech_selftest /dev/ttyS3    # 真发一帧, 看机构是否动作"
echo "  ./guitar_assist tuner hw:1,0  # 串口终端里调音"
