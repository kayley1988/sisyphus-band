#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""演奏主界面离线预览（不碰真实 SPI、不动琴）。

验三件事：
  ① screen_functions.play_song 走的那条路（LCDRenderer.show_progress）
     现在应该画出 240×240 「音浪 + 大字歌名」演奏主界面，而不是 128×64 小字；
  ② 1-bit 老版式页面（show_message 提示）不再是「紫字 + 桃红底」；
  ③ 帧底色统计（证明桃红已消失）。

PC：  <repo>/.venv/Scripts/python.exe preview_play.py
产物：_preview/play_*.png
"""
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("GUITAR_REPO", "E:/音乐/拨云_开源参考/liguanxi_Automatic_guitar")
_CANDS = [
    os.path.join(REPO, "src", "guitarist"),
    os.path.join(HERE, "..", "src", "guitarist"),
    os.path.join(HERE, "src", "guitarist"),
    HERE,
]
SRC = None
for cand in _CANDS:
    if os.path.isfile(os.path.join(cand, "song_library.py")):
        SRC = os.path.abspath(cand)
        break
if SRC is None:
    print("找不到源码目录，试过: %s" % _CANDS, flush=True)
    sys.exit(2)
sys.path.insert(0, SRC)


def _stub_hardware_if_needed():
    try:
        import hardware  # noqa: F401
        return "real"
    except Exception:
        pass
    import threading
    import types
    m = types.ModuleType("hardware")
    m.i2c_lock = threading.Lock()
    m.fonts = None
    m.USE_LCD = True
    m.up_button = m.down_button = m.set_button = m.esc_button = None
    sys.modules["hardware"] = m
    return "stub"


print("[preview] hardware = %s" % _stub_hardware_if_needed(), flush=True)

import numpy as np
from PIL import Image, ImageFont

CJK = None
for p in ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
          "C:/Windows/Fonts/msyh.ttc",
          "C:/Windows/Fonts/simhei.ttf",
          "C:/Windows/Fonts/simsun.ttc"):
    if os.path.exists(p):
        CJK = p
        break

import lcd_renderer as LR          # noqa: E402
import perform_timeline as PTL    # noqa: E402

if CJK:
    LR.LCDRenderer._CJK = CJK

OUT = os.environ.get("PREVIEW_OUT",
                     "/tmp" if os.name != "nt" else os.path.join(HERE, "_preview"))
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
    im = Image.fromarray(np.dstack([r, g, b]))
    im.resize((240 * scale, 240 * scale), Image.NEAREST).save(path)


def top_colors(buf, k=3):
    a = np.frombuffer(buf, dtype=">u2").reshape(240, 240)
    r = (((a >> 11) & 0x1F).astype(np.uint16) * 255 // 31).flatten().tolist()
    g = (((a >> 5) & 0x3F).astype(np.uint16) * 255 // 63).flatten().tolist()
    b = ((a & 0x1F).astype(np.uint16) * 255 // 31).flatten().tolist()
    return Counter(zip(r, g, b)).most_common(k)


def make_timeline(song="稻香"):
    """合成一条时间轴（60 音符 / 30s），只为看版式。"""
    chords = ["C", "G", "Am", "F", "Dm", "Em", "C", "G"]
    tl, t = [], 0.0
    for i in range(60):
        frets = [0 if j % 3 else (i % 4) + 1 for j in range(6)]
        if i % 7 == 0:
            frets = [(i + j) % 5 for j in range(6)]
        name = chords[(i // 7) % len(chords)]
        pressed = sum(1 for f in frets if f > 0)
        tl.append({
            "t_ms": t,
            "frets": list(frets),
            "name": name,
            "chord": name if i % 7 == 0 else None,
            "level": min(1.0, 0.35 + 0.65 * pressed / 6.0 + (0.15 if i % 4 == 0 else 0)),
            "strum": (i % 4 == 0),
        })
        t += 500.0
    return PTL.Timeline(song, tl, t, 120, 4)


def main():
    import state as _st

    print("[preview] CJK =", CJK, flush=True)

    # ① 演奏主界面：把时间轴塞进 state，再调 show_progress（= play_song 的调用）
    fake = FakeDisp()
    r = LR.LCDRenderer(fonts, oled=fake)
    _st.update_state("perform_timeline", make_timeline("稻香"))
    r.show_progress("稻香", 32)
    save_png(fake.buf, os.path.join(OUT, "play_perform.png"))
    print("[1] play_perform  top colors:", top_colors(fake.buf),
          "| 上屏帧数 =", fake.frames, "(单帧=不闪)", flush=True)

    # ② 无时间轴 → 兜底彩色进度条
    _st.update_state("perform_timeline", None)
    fake.frames = 0
    r.show_progress("稻香", 48)
    save_png(fake.buf, os.path.join(OUT, "play_fallback.png"))
    print("[2] play_fallback top colors:", top_colors(fake.buf),
          "| 上屏帧数 =", fake.frames, flush=True)

    # ③ 1-bit 老版式页面（提示）：应是白字黑底，不是桃红
    fake.frames = 0
    r.show_message(["正在切换演奏模式...", "全自动 → 人机配合"], duration=0)
    save_png(fake.buf, os.path.join(OUT, "play_legacy_msg.png"))
    print("[3] legacy message top colors:", top_colors(fake.buf), flush=True)
    print("[preview] done -> %s/play_{perform,fallback,legacy_msg}.png" % OUT, flush=True)


if __name__ == "__main__":
    main()
