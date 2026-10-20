#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""板上自检：跑一遍预览 + 精测单帧耗时（FakeDisp，不碰真屏 / 不写 /root 以外磁盘）。"""
import time
import runpy

t0 = time.time()
runpy.run_path("/root/guitarist_app/preview_boot.py", run_name="__main__")
print("preview total %.2fs" % (time.time() - t0))

import lcd_renderer as LR  # noqa: E402
from PIL import ImageFont  # noqa: E402

CJK = LR.LCDRenderer._CJK
fonts = {"font": ImageFont.truetype(CJK, 16),
         "font_small": ImageFont.truetype(CJK, 12),
         "font_tiny": ImageFont.truetype(CJK, 10)}


class F:
    w = h = 240
    buf = None

    def write_frame(self, b):
        self.buf = b


r = LR.LCDRenderer(fonts, oled=F())
r.show_boot_screen(progress=1.0, status_text="boot", boot_frame=5)   # 预热（字体/底图缓存）
t1 = time.time()
N = 12
for i in range(N):
    r.show_boot_screen(progress=1.0, status_text="boot", boot_frame=i)
dt = (time.time() - t1) / N
print("per-frame %.1f ms (spinner budget 200ms)" % (dt * 1000))
