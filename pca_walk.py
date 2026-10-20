# -*- coding: utf-8 -*-
"""PCA9685 走马灯点名 —— 从 V+ 端第 1 格起，逐格小幅摆动，看舵机是否依次动作。

用法:
    python pca_walk.py COM4            # 全 16 格
    python pca_walk.py COM4 1 6        # 只点 ch1~ch6（拨弦区）
    python pca_walk.py COM4 7 16       # 只点 ch7~ch16（压弦区）

原理: 固件 USB ASCII 命令 `PCA <ch> <ang>`，ch=1..16。
      每格 60 -> 120 -> 90（±30° 小幅，安全），间隔 1s 便于人眼分辨。
      PCA/PWM 是 fire-and-forget，靠固件回显 `[CMD] PCA #n: ch=x ang=y` 确认。
"""
import sys
import time

import serial

port = sys.argv[1] if len(sys.argv) > 1 else "COM4"
baud = 115200
lo = int(sys.argv[2]) if len(sys.argv) > 2 else 1
hi = int(sys.argv[3]) if len(sys.argv) > 3 else 16

try:
    ser = serial.Serial(port, baud, timeout=0.2)
except Exception as e:
    print("[FAIL] 打不开", port, "->", e)
    sys.exit(1)

# 不复位板子：只拉低 DTR/RTS，避免触发 ESP32 重启
ser.setDTR(False)
ser.setRTS(False)
time.sleep(0.3)
ser.reset_input_buffer()
t0 = time.time()
while time.time() - t0 < 1.0:
    ser.read(4096)


def send(cmd, wait=0.35):
    ser.reset_input_buffer()
    ser.write((cmd + "\n").encode("ascii", errors="replace"))
    ser.flush()
    print(">>>", cmd, flush=True)
    time.sleep(wait)
    buf = b""
    t0 = time.time()
    while time.time() - t0 < 0.25:
        c = ser.read(4096)
        if c:
            buf += c
            t0 = time.time()
        else:
            time.sleep(0.01)
    o = buf.decode("utf-8", errors="replace").strip()
    if o:
        print("    " + o.replace("\n", "\n    "), flush=True)


print("=" * 60)
print("== 1) 全 18 路回中 90 (齿条缩回) ==")
send("ALL 90", 1.2)
time.sleep(1.0)

print("=" * 60)
print("== 2) 走马灯 ch%d .. ch%d ==" % (lo, hi))
for ch in range(lo, hi + 1):
    print("---- ch%d ----" % ch, flush=True)
    send("PCA %d 60" % ch)
    send("PCA %d 120" % ch)
    send("PCA %d 90" % ch)
    time.sleep(0.9)

print("=" * 60)
print("== 3) 结束回中 ==")
send("ALL 90", 1.0)
ser.close()
print("[DONE]")
