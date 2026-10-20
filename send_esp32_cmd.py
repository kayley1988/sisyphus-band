# -*- coding: utf-8 -*-
"""向小智板（ESP32-S3 / CH340）USB 调试串口发送 ASCII 命令并读回显。

用法:
    python send_esp32_cmd.py "STATUS"
    python send_esp32_cmd.py "PCA 1 0" "PCA 1 180"
    python send_esp32_cmd.py --port COM5 --wait 2 "INFO"

支持命令（固件 cmd_legacy）:
    PCA <ch> <ang>    ch=1..16  PCA9685 通道
    PWM <id> <ang>    id=0/1    直驱通道(GPIO40/39)
    ALL <ang>         18 路统一角度
    AIN [ch]          ADC 读数
    STATUS / STATS / INFO / RESET_PROTECTION

注意: PCA/PWM/ALL 是 fire-and-forget 不回 OK，但固件会打印
      `[CMD] PCA #n: ch=x ang=y`（前 3 条必打，之后每 50 条一次）。
"""
import sys
import time
import serial

port = "COM5"
baud = 115200
wait = 1.5
cmds = []

args = sys.argv[1:]
i = 0
while i < len(args):
    if args[i] == "--port":
        port = args[i + 1]
        i += 2
    elif args[i] == "--wait":
        wait = float(args[i + 1])
        i += 2
    else:
        cmds.append(args[i])
        i += 1

if not cmds:
    print(__doc__)
    sys.exit(1)

try:
    ser = serial.Serial(port, baud, timeout=0.2)
except Exception as e:
    print("[FAIL] 打不开", port, "->", e)
    print("检查: 串口助手是否占用该口")
    sys.exit(1)

ser.setDTR(False)
ser.setRTS(False)
time.sleep(0.3)

# 排空残留/启动日志
ser.reset_input_buffer()
t0 = time.time()
while time.time() - t0 < 1.0:
    ser.read(4096)

print("[INFO] 端口 %s @ %d 已就绪，开始发送命令" % (port, baud))
print("=" * 56)

for c in cmds:
    ser.reset_input_buffer()
    ser.write((c + "\n").encode("ascii", errors="replace"))
    ser.flush()
    print(">>> %s" % c)
    time.sleep(wait)
    buf = b""
    t0 = time.time()
    while time.time() - t0 < 0.6:
        chunk = ser.read(4096)
        if chunk:
            buf += chunk
            t0 = time.time()
        else:
            time.sleep(0.02)
    out = buf.decode("utf-8", errors="replace").strip()
    print(out if out else "(无回显)")
    print("-" * 56)

ser.close()
