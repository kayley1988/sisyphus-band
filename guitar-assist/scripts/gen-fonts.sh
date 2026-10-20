#!/usr/bin/env bash
# gen-fonts.sh - 一键重新生成全部点阵字库
#
# 什么时候需要跑这个:
#   1. 界面上要显示新的汉字 —— 先加到 tools/gen_font.py 的 DEFAULT_CHARS, 再跑本脚本
#   2. 觉得某个字号看着不舒服 —— 改下面的 SIZE_xx 再跑
#   3. 换了字体(比如想用思源黑体) —— 改 CJK_FONT / ASCII_FONT 再跑
#
# 生成结果直接写进 src/ui/font/, 是项目的一部分, 要提交到 git。
# (不放进构建流程是因为: 生成的步骤依赖字体文件和 Python 的 Pillow,
#  而目标板编译环境不该有这些依赖。所以"生成一次, 提交进去"。)
#
# 依赖: python3 + Pillow  (sudo pip3 install pillow)
# 字体: Debian/Ubuntu: sudo apt install fonts-noto-cjk fonts-dejavu-core
set -euo pipefail

cd "$(dirname "$0")/.."
OUT_DIR="src/ui/font"

# ---- 字体路径 ----
# CJK 字体是 .ttc(字体集合), 里面第 0 个通常是简体中文。
# 如果找不到, 脚本会报错并提示装包, 不会静默生成空字库。
CJK_CANDIDATES=(
    /usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc
    /usr/share/fonts/truetype/noto-cjk/NotoSansCJK-Regular.ttc
    /usr/share/fonts/truetype/wqy/wqy-microhei.ttc
    /usr/share/fonts/truetype/wqy/wqy-zenhei.ttc
)
ASCII_CANDIDATES=(
    /usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf
    /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf
)

pick() {
    for f in "$@"; do
        [ -f "$f" ] && { echo "$f"; return 0; }
    done
    return 1
}

CJK_FONT="$(pick "${CJK_CANDIDATES[@]}")" || {
    echo "错误: 找不到中文字体。请安装:" >&2
    echo "  sudo apt install fonts-noto-cjk" >&2
    exit 1
}
ASCII_FONT="$(pick "${ASCII_CANDIDATES[@]}")" || {
    echo "错误: 找不到 ASCII 字体。请安装: sudo apt install fonts-dejavu-core" >&2
    exit 1
}

echo "[gen-fonts] CJK  : $CJK_FONT"
echo "[gen-fonts] ASCII: $ASCII_FONT"
mkdir -p "$OUT_DIR"

PY="python3 tools/gen_font.py"

# ---- ASCII 字库 ----
# 前缀必须唯一! 否则同一程序 include 多个字库会符号冲突。
# 数值必须和 src/ui/ui_draw.c 里 g_fonts[] 的顺序一致。
$PY --font "$ASCII_FONT" --ascii --size 10 --prefix f10 \
    --out "$OUT_DIR/font_num10.h"
$PY --font "$ASCII_FONT" --ascii --size 16 --prefix f16 \
    --out "$OUT_DIR/font_num16.h"
$PY --font "$ASCII_FONT" --ascii --size 32 --prefix f32 \
    --out "$OUT_DIR/font_num32.h"

# ---- 中文字库(只含 DEFAULT_CHARS 里的字) ----
$PY --font "$CJK_FONT" --size 12 --prefix cn12 \
    --out "$OUT_DIR/font_cn12.h"
$PY --font "$CJK_FONT" --size 16 --prefix cn16 \
    --out "$OUT_DIR/font_cn16.h"

echo "[gen-fonts] 完成, 输出到 $OUT_DIR/"
ls -la "$OUT_DIR"
