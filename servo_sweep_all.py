#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全通道逐路扫描 —— PCA1..PCA16 + PWM0/PWM1 各驱动一遍。

目的：Z 的编号（12/10/8/2/4/6）与 PCA 通道号对不上，
      用一次完整扫描把「哪几路不动」定位出来。

编排：按 PCA 数字顺序 1..16，再 PWM0/PWM1。
      每路 base -> +30 -> -30 -> base，约 3.2s。
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
        return 'CHORD #%d' % (chord_map.index(tag) + 1)
    if tag in strum_map:
        return 'STRUM s%d' % (strum_map.index(tag) + 1)
    return '?'


# (帧类型 ch_type, 通道号, 标签名)
SEQ = []
for n in range(1, 17):
    SEQ.append((0, n, 'PCA%d' % n))
SEQ.append((1, 0, 'PWM0'))
SEQ.append((1, 1, 'PWM1'))

SWING = 30
STANDBY = 15

try:
    fd = os.open(PORT, os.O_WRONLY | os.O_NOCTTY | os.O_NONBLOCK)
except OSError as e:
    print('OPEN FAILED: %s %s' % (type(e).__name__, e))
    sys.exit(1)

print('opened %s fd=%d' % (PORT, fd))


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
    ang = max(20, min(160, int(round(ang))))
    return send(make_servo_single(ct, ch, ang))


print('--- schedule ---')
for i, (ct, ch, tag) in enumerate(SEQ, 1):
    b = base_of(tag)
    print('  %2d) %-5s %-9s base=%d  -> %d -> %d' % (i, tag, label_of(tag), b, min(160, b + SWING), max(20, b - SWING)))
print('----------------')

print('%s STANDBY %ds -- watch the gears' % (stamp(), STANDBY))
for i in range(STANDBY, 0, -1):
    if i % 5 == 0:
        print('  ...%d' % i)
    time.sleep(1)

print('%s === START ===' % stamp())
send(make_mode_switch(EspMode.NORMAL))
time.sleep(0.15)
send(make_servo_ctrl(0x01))
time.sleep(0.3)
print('%s mode=NORMAL + ENABLE sent' % stamp())

for idx, (ct, ch, tag) in enumerate(SEQ, 1):
    b = base_of(tag)
    hi = min(160, b + SWING)
    lo = max(20, b - SWING)
    print('[%s] %2d/%d >>> %-5s %-9s base=%d' % (stamp(), idx, len(SEQ), tag, label_of(tag), b))
    single(ct, ch, b)
    time.sleep(0.4)
    single(ct, ch, hi)
    time.sleep(1.2)
    single(ct, ch, lo)
    time.sleep(1.2)
    single(ct, ch, b)
    time.sleep(0.4)

print('%s === DONE (%d channels) ===' % (stamp(), len(SEQ)))
os.close(fd)
