#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""和弦归位标定（逐路版）：一次过完 12 片和弦拨片。
每片节奏：预告 6 秒(停在基准) -> 0 度停 4 秒 -> 回基准 2 秒
        -> 180 度停 4 秒 -> 回基准 -> 间隔 5 秒。
目的：镜像安装确诊后，逐片确认「0 度缩进去还是 180 度缩进去」，
      后续按片写入独立归位角。
用法：python3 servo_calib_all.py            # 12 路和弦
      python3 servo_calib_all.py all        # 和弦12 + 拨弦6
      python3 servo_calib_all.py one PCA15  # 只测一路
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
WARN = 6
HOLD = 4
GAP = 5


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


argv = sys.argv[1:]
if argv and argv[0] == 'one':
    SEQ = [(argv[1], label_of(argv[1]))]
elif argv and argv[0].lower() == 'all':
    SEQ = [(t, label_of(t)) for t in chord_map] + [(t, label_of(t)) for t in strum_map]
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


print('--- schedule : base -> 0(%ds) -> base -> 180(%ds) -> base ---' % (HOLD, HOLD))
for i, (tag, name) in enumerate(SEQ, 1):
    print('  step %2d : %-5s %-9s base=%d' % (i, tag, name, base_of(tag)))
print('------------------------------------------------------------')

print('%s STANDBY 15s' % stamp())
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
    print('')
    print('%s === STEP %d/%d : %-5s %-9s base=%d  get ready %ds ===' % (
        stamp(), idx, len(SEQ), tag, name, b, WARN))
    single(ct, ch, b)
    time.sleep(WARN)

    print('%s >>> [A] %s (base %d) -> 0 deg, HOLD %ds  <== WATCH' % (stamp(), tag, b, HOLD))
    single(ct, ch, 0)
    time.sleep(HOLD)

    print('%s     back to base %d (2s)' % (stamp(), b))
    single(ct, ch, b)
    time.sleep(2)

    print('%s >>> [B] %s (base %d) -> 180 deg, HOLD %ds  <== WATCH' % (stamp(), tag, b, HOLD))
    single(ct, ch, 180)
    time.sleep(HOLD)

    print('%s     back to base %d' % (stamp(), b))
    single(ct, ch, b)
    time.sleep(GAP)

print('')
print('%s === CALIB DONE (%d channels) ===' % (stamp(), len(SEQ)))
os.close(fd)
