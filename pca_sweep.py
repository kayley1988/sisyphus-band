#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PCA9685 通道扫描 —— 用一个舵机问出"哪一格 = 第几通道"

用途：本板不印通道数字，把 1 个舵机插在某格，跑本脚本逐个通道点动，
      舵机动的那一下打印的通道号 = 这一格的编号（顺便验 0/1 基）。

用法（板端）：
    python3 pca_sweep.py             # 扫 PCA 1..16（每个通道 90→65→115→90）
    python3 pca_sweep.py 0           # 从 PCA 0 开始扫（验 0/1 基时用）
    python3 pca_sweep.py 1 40        # 从 PCA 1 开始，摆幅 ±40°
    python3 pca_sweep.py one 2 30    # 只点动 PCA 2，±30°
"""
import sys
import time
from datetime import datetime

sys.path.insert(0, "/root/guitarist_app/src")
sys.path.insert(0, "/root/guitarist_app/src/guitarist")

from serial_bus import SerialBus          # noqa: E402
from frame_layer import FrameLayer        # noqa: E402
from frame_codec import make_servo_single # noqa: E402
from frame_types import ServoChType       # noqa: E402

PORT = "/dev/ttyS3"
BAUD = 921600
STEP_WAIT = 0.45


def open_bus():
    bus = SerialBus.instance()
    bus.open(PORT, BAUD)
    time.sleep(0.4)
    return bus


def make_setter(bus):
    fl = FrameLayer(bus)

    def _set(ch, ang):
        fl.send_frame(make_servo_single(int(ServoChType.PCA), int(ch), int(ang)))

    return _set


def jog(_set, ch, swing):
    print("[%s]  PCA ch %-2d : 90 -> %d -> %d -> 90" %
          (datetime.now().strftime("%H:%M:%S"), ch, 90 - swing, 90 + swing), flush=True)
    for a in (90, 90 - swing, 90 + swing, 90):
        _set(ch, a)
        time.sleep(STEP_WAIT)


def main():
    args = sys.argv[1:]
    start, swing = 1, 25
    only = None

    if args and args[0] == "one":
        only = int(args[1])
        swing = int(args[2]) if len(args) > 2 else 30
    else:
        if len(args) > 0:
            start = int(args[0])
        if len(args) > 1:
            swing = int(args[1])

    bus = open_bus()
    _set = make_setter(bus)

    if only is not None:
        print("=== 只点动 PCA ch %d ===" % only, flush=True)
        jog(_set, only, swing)
        print("DONE one", flush=True)
        return

    print("=== 扫描 PCA ch %d ~ 16（盯住舵机，动的那一刻的通道号就是它）===" % start, flush=True)
    for ch in range(start, 17):
        jog(_set, ch, swing)
        time.sleep(0.9)
    print("=== 全部回中位 ===", flush=True)
    for ch in range(1, 17):
        _set(ch, 90)
    print("DONE sweep", flush=True)


if __name__ == "__main__":
    main()
