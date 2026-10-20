#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""装机角度滑动条离线预览：把新写的彩屏 show_volume_slider 出成 PNG 肉眼验收。

不碰真实 SPI、不动琴。跑在 PC 上（用项目 venv 的 PIL + numpy）。

PC：  <repo>/.venv/Scripts/python.exe preview_home_angle.py
产物：_preview/home_angle_<theme>.png
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("GUITAR_REPO", "E:/音乐/拨云_开源参考/liguanxi_Automatic_guitar")
_CANDS = [os.path.join(REPO, "src", "guitarist"), HERE]
SRC = None
for c in _CANDS:
    if os.path.isfile(os.path.join(c, "song_library.py")):
        SRC = os.path.abspath(c)
        break
if SRC is None:
    print("找不到源码目录:", _CANDS, flush=True)
    sys.exit(2)
sys.path.insert(0, SRC)

try:
    import hardware          # noqa: F401
    print("[preview] hardware = real", flush=True)
except Exception:
    import threading
    import types
    m = types.ModuleType("hardware")
    m.i2c_lock = threading.Lock()
    m.fonts = None
    m.USE_LCD = True
    m.up_button = m.down_button = m.set_button = m.esc_button = None
    sys.modules["hardware"] = m
    print("[preview] hardware = stub", flush=True)

import numpy as np                                       # noqa: E402
from PIL import Image, ImageFont                          # noqa: E402

CJK = None
for p in ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
          "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
          "C:/Windows/Fonts/simsun.ttc"):
    if os.path.exists(p):
        CJK = p
        break
if CJK is None:
    print("没有中文字体，出图会成方块", flush=True)

import lcd_renderer as LR          # noqa: E402
import theme as _th                # noqa: E402

if CJK:
    LR.LCDRenderer._CJK = CJK

OUT = os.environ.get("PREVIEW_OUT", os.path.join(HERE, "_preview"))
os.makedirs(OUT, exist_ok=True)

fonts = {
    "font": ImageFont.truetype(CJK, 16),
    "font_small": ImageFont.truetype(CJK, 12),
    "font_tiny": ImageFont.truetype(CJK, 10),
}


class FakeDisp:
    w = h = 240

    def __init__(self):
        self.buf = None
        self.frames = 0

    def write_frame(self, b):
        self.buf = bytes(b)
        self.frames += 1

    def fill_rgb565(self, rgb):
        hi, lo = (rgb >> 8) & 0xFF, rgb & 0xFF
        self.write_frame(bytes((hi, lo)) * (self.w * self.h))


def save_png(buf, path, scale=2):
    a = np.frombuffer(buf, dtype=">u2").reshape(240, 240)
    r = (((a >> 11) & 0x1F).astype(np.uint16) * 255 // 31).astype(np.uint8)
    g = (((a >> 5) & 0x3F).astype(np.uint16) * 255 // 63).astype(np.uint8)
    b = ((a & 0x1F).astype(np.uint16) * 255 // 31).astype(np.uint8)
    Image.fromarray(np.dstack([r, g, b])).resize(
        (240 * scale, 240 * scale), Image.NEAREST).save(path)


AMIN, AMAX = 60.0, 120.0


def pct(a):
    return int(round((a - AMIN) / (AMAX - AMIN) * 100))


def main():
    fake = FakeDisp()
    r = LR.LCDRenderer(fonts, oled=fake)

    cases = [
        ("wave", 90, "默认（音浪）"),
        ("cyber", 108, "赛博霓虹 · 大角度"),
        ("ocean", 78, "深海海浪"),
        ("crt", 90, "琥珀 CRT"),
    ]
    for key, ang, label in cases:
        _th.set_current(key, persist=False)
        fake.frames = 0
        t0 = time.time()
        r.show_volume_slider("装机角度（和弦收缩位）", pct(ang),
                             value_text="%d°" % ang,
                             hint="上/下:±1°  SET:保存  长按SET:取消")
        dt = (time.time() - t0) * 1000.0
        p = os.path.join(OUT, "home_angle_%s.png" % key)
        save_png(fake.buf, p)
        assert fake.frames == 1, "应单帧上屏，实际 %d" % fake.frames
        print("[%s] %-12s 角度=%d° 单帧=%.1fms -> %s" % (key, label, ang, dt, p),
              flush=True)

    _th.set_current(_th.DEFAULT_THEME, persist=False)
    print("[preview] done -> %s/home_angle_*.png" % OUT, flush=True)


if __name__ == "__main__":
    main()
