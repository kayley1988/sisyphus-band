#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地离线预览：把彩屏演奏界面渲成 PNG（PC 上验版式，不占用板子）。

用法:  python preview_perform.py [起始秒...]
"""
import os
import sys

GTR = r"E:\音乐\拨云_开源参考\liguanxi_Automatic_guitar"
HERE = os.path.dirname(os.path.abspath(__file__))
# ⚠️ 顺序要紧：src/guitarist 必须排最前，否则会被 档3实现/ 下的旧 lcd_renderer.py 挡住
sys.path.insert(0, r"E:\音乐\档3实现")
sys.path.insert(0, os.path.join(GTR, "src", "guitarist"))
os.environ["GUITAR_USE_LCD"] = "1"

import numpy as np
from PIL import Image, ImageFont

# Windows 无 fcntl（Linux 专有）—— 预览时打桩，不碰真实 SPI
if sys.platform != "linux":
    import types
    _m = types.ModuleType("fcntl")
    _m.ioctl = lambda *a, **k: 0
    _m.flock = lambda *a, **k: 0
    sys.modules.setdefault("fcntl", _m)

import lcd_renderer as LR

LR.LCDRenderer._CJK = r"C:\Windows\Fonts\msyh.ttc"      # 预览用微软雅黑

import auto_play_show as APS
APS.CHORD_DEFS = os.path.join(HERE, "_preview", "chord_definitions.csv")

from servo_controller import ServoController

WINDOW_MS = 7000.0
HEAD_X = 26


class FakeDisp:
    w, h = 240, 240

    def __init__(self):
        self.buf = None

    def write_frame(self, b):
        self.buf = bytes(b)


def save_png(buf, path):
    a = np.frombuffer(buf, dtype=">u2").reshape(240, 240)
    r = (((a >> 11) & 0x1F).astype(np.uint16) * 255 // 31).astype(np.uint8)
    g = (((a >> 5) & 0x3F).astype(np.uint16) * 255 // 63).astype(np.uint8)
    b = ((a & 0x1F).astype(np.uint16) * 255 // 31).astype(np.uint8)
    img = Image.fromarray(np.dstack([r, g, b]))
    img.resize((480, 480), Image.NEAREST).save(path)
    print("  ->", path)


def main():
    sc = ServoController(os.path.join(GTR, "config", "servo_config.csv"),
                         send_init_commands=False)
    tl, total_ms, bpm, ts = APS._load_timeline(
        os.path.join(HERE, "_preview", "daoxiang.csv"), sc)
    n_chord = sum(1 for e in tl if e["chord"])
    print("音符 %d / 时长 %.0fs / BPM %s / 拍号 %s / 和弦变化 %d"
          % (len(tl), total_ms / 1000.0, bpm, ts, n_chord))

    fpath = LR.LCDRenderer._CJK
    fonts = {"font": ImageFont.truetype(fpath, 16),
             "font_small": ImageFont.truetype(fpath, 12),
             "font_tiny": ImageFont.truetype(fpath, 10)}
    fake = FakeDisp()
    r = LR.LCDRenderer(fonts, oled=fake)

    secs = [float(x) for x in sys.argv[1:]] or [15, 60, 130]
    for t_sec in secs:
        t_ms = t_sec * 1000.0
        events = []
        for e in tl:
            dt = e["t_ms"] - t_ms
            if dt < -WINDOW_MS * 0.15 or dt > WINDOW_MS:
                continue
            events.append({
                "x": HEAD_X + dt / WINDOW_MS * (240 - HEAD_X),
                "level": e["level"],
                "strum": e["strum"],
                "chord": e["chord"],
            })
        cur = APS._pick_current(tl, t_ms)
        pct = t_ms / total_ms * 100.0
        print("t=%ds  %d 事件  当前和弦=%s  品位=%s"
              % (t_sec, len(events), cur["name"] if cur else "-",
                 cur["frets"] if cur else "-"))
        r.show_perform_panel(
            "稻香", pct, events,
            chord_now=(cur["name"] if cur and cur["name"] else "—"),
            frets_now=(cur["frets"] if cur else [0] * 6),
            head_x=HEAD_X)
        save_png(fake.buf, os.path.join(HERE, "_preview", "frame_%03d.png" % int(t_sec)))


if __name__ == "__main__":
    main()
