#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""点名（ident）：12 片和弦舵机按固定顺序依次单独抖一下。
目的：把「PCA 通道号」与「机器上手写的舵机编号 1~12」对应起来。
编排：顺序 = servo_config.csv 的 chord_channel_mapping 顺序。
      每路：预告 4 秒 -> base+28 -> base -> base-28 -> base (约 4 秒) -> 间隔 7 秒。
用法：python3 servo_ident.py            # 全部 12 路
      python3 servo_ident.py one PCA15  # 只点名一路（补测用）
输出全 ASCII（板端 stdout 非 UTF-8）。
"""
import os
import sys
import time

sys.path.insert(0, '/root/guitarist_app/src/guitarist')
from frame_codec import make_servo_single, make_servo_ctrl, make_mode_switch
from frame_types import EspMode

PORT = '/dev/ttyS3'
CFG = '/root/guitarist_app/config/servo_config.csv'
SWING = 28


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


if len(sys.argv) > 2 and sys.argv[1] == 'one':
    SEQ = [(sys.argv[2], label_of(sys.argv[2]))]
else:
    SEQ = [(t, label_of(t)) for t in chord_map]

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


def single(ct, ch, ang):
    ang = max(0, min(180, int(round(ang))))
    return send(make_servo_single(ct, ch, ang))


print('--- ident order ---')
for i, (tag, name) in enumerate(SEQ, 1):
    print('  step %2d : %-5s %-9s base=%d' % (i, tag, name, base_of(tag)))
print('-------------------')

print('%s STANDBY 15s -- watch, note the hand-written number of the tab that moves' % stamp())
for i in range(15, 0, -1):
    if i % 5 == 0:
        print('  ...%d' % i)
    time.sleep(1)

send(make_mode_switch(EspMode.NORMAL))
time.sleep(0.15)
send(make_servo_ctrl(0x01))
time.sleep(0.3)
print('%s mode=NORMAL + ENABLE sent' % stamp())

for idx, (tag, name) in enumerate(SEQ, 1):
    ct, ch = frame_args(tag)
    b = base_of(tag)
    hi = min(175, b + SWING)
    lo = max(5, b - SWING)
    print('')
    print('%s >>> STEP %d/%d : %-5s %-9s base=%d  (wiggle now)' % (
        stamp(), idx, len(SEQ), tag, name, b))
    single(ct, ch, b)
    time.sleep(0.5)
    single(ct, ch, hi)
    time.sleep(0.9)
    single(ct, ch, b)
    time.sleep(0.9)
    single(ct, ch, lo)
    time.sleep(0.9)
    single(ct, ch, b)
    print('%s     step %d done, next in 7s' % (stamp(), idx))
    time.sleep(7)

print('')
print('%s === IDENT DONE (%d channels) ===' % (stamp(), len(SEQ)))
os.close(fd)
