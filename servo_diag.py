#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小智板链路诊断 + 舵机使能 + 单点动
  1) 发 CTRL_HW_INFO，看有没有 ACK（判断帧链路到底通不通、是否 safe_mode）
  2) 打 FrameLayer 统计（帧数 / CRC 错误）
  3) 发 SERVO_CTRL ENABLE（解除可能的禁能/过流保护）
  4) 点动和弦 0 路（PWM0 = GPIO40）验证
"""
import sys
import time

sys.path.insert(0, "/root/guitarist_app/src")
sys.path.insert(0, "/root/guitarist_app/src/guitarist")

from serial_bus import SerialBus            # noqa: E402
from frame_layer import FrameLayer          # noqa: E402
from frame_codec import (                   # noqa: E402
    make_hw_info, make_servo_ctrl, make_servo_single, parse_hw_info_ack,
)
from frame_types import FrameType, ServoCtrlType, ServoChType  # noqa: E402

PORT = "/dev/ttyS3"
BAUD = 921600

bus = SerialBus.instance()
bus.open(PORT, BAUD)
time.sleep(0.5)
fl = FrameLayer(bus)

seen = {}


def _payload(f):
    for k in ("payload", "data", "body"):
        if hasattr(f, k):
            return getattr(f, k)
    return None


def _ftype(f):
    for k in ("frame_type", "ftype", "type", "msg_type"):
        if hasattr(f, k):
            return getattr(f, k)
    return "?"


def cb(f):
    p = _payload(f)
    seen[str(_ftype(f))] = p
    print("  RECV type=%s len=%s %s" % (_ftype(f), len(p) if p else 0,
                                        p.hex() if p else ""), flush=True)


fl.on_frame(FrameType.CTRL_HW_INFO_ACK, cb)

print("[1] 发 CTRL_HW_INFO（查固件/板型/safe_mode）", flush=True)
fl.send_frame(make_hw_info())
time.sleep(3.0)

key = str(FrameType.CTRL_HW_INFO_ACK)
if key in seen:
    info = parse_hw_info_ack(seen[key])
    print("    HW_INFO_ACK:", info, flush=True)
    print("    → board=%s safe_mode=%s has_pca_oe=%s"
          % (info.get("name"), info.get("safe_mode"), info.get("has_pca_oe")), flush=True)
else:
    print("    ❌ 没收到 HW_INFO_ACK —— 小智板没收到我们的帧（链路/协议问题）", flush=True)

print("[2] FrameLayer 统计:", fl.stats(), flush=True)

print("[3] 发 SERVO_CTRL ENABLE（解除禁能/过流保护）", flush=True)
fl.send_frame(make_servo_ctrl(int(ServoCtrlType.ENABLE)))
time.sleep(0.6)

print("[4] 点动 chord0 = PWM slot0 = GPIO40：90→70→110→90", flush=True)
for a in (90, 70, 110, 90):
    fl.send_frame(make_servo_single(int(ServoChType.PWM), 0, a))
    time.sleep(0.6)

print("[5] 点动 chord1 = PCA ch15：90→70→110→90", flush=True)
for a in (90, 70, 110, 90):
    fl.send_frame(make_servo_single(int(ServoChType.PCA), 15, a))
    time.sleep(0.6)

print("DONE", flush=True)
