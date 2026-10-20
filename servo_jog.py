#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
舵机点动测试（泰山派 → UART3@921600 → 小智板 → PCA9685 / LEDC 直驱）
走 V3 二进制帧协议（NORMAL_SERVO_SINGLE），不走 ASCII（ASCII 已无响应）。

通道映射（固件 XIAOZHI_PROFILE，勿手改）：
    和弦 12 路: idx0=PWM0(GPIO40) idx1=PCA15 idx2=PCA13 idx3=PCA11 idx4=PCA9  idx5=PCA7
                idx6=PWM1(GPIO39) idx7=PCA16 idx8=PCA14 idx9=PCA12 idx10=PCA10 idx11=PCA8
    拨弦 6 路 : idx0..5 = PCA 2 / 4 / 6 / 1 / 3 / 5

用法：
    python3 servo_jog.py all [幅度]        18 路逐路点动一遍（默认幅度 20°）
    python3 servo_jog.py chord [idx] [幅度] 点动和弦（0~11），省略 idx = 全部 12 路
    python3 servo_jog.py strum [idx] [幅度] 点动拨弦（0~5），省略 idx = 全部 6 路
    python3 servo_jog.py angle <角度>      18 路统一到某角度
    python3 servo_jog.py sniff [秒]        只监听串口，看小智板有没有在发数据
"""
import sys
import time

sys.path.insert(0, "/root/guitarist_app/src")
sys.path.insert(0, "/root/guitarist_app/src/guitarist")

from serial_bus import SerialBus          # noqa: E402
from frame_layer import FrameLayer        # noqa: E402
from frame_codec import make_servo_single # noqa: E402
from frame_types import ServoChType       # noqa: E402

PORT = "/dev/ttyS3"
BAUD = 921600

# (类型, 通道号) — PWM 通道号是 slot(0/1)，PCA 通道号是 1-based
CHORD = [("PWM", 0), ("PCA", 15), ("PCA", 13), ("PCA", 11),
         ("PCA", 9), ("PCA", 7), ("PWM", 1), ("PCA", 16),
         ("PCA", 14), ("PCA", 12), ("PCA", 10), ("PCA", 8)]
STRUM = [("PCA", 2), ("PCA", 4), ("PCA", 6), ("PCA", 1), ("PCA", 3), ("PCA", 5)]

STEP_WAIT = 0.55


def open_bus():
    bus = SerialBus.instance()
    bus.open(PORT, BAUD)
    time.sleep(0.4)
    return bus


def make_setter(bus):
    fl = FrameLayer(bus)

    def _set(ty, ch, ang):
        ct = int(ServoChType.PWM if ty == "PWM" else ServoChType.PCA)
        fl.send_frame(make_servo_single(ct, ch, int(ang)))

    return _set


def jog(_set, kind, idx, ty, ch, swing):
    label = "%s %-2d  %s ch=%-2d" % (kind, idx, "PWM" if ty == "PWM" else "PCA", ch)
    print("[%s]  90 → %d → %d → 90" % (label, 90 - swing, 90 + swing), flush=True)
    for a in (90, 90 - swing, 90 + swing, 90):
        _set(ty, ch, a)
        time.sleep(STEP_WAIT)


def main():
    args = sys.argv[1:]
    mode = args[0] if args else "all"
    swing = 20

    if mode == "sniff":
        import serial
        secs = float(args[1]) if len(args) > 1 else 5.0
        s = serial.Serial(PORT, BAUD, timeout=1)
        end = time.time() + secs
        n = 0
        while time.time() < end:
            d = s.read(256)
            if d:
                n += len(d)
        print("sniff %.1fs: 收到 %d 字节" % (secs, n), flush=True)
        return

    bus = open_bus()
    _set = make_setter(bus)

    if mode == "angle":
        ang = int(args[1]) if len(args) > 1 else 90
        print("全部 18 路 → %d°" % ang, flush=True)
        for ty, ch in CHORD + STRUM:
            _set(ty, ch, ang)
        time.sleep(0.5)
        return

    if mode == "all":
        if len(args) > 1:
            swing = int(args[1])
        print("=== 和弦 12 路 ===", flush=True)
        for i, (ty, ch) in enumerate(CHORD):
            jog(_set, "chord", i, ty, ch, swing)
        print("=== 拨弦 6 路 ===", flush=True)
        for i, (ty, ch) in enumerate(STRUM):
            jog(_set, "strum", i, ty, ch, swing)
        print("=== 全部回中位 90 ===", flush=True)
        for ty, ch in CHORD + STRUM:
            _set(ty, ch, 90)
        time.sleep(0.5)
        print("DONE all", flush=True)
        return

    if mode in ("chord", "strum"):
        table = CHORD if mode == "chord" else STRUM
        if len(args) > 2:
            swing = int(args[2])
        if len(args) > 1:
            i = int(args[1])
            ty, ch = table[i]
            jog(_set, mode, i, ty, ch, swing)
        else:
            for i, (ty, ch) in enumerate(table):
                jog(_set, mode, i, ty, ch, swing)
            for ty, ch in table:
                _set(ty, ch, 90)
        print("DONE %s" % mode, flush=True)
        return

    print(__doc__)


if __name__ == "__main__":
    main()
