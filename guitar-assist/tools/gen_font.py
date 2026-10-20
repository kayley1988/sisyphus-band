#!/usr/bin/env python3
"""
gen_font.py - 从 TTF 字体生成 C 点阵字库

为什么需要这个工具:
  嵌入式界面显示中文通常需要全字库(几 MB), 但我们的界面只用几十个汉字。
  用这个脚本"按需提取", 生成的 C 文件只有几十 KB, 直接编进程序,
  不需要在板子上装字体文件, 也不需要 FreeType。

用法:
  # 生成界面用到的汉字(默认字表)
  python3 tools/gen_font.py --out src/ui/font_cn.h

  # 从你自己的文本里提取用到的字
  python3 tools/gen_font.py --text "你的界面文案" --out src/ui/font_cn.h

  # 生成 ASCII 字库(数字/字母)
  python3 tools/gen_font.py --ascii --size 32 --out src/ui/font_num32.h

依赖: pip install pillow
"""

import argparse
import sys

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("需要 Pillow: pip install pillow", file=sys.stderr)
    sys.exit(1)


# 界面默认用到的汉字。按界面实际文案整理。
DEFAULT_CHARS = (
    "调音和弦模式退出准了偏高低紧弦松等待拨"
    "六五四三二一弦不弹空品小大七挂属度置信"
    "当前识别分数匹配库中无此指法音名频率自动"
    "锁定参考播报切换列表常用个共秒设备后端"
    "分辨率色深行字节已刷新次累计音量信号弱"
    "请重新拨弦错误初始化成功连接失败请检查"
    "松开按下旋转选择确认返回上一页下一页"
    # 界面文案补充(缺字会显示为空心方块, 加字后跑 scripts/gen-fonts.sh)
    "第根扫听到可继续扫！，。压待机准就绪备亮度"
)


