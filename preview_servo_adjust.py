#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""舵机调整屏离线预览：把新的「舵机调整」全屏界面渲染成 PNG 目检。

PC：  <repo>/.venv/Scripts/python.exe preview_servo_adjust.py
产物：_preview/servo_adj_{wave,cyber,ocean,crt}.png + servo_adj_wave_78.png
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("GUITAR_REPO", "E:/音乐/拨云_开源参考/liguanxi_Automatic_guitar")
SRC = None
for c in (os.path.join(REPO, "src", "guitarist"), HERE):
    if os.path.isfile(os.path.join(c, "song_library.py")):
        SRC = os.path.abspath(c)
        break
if SRC is None:
    print("找不到源码目录", flush=True)
    sys.exit(2)
sys.path.insert(0, SRC)

try:
    import hardware          # noqa: F401
except Exception:
    import threading
    import types
    m = types.ModuleType("hardware")
    m.i2c_lock = threading.Lock()
    m.fonts = None
    m.USE_LCD = True
    m.up_button = m.down_button = m.set_button = m.esc_button = None
    sys.modules["hardware"] = m

import numpy as np                                       # noqa: E402
from PIL import Image, ImageFont                          # noqa: E402

CJK = None
for p in ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
          "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf"):
    if os.path.exists(p):
        CJK = p
        break

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


def main():
    fake = FakeDisp()
    r = LR.LCDRenderer(fonts, oled=fake)
    for key in _th.THEME_ORDER:
        _th.set_current(key, persist=False)
        fake.frames = 0
        t0 = time.time()
        r.show_servo_adjust(92.0, 60.0, 120.0, 90.0)
        dt = (time.time() - t0) * 1000.0
        p = os.path.join(OUT, "servo_adj_%s.png" % key)
        save_png(fake.buf, p)
        print("[%s] %-6s 帧数=%d 单帧=%.1fms -> %s"
              % (key, _th.theme_name(key), fake.frames, dt, p), flush=True)

    # 偏离默认位的一帧（78°，验证 90° 白标线 + 滑块偏移）
    _th.set_current("wave", persist=False)
    r.show_servo_adjust(78.0, 60.0, 120.0, 90.0)
    save_png(fake.buf, os.path.join(OUT, "servo_adj_wave_78.png"))
    print("[wave] 78° 样例 -> servo_adj_wave_78.png", flush=True)

    _th.set_current(_th.DEFAULT_THEME, persist=False)
    print("[preview] done", flush=True)


if __name__ == "__main__":
    main()
