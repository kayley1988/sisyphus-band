#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""彩屏界面离线预览：把演奏主界面渲成 PNG（写 /tmp），**不碰真实 SPI**。

用途：改完版式先在板子上渲图 → adb pull 回来看，避免每轮都真演奏一遍。

用法:
    python3 preview_board.py            # 默认 t=15s / 60s / 130s
    python3 preview_board.py 5 40 200
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src", "guitarist"))
os.environ.setdefault("GUITAR_USE_LCD", "1")

import numpy as np
from PIL import Image
import lcd_renderer as LR
import auto_play_show as APS
from servo_controller import ServoController

CSV = os.path.join(HERE, "歌曲列表", "稻香", "图片源1", "输出结果.csv")
WINDOW_MS = 7000.0
HEAD_X = 26


class FakeDisp:
    """只收帧不发送 —— 让 LCDRenderer 以为在真屏上画。"""
    w = h = 240

    def __init__(self):
        self.buf = None

    def write_frame(self, b):
        self.buf = bytes(b)


def save_png(buf, path):
    a = np.frombuffer(buf, dtype=">u2").reshape(240, 240)
    r = (((a >> 11) & 0x1F).astype(np.uint16) * 255 // 31).astype(np.uint8)
    g = (((a >> 5) & 0x3F).astype(np.uint16) * 255 // 63).astype(np.uint8)
    b = ((a & 0x1F).astype(np.uint16) * 255 // 31).astype(np.uint8)
    Image.fromarray(np.dstack([r, g, b])).resize((480, 480), Image.NEAREST).save(path)


def main():
    fonts, src = APS._load_fonts()
    print("[preview] fonts =", src, flush=True)
    if not fonts:
        return 1
    sc = ServoController(os.path.join(HERE, "config", "servo_config.csv"),
                         send_init_commands=False)
    tl, total_ms, bpm, ts = APS._load_timeline(CSV, sc)
    print("[preview] %d 音符 / %.0fs / BPM %s / %s" % (len(tl), total_ms / 1000.0, bpm, ts),
          flush=True)

    fake = FakeDisp()
    r = LR.LCDRenderer(fonts, oled=fake)

    secs = [float(x) for x in sys.argv[1:]] or [15.0, 60.0, 130.0]
    for t in secs:
        t_ms = t * 1000.0
        evs = []
        for e in tl:
            dt = e["t_ms"] - t_ms
            if dt < -WINDOW_MS * 0.15 or dt > WINDOW_MS:
                continue
            evs.append({"x": HEAD_X + dt / WINDOW_MS * (240 - HEAD_X),
                        "level": e["level"], "strum": e["strum"], "chord": e["chord"]})
        cur = APS._pick_current(tl, t_ms)
        print("[preview] t=%ds  %d 事件  和弦=%s  品位=%s"
              % (t, len(evs), cur["name"] if cur else "-", cur["frets"] if cur else "-"),
              flush=True)
        r.show_perform_panel(
            "稻香", t_ms / total_ms * 100.0, evs,
            chord_now=((cur["name"] if cur and cur["name"] else "—")),
            frets_now=(cur["frets"] if cur else [0] * 6),
            head_x=HEAD_X)
        save_png(fake.buf, "/tmp/prev_%03d.png" % int(t))
    print("[preview] done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
