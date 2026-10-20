# -*- coding: utf-8 -*-
"""开机图金鱼换装：矢量金鱼 → 油画「金鱼吐泡泡」位图（base64 内嵌）。

做三件事：
  1. fish_cut_tight.png → 260×224 PNG8 → base64（最终素材）
  2. lcd_renderer.py 三段替换：
     A1 删 _F_* 矢量配色常量
     A2 删 _bez/_chain/_fish_parts，插入 _BOOT_FISH_B64 + _boot_fish()
     B  重写 show_boot_screen 的鱼绘制段：贴位图 + 动态泡泡
  3. py_compile 校验
"""
import base64
import io
import os
import py_compile
import re

PREV = r"E:\音乐\档3实现\_preview"
LCD = r"E:\音乐\拨云_开源参考\liguanxi_Automatic_guitar\src\guitarist\lcd_renderer.py"
BAK = LCD + ".bak_1011_fishbmp"

from PIL import Image  # noqa: E402

# ── 1. 最终素材 ──
im = Image.open(os.path.join(PREV, "fish_cut_tight.png"))
fish = im.resize((260, 224), Image.LANCZOS).quantize(colors=255, method=Image.FASTOCTREE)
buf = io.BytesIO()
fish.save(buf, "PNG", optimize=True)
png_bytes = buf.getvalue()
b64 = base64.b64encode(png_bytes).decode("ascii")
print("final png: %d bytes, b64: %d chars" % (len(png_bytes), len(b64)))
fish.save(os.path.join(PREV, "fish_final.png"), optimize=True)

# base64 拆行（每行 96 字符，8 空格缩进）
lines = [b64[i:i + 96] for i in range(0, len(b64), 96)]
b64_lit = "(\n" + "\n".join('        "%s"' % l for l in lines) + "\n    )"

# ── 2. 三段替换 ──
src = open(LCD, "r", encoding="utf-8").read()
if not os.path.exists(BAK):
    open(BAK, "w", encoding="utf-8").write(src)
    print("backup ->", BAK)


def cut(text, start, end, repl, tag):
    i = text.find(start)
    j = text.find(end, i + len(start)) if i >= 0 else -1
    assert i >= 0 and j >= 0, "锚点未命中: %s" % tag
    print("[%s] 替换 %d 字符" % (tag, j - i))
    return text[:i] + repl + text[j:]


# A1：删 _F_* 配色（保留文字/进度条颜色）
src = cut(src,
          "    # 金鱼配色（RGB / RGBA）",
          "    # 文字 / 进度条",
          "", "A1")

# A2：删矢量工具 + fish_parts，插位图加载器
fish_code = '''    # ── 金鱼位图（2026-10-11 换装：矢量金鱼 → 油画「金鱼吐泡泡」抠图）──
    # 来源：Z 提供的油画《金鱼缸》，抠出右上主鱼（HSV 橙色分割 + 最大连通域 + 羽化）。
    # PNG8 260×224（2x 超采样尺度，屏上显示约 130×112）；base64 内嵌，不依赖外部文件。
    _BOOT_MOUTH = (0.088, 0.452)               # 鱼嘴在位图内的相对位置（泡泡起点）
    _BOOT_FISH_B64 = %s

    @classmethod
    def _boot_fish(cls):
        """解码并缓存金鱼位图（RGBA）。"""
        cache = cls.__dict__.get("_BOOT_FISH_CACHE")
        if cache is None:
            raw = base64.b64decode(cls._BOOT_FISH_B64)
            cache = Image.open(io.BytesIO(raw)).convert("RGBA")
            cls._BOOT_FISH_CACHE = cache
        return cache

''' % b64_lit
src = cut(src,
          "    # ── 曲线工具（金鱼的有机轮廓全靠贝塞尔拼）──",
          "    @classmethod\n    def _boot_base",
          fish_code, "A2")

# B：show_boot_screen 鱼绘制段
draw_code = '''        cx, cy = 120.0 * ss, 110.0 * ss            # 金鱼视觉中心（2x 尺度）
        img = self._boot_base(W, H).copy().convert("RGBA")

        # ── ① 金鱼位图（油画「金鱼吐泡泡」抠图）+ 轻微上下浮动（呼吸感）──
        fish = self._boot_fish()
        fw, fh = fish.size
        fx = int(cx - fw / 2)
        fy = int(cy - fh / 2 + math.sin(ph * 2 * math.pi) * 6.0)
        img.alpha_composite(fish, (fx, fy))

        # ── ② 气泡：从鱼嘴往左上方升，渐大渐隐，循环往复 ──
        ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        od = ImageDraw.Draw(ov)
        w_thin = max(1, int(0.9 * ss))
        mx = fx + fw * self._BOOT_MOUTH[0]
        my = fy + fh * self._BOOT_MOUTH[1]
        for spd, r0, p0, drift in ((0.55, 5.0, 0.00, -14.0),
                                   (0.75, 3.8, 0.42, -28.0),
                                   (0.45, 3.0, 0.74, -6.0)):
            t = (ph * spd + p0) % 1.0
            a = int(160 * (1.0 - t) ** 0.8)
            if a <= 6:
                continue
            bp_x = mx + drift * t + math.sin(t * 6.283 + p0 * 7.0) * 4.0
            bp_y = my - t * 74.0
            rp = r0 * ss * (0.75 + 0.55 * t)
            od.ellipse([bp_x - rp, bp_y - rp, bp_x + rp, bp_y + rp],
                       fill=(150, 214, 240, a // 3), outline=(196, 232, 252, a),
                       width=w_thin)
        img = Image.alpha_composite(img, ov)
        d = ImageDraw.Draw(img)

'''
src = cut(src,
          "        parts = self._fish_parts()",
          "        # ── ④ 琴名「JY-演奏」──",
          draw_code, "B")

# 顶部 import base64/io（若无）
if "import base64" not in src:
    src = src.replace("import math", "import base64\nimport io\nimport math", 1)
    print("imports added")

open(LCD, "w", encoding="utf-8").write(src)
print("written:", LCD)

py_compile.compile(LCD, doraise=True)
print("py_compile OK")

# 快速自检：b64 能否解码回图
chk = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGBA")
print("embedded fish decode:", chk.size, chk.mode)
