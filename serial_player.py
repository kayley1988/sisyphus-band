#!/usr/bin/env python3
"""
serial_player.py —— 串口自动演奏电脑端（档3保险模式的"点歌器"）

功能：读取歌曲时间轴 JSON，按时间通过串口向 ESP32 发送和弦指令，
     实现自动演奏。借鉴 TheRiffler (MIT) 的"电脑做时序、单片机做执行"架构。

用法：
    pip install pyserial
    python serial_player.py COM5 song_demo.json
    （Windows 端口形如 COM5，macOS/Linux 形如 /dev/ttyUSB0 或 /dev/ttyACM0）

歌曲 JSON 格式（事件按时间升序，chord 用和弦名或编号均可）：
    [
      {"t": 0,    "chord": "Em"},
      {"t": 1500, "chord": "C"},
      {"t": 3000, "chord": "G"},
      {"t": 4500, "chord": "D"},
      {"t": 6000, "chord": null}      ← null = 全部松开（乐段收尾）
    ]

和弦名对应（与固件 CHORD_TABLE 一致）：Em=0 Am=1 C=2 D=3 G=4 G7=5 E=6
"""

import json
import sys
import time

import serial

CHORD_INDEX = {"Em": 0, "Am": 1, "C": 2, "D": 3, "G": 4, "G7": 5, "E": 6}


def load_song(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    events = []
    for ev in raw:
        t = int(ev["t"])
        c = ev.get("chord")
        if c is None:
            events.append({"t": t, "cmd": "r", "name": "松开"})
        else:
            idx = CHORD_INDEX.get(c, c if isinstance(c, int) else None)
            if idx is None or not (0 <= idx <= 6):
                raise ValueError(f"未知和弦: {c!r}（支持 {list(CHORD_INDEX)} 或 0~6）")
            events.append({"t": t, "cmd": str(idx), "name": c})
    events.sort(key=lambda e: e["t"])
    return events


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    port, song_path = sys.argv[1], sys.argv[2]
    baud = int(sys.argv[3]) if len(sys.argv) > 3 else 115200

    events = load_song(song_path)
    total = events[-1]["t"] / 1000
    print(f"歌曲：{song_path}，共 {len(events)} 个事件，时长约 {total:.1f} 秒")
    print(f"端口：{port} @ {baud}")

    with serial.Serial(port, baud, timeout=0.2) as ser:
        time.sleep(2)          # 等 ESP32 复位完成（DTR 会触发重启）
        ser.reset_input_buffer()

        ser.write(b"A")        # 进入自动演奏模式
        time.sleep(0.3)
        # 清掉固件的欢迎信息
        while ser.read():
            pass

        print("3 秒后开始演奏…")
        for i in (3, 2, 1):
            print(f"  {i}…")
            time.sleep(1)

        t0 = time.monotonic()
        for ev in events:
            wait = ev["t"] / 1000 - (time.monotonic() - t0)
            if wait > 0:
                time.sleep(wait)
            ser.write(ev["cmd"].encode())
            print(f"  [{ev['t']:>6} ms] {ev['name']}")

        time.sleep(0.5)
        ser.write(b"r")        # 收尾全部松开
        print("演奏完毕（固件保持在自动演奏模式，发 T 可切回触摸）")


if __name__ == "__main__":
    main()