def render_char(ch: str, font: ImageFont.FreeTypeFont, size: int):
    """把一个字符渲染成 1bit 点阵。
    返回 (bitmap_rows, width, height)，bitmap_rows 是每行的 byte 列表。

    用 1bit 是因为: 单色文字在 LCD 上完全够用, 而且存储省 8 倍。
    需要抗锯齿时改这里的阈值逻辑(用灰度+阈值抖动)。
    """
    # 先渲染到临时画布, 拿到实际 bbox
    img = Image.new("L", (size * 2, size * 2), 0)
    d = ImageDraw.Draw(img)
    d.text((size // 2, size // 2), ch, fill=255, font=font)
    bbox = img.getbbox()
    if bbox is None:
        # 空白字符(空格等): 返回"零行"。
        # 关键: h 必须是 0, 不能返回 size!
        # 否则渲染时会按 stride=0 走 size 行, 读到的是相邻字形的位图,
        # 屏幕上显示成一条乱码竖条。
        # 宽度给一个合理的空格宽度, 让它能正常推进光标。
        return [], max(4, size // 3), 0

    img = img.crop(bbox)
    w, h = img.size

    # 统一到固定高度, 宽度按实际(不等宽字符)
    rows = []
    px = img.load()
    for y in range(h):
        row_bytes = []
        byte = 0
        nbit = 0
        for x in range(w):
            bit = 1 if px[x, y] > 128 else 0
            byte = (byte << 1) | bit
            nbit += 1
            if nbit == 8:
                row_bytes.append(byte)
                byte = 0
                nbit = 0
        if nbit > 0:
            byte <<= (8 - nbit)   # 左对齐补齐
            row_bytes.append(byte)
        rows.append(row_bytes)
    return rows, w, h


def render_ascii(ch: str, font: ImageFont.FreeTypeFont, size: int):
    """ASCII 字符渲染, 逻辑同上但强制等宽(数字对齐好看)"""
    img = Image.new("L", (size * 2, size), 0)
    d = ImageDraw.Draw(img)
    # 用固定槽宽渲染, 保证数字等宽
    d.text((size // 2, 0), ch, fill=255, font=font)
    bbox = img.getbbox()
    if bbox is None:
        # 空白字符: 同 render_char, h 必须为 0(见那里的说明)
        return [], size // 2, 0

    img = img.crop((bbox[0], bbox[1], bbox[2], bbox[3]))
    w, h = img.size
    rows = []
    px = img.load()
    for y in range(h):
        row_bytes = []
        byte = 0
        nbit = 0
        for x in range(w):
            bit = 1 if px[x, y] > 128 else 0
            byte = (byte << 1) | bit
            nbit += 1
            if nbit == 8:
                row_bytes.append(byte)
                byte = 0
                nbit = 0
        if nbit > 0:
            byte <<= (8 - nbit)
            row_bytes.append(byte)
        rows.append(row_bytes)
    return rows, w, h


def emit_c_array(f, name: str, rows, w, h):
    """输出 C 数组"""
    total_bytes = sum(len(r) for r in rows)
    f.write(f"/* {name}: {w}x{h}, {total_bytes} bytes */\n")
    f.write(f"static const uint16_t {name}_w = {w};\n")
    f.write(f"static const uint16_t {name}_h = {h};\n")
    # 每行的字节数(用于跨行取数据)
    f.write(f"static const uint8_t {name}_stride = "
            f"{len(rows[0]) if rows else 0};\n")
    f.write(f"static const uint8_t {name}[] = {{\n    ")
    n = 0
    for r in rows:
        for b in r:
            f.write(f"0x{b:02X},")
            n += 1
            if n % 16 == 0:
                f.write("\n    ")
    f.write("\n};\n\n")


def main():
    ap = argparse.ArgumentParser(description="从 TTF 生成 C 点阵字库")
    ap.add_argument("--font", default="/usr/share/fonts/truetype/"
                    "noto-cjk/NotoSansCJK-Regular.ttc",
                    help="TTF/TTC 字体路径")
    ap.add_argument("--size", type=int, default=16, help="字号(像素高)")
    ap.add_argument("--out", required=True, help="输出的 C 头文件")
    ap.add_argument("--text", help="要提取的字符(默认用内置字表)")
    ap.add_argument("--ascii", action="store_true", help="生成 ASCII 字库")
    ap.add_argument("--prefix", default="ga_font",
                    help="符号前缀, 用于在同一程序里引入多个字库时避免重名")
    args = ap.parse_args()

    # 找可用字体(系统可能没有 Noto CJK)
    import os
    candidates = [
        args.font,
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    font_path = None
    for p in candidates:
        if os.path.exists(p):
            font_path = p
            break
    if not font_path:
        print("找不到可用字体。用 --font 指定, 或安装:", file=sys.stderr)
        print("  sudo apt install fonts-noto-cjk", file=sys.stderr)
        sys.exit(1)

    PFX = args.prefix
    print(f"[font] 使用: {font_path}, 字号 {args.size}, 前缀 {PFX}")

    try:
        font = ImageFont.truetype(font_path, args.size)
    except Exception as e:
        print(f"加载字体失败: {e}", file=sys.stderr)
        sys.exit(1)

    # 包含保护宏必须带前缀! 否则同一程序里 include 多个字库时,
    # 只有第一个会生效(后面全被 #ifndef 挡掉), 症状是"用了别的字号却报未定义符号"。
    GUARD = f"GA_FONT_{PFX.upper()}_H"

    with open(args.out, "w") as f:
        f.write(f"""/*
 * {os.path.basename(args.out)} - 自动生成的点阵字库
 *
 * 由 tools/gen_font.py 生成, 请勿手工修改。
 * 字体: {os.path.basename(font_path)}, 字号: {args.size}px
 *
 * 数据格式: 1bit/像素, 每行按字节左对齐, 高位在前。
 * 取位方法: bit = (row[y][x/8] >> (7 - x%8)) & 1
 */
#ifndef {GUARD}
#define {GUARD}

#include <stdint.h>

#ifndef GA_GLYPH_T_DEFINED
#define GA_GLYPH_T_DEFINED
typedef struct {{
    uint32_t code;          /* Unicode 码点 */
    uint16_t w, h;          /* 宽高 */
    uint8_t  stride;        /* 每行字节数 */
    uint16_t offset;        /* 在位图数组里的字节偏移 */
}} ga_glyph_t;
#endif

""")


        if args.ascii:
            chars = [chr(c) for c in range(32, 127)]
            glyphs = []
            blob = bytearray()
            for ch in chars:
                rows, w, h = render_ascii(ch, font, args.size)
                off = len(blob)
                for r in rows:
                    blob.extend(r)
                glyphs.append((ord(ch), w, h, rows[0].__len__() if rows else 0, off))

            f.write(f"static const uint8_t {PFX}_blob[] = {{\n    ")
            n = 0
            for b in blob:
                f.write(f"0x{b:02X},")
                n += 1
                if n % 16 == 0:
                    f.write("\n    ")
            f.write("\n};\n\n")
            f.write(f"static const ga_glyph_t {PFX}_table[] = {{\n")
            for code, w, h, stride, off in glyphs:
                ch_repr = repr(chr(code))
                f.write(f"    {{0x{code:04X}, {w}, {h}, {stride}, {off}}}, "
                        f"/* {ch_repr} */\n")
            f.write("};\n")
            f.write(f"static const int {PFX}_count = {len(glyphs)};\n")
        else:
            text = args.text if args.text else DEFAULT_CHARS
            # 去重并保持顺序
            seen = set()
            chars = []
            for ch in text:
                if ch not in seen and ch.strip():
                    seen.add(ch)
                    chars.append(ch)

            glyphs = []
            blob = bytearray()
            for ch in chars:
                rows, w, h = render_char(ch, font, args.size)
                off = len(blob)
                for r in rows:
                    blob.extend(r)
                glyphs.append((ord(ch), w, h,
                               len(rows[0]) if rows else 0, off))

            f.write(f"/* 共 {len(glyphs)} 个字形, {len(blob)} 字节位图 */\n")
            f.write(f"static const uint8_t {PFX}_blob[] = {{\n    ")
            n = 0
            for b in blob:
                f.write(f"0x{b:02X},")
                n += 1
                if n % 16 == 0:
                    f.write("\n    ")
            f.write("\n};\n\n")
            f.write(f"static const ga_glyph_t {PFX}_table[] = {{\n")
            for code, w, h, stride, off in glyphs:
                f.write(f"    {{0x{code:04X}, {w}, {h}, {stride}, {off}}}, "
                        f"/* {chr(code)} */\n")
            f.write("};\n")
            f.write(f"static const int {PFX}_count = {len(glyphs)};\n")

        f.write(f"\n#endif /* {GUARD} */\n")

    print(f"[font] 已生成 {args.out}")
    print(f"[font] 字形数: {len(glyphs)}, 位图大小: {len(blob)} 字节")


if __name__ == "__main__":
    import os
    main()
