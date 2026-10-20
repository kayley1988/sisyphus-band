# -*- coding: utf-8 -*-
"""读取小智板（ESP32-S3 / CH340）调试串口日志。

用法:
    python read_esp32_log.py [COM口] [波特率] [复位后读取秒数]
默认:
    COM5  115200  8

说明:
    先静读 2 秒；若无输出则发一次硬件复位脉冲（拉 RTS），
    让 ESP32 重启以打印启动自检日志（含 PCA9685 检测结果）。
"""
import sys
import time
import serial

port = sys.argv[1] if len(sys.argv) > 1 else "COM5"
baud = int(sys.argv[2]) if len(sys.argv) > 2 else 115200
secs = float(sys.argv[3]) if len(sys.argv) > 3 else 8.0

try:
    ser = serial.Serial(port, baud, timeout=0.2)
except Exception as e:
    print("[FAIL] 打不开", port, "->", e)
    print("检查: 1) 串口助手/PuTTY 是否占用了该口  2) 设备管理器里 CH340 是否在")
    sys.exit(1)

# 正常态：DTR 拉低保持 IO0 高（不进下载模式），RTS 拉低不复位
ser.setDTR(False)
ser.setRTS(False)
time.sleep(0.3)
ser.reset_input_buffer()

print("[INFO] 已打开 %s @ %d，静读 2 秒..." % (port, baud))
data = b""
t0 = time.time()
while time.time() - t0 < 2.0:
    data += ser.read(4096)

if not data:
    print("[INFO] 无输出 -> 发硬件复位脉冲 (RTS)，让板子重启打日志...")
    ser.setDTR(False)
    ser.setRTS(True)
    time.sleep(0.15)
    ser.setRTS(False)
    t0 = time.time()
    while time.time() - t0 < secs:
        data += ser.read(4096)

ser.close()

if not data:
    print("[RESULT] 一个字节都没收到")
    print("可能原因: 1) 板子跑的不是我们的固件  2) 波特率不是 %d  3) 该口不是调试口" % baud)
else:
    print("[RESULT] 收到 %d 字节:" % len(data))
    print("-" * 60)
    print(data.decode("utf-8", errors="replace"))
    print("-" * 60)
