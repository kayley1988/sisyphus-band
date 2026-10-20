#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逐个驱动指定的 PCA 通道 —— 直接向 /dev/ttyS3 注入二进制帧。

为什么这样做：
  - /api/servo/jog 只能把【整个和弦组】设成同一角度（chord = [angle]*12），
    无法只点一路；要逐路验证必须走底层单通道帧 NORMAL_SERVO_SINGLE。
  - 以 O_WRONLY|O_NONBLOCK 打开串口：不改 termios、不读串口，
    所以正在运行的 guitar.service 的收发完全不受影响。

测试编排（关键）：
  每一路"可疑通道"前后各夹一次 PCA15（已知能动的对照组），
  这样围观者只需比较"紧挨着的两下"，不必记前面的。

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


def pca_base(n):
    tag = 'PCA%d' % n
    if tag in chord_map:
        return int(round(chord_home + null_offset[chord_map.index(tag)]))
    if tag in strum_map:
        return int(round(strum_idle[strum_map.index(tag)]))
    return 90


REF = (15, 'REF  PCA15 chord#2  (known-good)')
SUSPECTS = [
    (4,  'TEST PCA4  strum#1'),
    (6,  'TEST PCA6  strum#2'),
    (8,  'TEST PCA8  strum#3'),
    (10, 'TEST PCA10 chord#11'),
    (12, 'TEST PCA12 chord#10'),
]

SEQ = []
for _s in SUSPECTS:
    SEQ.append(REF)
    SEQ.append(_s)
SEQ.append(REF)

SWING = 25
STANDBY = 20

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


def single(pca, ang):
    ang = max(20, min(160, int(round(ang))))
    return send(make_servo_single(0, pca, ang))


print('%s STANDBY %ds -- watch the gears now' % (stamp(), STANDBY))
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

for idx, (pca, label) in enumerate(SEQ, 1):
    base = pca_base(pca)
    hi = min(160, base + SWING)
    lo = max(20, base - SWING)
    print('[%s] %2d/%d  >>> %s  base=%d  %d -> %d -> %d' %
          (stamp(), idx, len(SEQ), label, base, hi, lo, base))
    single(pca, base)
    time.sleep(0.5)
    single(pca, hi)
    time.sleep(1.3)
    single(pca, lo)
    time.sleep(1.3)
    single(pca, base)
    time.sleep(0.6)

print('%s === DONE (%d segments) ===' % (stamp(), len(SEQ)))
os.close(fd)
