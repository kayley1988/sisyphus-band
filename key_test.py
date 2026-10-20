#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
4 个菜单按键 GPIO 监测（泰山派 40PIN 脚 11/13/15/16）
按下 = 接地 = 低电平。内部上拉，未按应为高。

用法：python3 key_test.py [秒数]   默认 90 秒
"""
import sys
import time

# (标签, 板定义 pin 名, 40PIN 物理脚)
MAP = [
    ("UP ", "GPIO3_A1", 11),
    ("DN ", "GPIO3_A3", 13),
    ("SET", "GPIO3_A2", 15),
    ("ESC", "GPIO3_A4", 16),
]


def make_reader():
    """优先 libgpiod(python)，失败回退 Blinka digitalio"""
    try:
        import gpiod  # type: ignore
        chip = gpiod.Chip("gpiochip0")
        lines = {}
        for label, attr, _pin in MAP:
            num = gpio_num(attr)
            ln = chip.get_line(num)
            ln.request(consumer="key_test", type=gpiod.LINE_REQ_DIR_IN,
                       flags=gpiod.LINE_REQ_FLAG_BIAS_PULL_UP)
            lines[label] = ln
        def read():
            return [(lb, 0 if ln.get_value() else 1) for lb, ln in lines.items()]
        return read, "gpiod"
    except Exception as e:
        print("gpiod 不可用(%s)，回退 digitalio" % (e,), flush=True)

    import board  # type: ignore
    import digitalio  # type: ignore
    objs = []
    for label, attr, _pin in MAP:
        d = digitalio.DigitalInOut(getattr(board, attr))
        d.direction = digitalio.Direction.INPUT
        try:
            d.pull = digitalio.Pull.UP
        except Exception as ex:
            print("  pin %s 不支持上拉: %s" % (attr, ex), flush=True)
        objs.append((label, d))
    def read2():
        return [(lb, 0 if d.value else 1) for lb, d in objs]
    return read2, "digitalio"


def gpio_num(attr):
    """GPIO3_A1 -> 3*32 + 0*8 + 1 = 97"""
    s = attr.replace("GPIO", "")
    bank, rest = s.split("_")
    grp, idx = rest[0], rest[1:]
    return int(bank) * 32 + (ord(grp) - ord("A")) * 8 + int(idx)


def main():
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 90.0
    read, how = make_reader()
    print("监测方式: %s" % how, flush=True)
    print("映射: " + " | ".join("%s=%s(脚%d)" % (l, a, p) for l, a, p in MAP), flush=True)
    print("--- 未按时应全为 0(高电平)。按下时该键变 1，松开回 0 ---", flush=True)

    prev = {lb: 0 for lb, _, _ in MAP}
    idle_checked = False
    end = time.time() + secs
    t0 = time.time()
    while time.time() < end:
        cur = read()
        t = time.time() - t0
        # 前 3 秒只看静置电平
        if not idle_checked and t > 2.0:
            bad = [lb for lb, v in cur if v == 1]
            if bad:
                print("⚠️ 静置时就为低电平（可能上拉没生效/线接错）: %s" % bad, flush=True)
            else:
                print("✅ 静置电平正常（4 路全高）", flush=True)
            idle_checked = True
        for lb, v in cur:
            if v != prev[lb]:
                print("%6.1fs  %s -> %s" % (t, lb, "按下 ✅" if v else "松开"), flush=True)
                prev[lb] = v
        time.sleep(0.05)
    print("DONE 监测结束", flush=True)


if __name__ == "__main__":
    main()
