# -*- coding: utf-8 -*-
"""舵机急停 / 松弦断电（PC 端，走小智板 USB 调试口 COM）。

背景：固件 playback_engine.cpp:314 明确写着
    // servo::disable_all() 已移除——舵机保持最后一次 PWM 输出，有保持力矩不会乱动
所以演奏结束后舵机仍持续通电（发热）。而 `STOP` 命令也只 request_stop、不失能。

本脚本发送 ASCII 命令 `CONFIG_APPLY`（cmd_nvs.cpp）：
    chord::reset_position()  → delay(300ms)  → servo::disable_all()
disable_all() 会：归位 → 校验 → 分组断电 → 拉 OE → PCA9685 进 SLEEP
⇒ 16 路 PCA + 2 路直驱全部失去 PWM 输出，舵机真正松掉、不再发热。

⚠️ 前提：固件里 chord::is_calib_valid() 为真（有标定数据）。
        若无，会返回 "ERR no calibration data" —— 本脚本自动改用 CONFIG_SAVE 兜底
        （save_to_nvs 内部 = save → enable → apply → disable）。

用法:
    python servo_off.py                 # 自动找 CH340 口
    python servo_off.py --port COM4
    python servo_off.py --dry           # 只打印将发送的命令
"""
import sys
import time

try:
    import serial
    import serial.tools.list_ports as list_ports
except ImportError:
    print("[FAIL] 缺 pyserial，请用项目 venv 运行")
    sys.exit(1)

CMD_LOAD = "CONFIG_LOAD"
CMD_APPLY = "CONFIG_APPLY"
CMD_SAVE = "CONFIG_SAVE"
CMD_STATUS = "STATUS"


def find_port():
    """找 CH340 / USB-SERIAL 那个口（小智板 USB 调试口）。"""
    cands = []
    for p in list_ports.comports():
        hwid = (p.hwid or "").upper()
        desc = (p.description or "").upper()
        if "1A86" in hwid or "CH340" in desc or "USB-SERIAL" in desc:
            cands.append(p.device)
    return cands[0] if cands else None


def _drain(ser, sec):
    t0 = time.time()
    buf = b""
    while time.time() - t0 < sec:
        chunk = ser.read(4096)
        if chunk:
            buf += chunk
            t0 = time.time()
        else:
            time.sleep(0.02)
    return buf.decode("utf-8", errors="replace")


def send(ser, cmd, wait=1.2):
    ser.reset_input_buffer()
    ser.write((cmd + "\n").encode("ascii", errors="replace"))
    ser.flush()
    time.sleep(wait)
    return _drain(ser, 0.6).strip()


def main():
    args = sys.argv[1:]
    port = None
    dry = False
    i = 0
    while i < len(args):
        if args[i] == "--port":
            port = args[i + 1]
            i += 2
        elif args[i] == "--dry":
            dry = True
            i += 1
        else:
            i += 1

    if dry:
        print("[DRY] 将依次发送:", CMD_LOAD, CMD_APPLY, "(若 ERR ->)", CMD_SAVE, CMD_APPLY)
        return

    if not port:
        port = find_port()
        if not port:
            print("[FAIL] 没找到 CH340 串口 —— 小智板的 USB 调试线插上了吗？")
            print("       插上后重跑；或显式指定 --port COMx")
            sys.exit(1)
        print("[INFO] 自动选用串口:", port)

    try:
        ser = serial.Serial(port, 115200, timeout=0.2)
    except Exception as e:
        print("[FAIL] 打不开 %s -> %s" % (port, e))
        print("       检查: 串口助手/其他程序是否占用；板子是否上电")
        sys.exit(1)

    ser.setDTR(False)
    ser.setRTS(False)
    time.sleep(0.3)
    ser.reset_input_buffer()
    _drain(ser, 1.0)          # 排空启动日志

    print("=" * 56)
    print("[STEP 1] 加载标定参数:", CMD_LOAD)
    print(send(ser, CMD_LOAD))

    print("-" * 56)
    print("[STEP 2] 归位 + 失能（★ 舵机应停止出力、开始变凉）:", CMD_APPLY)
    out = send(ser, CMD_APPLY, wait=2.0)   # reset_position 内部 delay(300ms)
    print(out)

    if "ERR" in out.upper():
        print("-" * 56)
        print("[STEP 3] 无标定数据 -> 用 CONFIG_SAVE 兜底（内部也会 disable）")
        print(send(ser, CMD_SAVE, wait=2.5))
        print("[STEP 3b] 再 APPLY 一次确保失能")
        print(send(ser, CMD_APPLY, wait=2.0))
    else:
        print("[OK] 已发送失能流程。摸一下舵机，应在几十秒内明显变凉。")

    print("-" * 56)
    print("[STEP 4] 状态确认:", CMD_STATUS)
    print(send(ser, CMD_STATUS))

    ser.close()
    print("=" * 56)
    print("提示：若仍烫手，说明是机械堵转/供电异常 —— 直接物理拔掉 5V（PCA 绿端子 V+ 粗线）。")


if __name__ == "__main__":
    main()
