#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
触摸条 6 路 GPIO 采样（TTP223 按下=高电平 1）
用法：python3 touch_test.py [秒数]   默认 30 秒
"""
import sys
import time
import board  # type: ignore
import digitalio  # type: ignore

TOUCH = [
    ("C1脚7 ", "GPIO1_A4"),
    ("C2脚12", "GPIO3_C4"),
    ("C3脚18", "GPIO3_A5"),
    ("C4脚22", "GPIO3_A6"),
    ("C5脚26", "GPIO3_A7"),
    ("C6脚32", "GPIO3_C5"),
]


def main():
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0
    objs = []
    for label, attr in TOUCH:
        try:
            d = digitalio.DigitalInOut(getattr(board, attr))
            d.direction = digitalio.Direction.INPUT
            objs.append((label, d))
        except Exception as e:
            print("  %s 打开失败: %s" % (label, e), flush=True)

    print("已打开 %d 路，开始采样 %.0f 秒（请把 6 个点逐个摸一遍）" % (len(objs), secs), flush=True)
    prev = {lb: None for lb, _ in objs}
    t0 = time.time()
    next_report = 5.0
    hit = set()
    while time.time() - t0 < secs:
        t = time.time() - t0
        for lb, d in objs:
            v = 1 if d.value else 0
            if prev[lb] is None:
                prev[lb] = v
                print("%6.1fs  初始 %s = %d" % (t, lb, v), flush=True)
            elif v != prev[lb]:
                print("%6.1fs  %s -> %s" % (t, lb, "触摸 ✅(高)" if v else "松开"), flush=True)
                prev[lb] = v
                if v:
                    hit.add(lb)
        if t >= next_report:
            print("  [%.0fs] 当前: %s" % (t, " ".join("%s=%d" % (lb, prev[lb]) for lb, _ in objs)), flush=True)
            next_report += 5.0
        time.sleep(0.05)

    print("--- 结果 ---", flush=True)
    print("检测到触摸的路: %s" % (sorted(hit) if hit else "【无】"), flush=True)
    print("没反应的路: %s" % (sorted(set(lb for lb, _ in objs) - hit) or "无"), flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
