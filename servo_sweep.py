# 舵机 PWM 动态判决：让和弦舵机在两个角度间摆动 + 一次失能/使能，供万用表"看读数跳变"
# 用法（板上）: python3 /tmp/servo_sweep.py
import json
import time
import urllib.request

BASE = 'http://127.0.0.1:5000'


def post(path, payload, t=20):
    req = urllib.request.Request(BASE + path, method='POST',
                                 data=json.dumps(payload).encode(),
                                 headers={'Content-Type': 'application/json'})
    try:
        return urllib.request.urlopen(req, timeout=t).read().decode()
    except Exception as e:
        return 'ERR %s' % e


t0 = time.time()


def step(name, path, payload, hold):
    r = post(path, payload)
    print('[%5.1fs] %-26s -> %s' % (time.time() - t0, name, r[:130]))
    time.sleep(hold)


print('### 开始（总时长约 22s）')
print('--- 1) ENABLE + 60度（信号针读数应偏低）---')
step('jog 60 (enable)', '/api/servo/jog', {'chord_angle': 60, 'ensure_mode': True}, 5)

print('--- 2) 120度（信号针读数应明显升高）---')
step('jog 120', '/api/servo/jog', {'chord_angle': 120}, 5)

print('--- 3) RELEASE 失能（读数应塌为 0/不动，齿轮应变松）---')
step('release', '/api/servo/release', {}, 5)

print('--- 4) 重新 ENABLE + 90度（读数回来、齿轮应重新锁住）---')
step('jog 90 (re-enable)', '/api/servo/jog', {'chord_angle': 90, 'ensure_mode': True}, 5)

print('### done')
