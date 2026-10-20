#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单片舵机测试（一个一个来）。
用法: python3 servo_one.py <tag> [probe|wiggle|zero|full|hold180]
      tag 例: PCA15 / PWM0
      probe(默认) = 抖动确认编号 -> 0 度保持 6 秒 -> 回基准 -> 180 度保持 6 秒 -> 回基准
      wiggle      = 只抖动（确认是哪一片）
      zero/full   = 只停在 0 度 / 180 度保持 8 秒
编排时间线（probe）:
  T-10s  待机（先读这条日志，知道要开始了）
  T+0    回基准
  T+1    抖 3 下（+28 -> base -> -28 -> base）
  T+6    停 5 秒（趁这时确认「它是几号」）
  T+11   0 度保持 6 秒   <== 看它缩不缩进去
  T+19   回基准
  T+21   180 度保持 6 秒 <== 看它缩不缩进去
  T+29   回基准，结束
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
STANDBY = 10
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


if len(sys.argv) < 2:
    print('usage: servo_one.py <tag> [probe|wiggle|zero|full|hold180]')
    sys.exit(2)

TAG = sys.argv[1]
MODE = sys.argv[2] if len(sys.argv) > 2 else 'probe'
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
    return single(CT, CH, ang)


def single(ct, ch, ang):
    return send(make_servo_single(ct, ch, ang))


print('%s TARGET %s (%s) base=%d mode=%s' % (stamp(), TAG, label_of(TAG), BASE, MODE))
print('%s STANDBY %ds ...' % (stamp(), STANDBY))
for i in range(STANDBY, 0, -1):
    if i % 5 == 0:
        print('  ...%d' % i)
    time.sleep(1)

send(make_mode_switch(EspMode.NORMAL))
time.sleep(0.15)
send(make_servo_ctrl(0x01))
time.sleep(0.3)
print('%s mode=NORMAL + ENABLE sent' % stamp())

if MODE in ('wiggle', 'probe'):
    print('%s [1] base %d (settle)' % (stamp(), BASE))
    go(BASE)
    time.sleep(1)
    hi = min(175, BASE + SWING)
    lo = max(5, BASE - SWING)
    print('%s [2] wiggle %d -> %d -> %d -> %d  <== WHICH NUMBER AM I?' % (stamp(), hi, BASE, lo, BASE))
    go(hi)
    time.sleep(1.0)
    go(BASE)
    time.sleep(1.0)
    go(lo)
    time.sleep(1.0)
    go(BASE)
    if MODE == 'wiggle':
        print('%s === WIGGLE DONE ===' % stamp())
        os.close(fd)
        sys.exit(0)
    print('%s [3] hold 5s -- confirm the number' % stamp())
    time.sleep(5)

if MODE in ('probe', 'zero'):
    print('%s [4] >>> 0 DEG, hold 6s   <== RETRACT HERE?' % stamp())
    go(0)
    time.sleep(6)
    print('%s [5] back to base %d' % (stamp(), BASE))
    go(BASE)
    time.sleep(2)

if MODE in ('probe', 'full', 'hold180'):
    print('%s [6] >>> 180 DEG, hold 6s <== RETRACT HERE?' % stamp())
    go(180)
    time.sleep(6)
    print('%s [7] back to base %d' % (stamp(), BASE))
    go(BASE)
    time.sleep(1)

print('%s === DONE %s ===' % (stamp(), TAG))
os.close(fd)
