#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单片慢扫（找"两个槽都缩进去"的那个角度）。
用法: python3 servo_scan.py <tag> [step_deg] [dwell_sec]
默认: step=10 度, dwell=5 秒  ->  0,10,20,...,180 共 19 档, 历时 90 秒
编排:
  STANDBY 10s -> 回基准 -> 【大跳】直接到 0 度  <== 这一刻开始掐表
  -> 每 dwell 秒 +step 度 -> 到 180 度停 3 秒
  -> 回基准 -> 三连抖（表示结束）
日志会同步打印每一步的时刻与角度，方便对照手机秒表。
直接向 /dev/ttyS3 注入单通道帧，不打扰服务。输出全 ASCII。
"""
import os
import sys
import time

sys.path.insert(0, '/root/guitarist_app/src/guitarist')
from frame_codec import make_servo_single, make_servo_ctrl, make_mode_switch
from frame_types import EspMode

PORT = '/dev/ttyS3'
CFG = '/root/guitarist_app/config/servo_config.csv'


def load_cfg(path):
    d = {}
    with open(path) as f:
        for ln in f:
            ln = ln.strip()
            if not ln or ln.startswith('#'):
                continue
            p = ln.split(',')
            d[p[0]] = p[1:]
    return d


def stamp():
    return time.strftime('%H:%M:%S')


cfg = load_cfg(CFG)
null_offset = [float(x) for x in cfg['null_offset']]
chord_map = cfg['chord_channel_mapping']
strum_map = cfg['strum_channel_mapping']
strum_idle = [float(cfg['strum_angles_%d' % (i + 1)][0]) for i in range(6)]
chord_home = float(cfg['home_chord_angle'][0])


def base_of(tag):
    if tag in chord_map:
        return int(round(chord_home + null_offset[chord_map.index(tag)]))
    if tag in strum_map:
        return int(round(strum_idle[strum_map.index(tag)]))
    return 90


def label_of(tag):
    if tag in chord_map:
        return 'CHORD#%d' % (chord_map.index(tag) + 1)
    if tag in strum_map:
        return 'STRUM#%d' % (strum_map.index(tag) + 1)
    return '?'


def frame_args(tag):
    if tag.startswith('PWM'):
        return (1, int(tag[3:]))
    return (0, int(tag[3:]))


if len(sys.argv) < 2:
    print('usage: servo_scan.py <tag> [step_deg] [dwell_sec]')
    sys.exit(2)

TAG = sys.argv[1]
STEP = int(sys.argv[2]) if len(sys.argv) > 2 else 10
DWELL = float(sys.argv[3]) if len(sys.argv) > 3 else 5.0
CT, CH = frame_args(TAG)
BASE = base_of(TAG)

try:
    fd = os.open(PORT, os.O_WRONLY | os.O_NOCTTY | os.O_NONBLOCK)
except OSError as e:
    print('OPEN FAILED: %s %s' % (type(e).__name__, e))
    sys.exit(1)


def send(frame):
    b = frame.serialize()
    off = 0
    t0 = time.time()
    while off < len(b) and time.time() - t0 < 0.5:
        try:
            off += os.write(fd, b[off:])
        except BlockingIOError:
            time.sleep(0.004)
        except OSError as e:
            print('write err %s' % e)
            break
    return off


def go(ang):
    ang = max(0, min(180, int(round(ang))))
    return send(make_servo_single(CT, CH, ang))


angles = list(range(0, 181, STEP))
print('%s TARGET %s (%s) base=%d step=%d dwell=%.1fs' % (
    stamp(), TAG, label_of(TAG), BASE, STEP, DWELL))
print('--- tick table (elapsed -> angle) ---')
for i, a in enumerate(angles):
    print('  t=%3ds  angle=%3d' % (int(round(i * DWELL)), a))
print('-------------------------------------')

print('%s STANDBY 10s' % stamp())
time.sleep(10)

send(make_mode_switch(EspMode.NORMAL))
time.sleep(0.15)
send(make_servo_ctrl(0x01))
time.sleep(0.3)
print('%s mode=NORMAL + ENABLE sent' % stamp())

print('%s [1] back to base %d' % (stamp(), BASE))
go(BASE)
time.sleep(1.5)

print('%s [2] >>>>>> BIG JUMP TO 0 DEG -- START YOUR STOPWATCH NOW <<<<<<' % stamp())
go(0)
for i, a in enumerate(angles):
    print('%s     t=%3ds angle=%3d %s' % (
        stamp(), int(round(i * DWELL)), a,
        '<== REMEMBER THIS TIME IF BOTH SLOTS ARE IN' if a not in (0, 180) else ''))
    if i:
        go(a)
    time.sleep(DWELL)

print('%s [3] reached 180, hold 3s' % stamp())
go(180)
time.sleep(3)

print('%s [4] back to base %d' % (stamp(), BASE))
go(BASE)
time.sleep(1)

print('%s [5] end marker: wiggle x3' % stamp())
for _ in range(3):
    go(min(175, BASE + 25))
    time.sleep(0.4)
    go(BASE)
    time.sleep(0.4)

print('%s === SCAN DONE %s ===' % (stamp(), TAG))
os.close(fd)
