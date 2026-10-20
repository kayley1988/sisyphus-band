#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""界面模式离线预览：把 4 套模式各出成一张 240×240 PNG，肉眼验收。

不碰真实 SPI、不动琴。跑在 PC 上（用项目 venv 的 PIL + numpy）。

PC：  <repo>/.venv/Scripts/python.exe preview_theme.py
产物：_preview/theme_{wave,cyber,ocean,crt}.png（海浪多出一帧看流动）
"""
import os
import sys
import time
from collections import Counter

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
import perform_timeline as PTL     # noqa: E402
import state as _st                # noqa: E402
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


def top_colors(buf, k=3):
    a = np.frombuffer(buf, dtype=">u2").reshape(240, 240)
    r = (((a >> 11) & 0x1F).astype(np.uint16) * 255 // 31).flatten().tolist()
    g = (((a >> 5) & 0x3F).astype(np.uint16) * 255 // 63).flatten().tolist()
    b = ((a & 0x1F).astype(np.uint16) * 255 // 31).flatten().tolist()
    return Counter(zip(r, g, b)).most_common(k)


def make_timeline(song="稻香"):
    chords = ["C", "G", "Am", "F", "Dm", "Em", "C", "G"]
    tl, t = [], 0.0
    for i in range(60):
        frets = [0 if j % 3 else (i % 4) + 1 for j in range(6)]
        if i % 7 == 0:
            frets = [(i + j) % 5 for j in range(6)]
        name = chords[(i // 7) % len(chords)]
        pressed = sum(1 for f in frets if f > 0)
        tl.append({
            "t_ms": t, "frets": list(frets), "name": name,
            "chord": name if i % 7 == 0 else None,
            "level": min(1.0, 0.35 + 0.65 * pressed / 6.0 + (0.15 if i % 4 == 0 else 0)),
            "strum": (i % 4 == 0),
        })
        t += 500.0
    return PTL.Timeline(song, tl, t, 120, 4)


def main():
    print("[preview] CJK =", CJK, flush=True)

    # 渲染层的旧色回退表必须都能在类上找到（防手滑写错常量名）
    for logical, attr in LR.LCDRenderer._THEME_FALLBACK.items():
        assert hasattr(LR.LCDRenderer, attr), "回退常量缺失: %s -> %s" % (logical, attr)
    print("[0] 旧色回退表 12 项全部存在 ✓", flush=True)

    fake = FakeDisp()
    r = LR.LCDRenderer(fonts, oled=fake)
    _st.update_state("perform_timeline", make_timeline("稻香"))

    for key in _th.THEME_ORDER:
        _th.set_current(key, persist=False)          # 不落盘，别污染真实配置
        fake.frames = 0
        t0 = time.time()
        r.show_progress("稻香", 32)
        dt = (time.time() - t0) * 1000.0
        p = os.path.join(OUT, "theme_%s.png" % key)
        save_png(fake.buf, p)
        print("[%s] %-6s top=%-38s 帧数=%d 单帧=%.1fms -> %s"
              % (key, _th.theme_name(key), top_colors(fake.buf), fake.frames, dt, p),
              flush=True)

    # 音浪（合体版）多拍一帧：验证柱子确实在随浪起伏
    _th.set_current("wave", persist=False)
    fake.frames = 0
    r.show_progress("稻香", 32)
    bw1 = fake.buf
    time.sleep(0.42)
    r.show_progress("稻香", 32)
    bw2 = fake.buf
    dw = sum(1 for x, y in zip(bw1, bw2) if x != y)
    save_png(bw2, os.path.join(OUT, "theme_wave_t2.png"))
    print("[wave] 0.42s 前后两帧差异字节 = %d（>0 说明音浪在起伏流动）" % dw, flush=True)

    # 海浪多拍一帧：验证相位确实在走（两帧像素应有差异）
    _th.set_current("ocean", persist=False)
    fake.frames = 0
    r.show_progress("稻香", 32)
    b1 = fake.buf
    time.sleep(0.35)
    r.show_progress("稻香", 32)
    b2 = fake.buf
    diff = sum(1 for x, y in zip(b1, b2) if x != y)
    save_png(b2, os.path.join(OUT, "theme_ocean_t2.png"))
    print("[ocean] 0.35s 前后两帧差异字节 = %d（>0 说明浪在动）" % diff, flush=True)

    _th.set_current(_th.DEFAULT_THEME, persist=False)
    print("[preview] done -> %s/theme_*.png" % OUT, flush=True)


if __name__ == "__main__":
    main()
