#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""标定示范：只动 PCA15 一片拨片，0 度停 4 秒 -> 180 度停 4 秒。
看这一片在哪个站缩进去。直接向 /dev/ttyS3 注入单通道帧，不打扰服务。"""
import os
import sys
import time

sys.path.insert(0, '/root/guitarist_app/src/guitarist')
from frame_codec import make_servo_single, make_servo_ctrl, make_mode_switch
from frame_types import EspMode

PORT = '/dev/ttyS3'
CH = 15
BASE = 98            # PCA15 的组内基准角 (home 90 + null_offset 8)
STANDBY = 15

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


def single(pca, ang):
    ang = max(0, min(180, int(round(ang))))
    return send(make_servo_single(0, pca, ang))


def stamp():
    return time.strftime('%H:%M:%S')


print('%s STANDBY %ds -- watch which tab moves (only ONE tab should move)' % (stamp(), STANDBY))
for i in range(STANDBY, 0, -1):
    if i % 5 == 0:
        print('  ...%d' % i)
    time.sleep(1)

send(make_mode_switch(EspMode.NORMAL))
time.sleep(0.15)
send(make_servo_ctrl(0x01))
time.sleep(0.3)
print('%s mode=NORMAL + ENABLE sent' % stamp())

print('%s [1] PCA15 -> base %d (settle)' % (stamp(), BASE))
single(CH, BASE)
time.sleep(1.2)

print('%s [2] >>> STOP-1: PCA15 -> 0 deg, hold 4s' % stamp())
single(CH, 0)
time.sleep(4)

print('%s [3] PCA15 -> back to base %d (2s)' % (stamp(), BASE))
single(CH, BASE)
time.sleep(2)

print('%s [4] >>> STOP-2: PCA15 -> 180 deg, hold 4s' % stamp())
single(CH, 180)
time.sleep(4)

print('%s [5] PCA15 -> back to base %d' % (stamp(), BASE))
single(CH, BASE)
time.sleep(1)

print('%s === DEMO DONE ===' % stamp())
os.close(fd)
