# 舵机动作可视化验证：把和弦舵机来回走一趟（装机前自检）
# 用法（板上）: python3 /tmp/servo_check.py
import json
import time
import urllib.request

BASE = 'http://127.0.0.1:5000'


def post(path, payload):
    req = urllib.request.Request(BASE + path, method='POST',
                                 data=json.dumps(payload).encode(),
                                 headers={'Content-Type': 'application/json'})
    try:
        return urllib.request.urlopen(req, timeout=20).read().decode()
    except Exception as e:
        return 'ERR %s' % e


def get(path):
    try:
        return urllib.request.urlopen(BASE + path, timeout=8).read().decode()
    except Exception as e:
        return 'ERR %s' % e


print('=== 0) 读归位配置 ===')
print(get('/api/servo/home/config'))

print('=== 1) 和弦舵机走动: 60 -> 120 -> 60 -> 90 (每步停 2.5s，盯着齿轮看) ===')
seq = [60, 120, 60, 90]
for i, a in enumerate(seq):
    r = post('/api/servo/jog', {'chord_angle': a, 'ensure_mode': (i == 0)})
    print('  jog -> %3d deg : %s' % (a, r[:200]))
    time.sleep(2.5)

print('=== 2) 拨弦舵机走动: 用归位批帧推 6 路到收缩位 ===')
print('  home:', post('/api/servo/home', {'hold': True})[:200])
time.sleep(1.0)

print('=== 3) 最终配置 ===')
print(get('/api/servo/home/config'))
print('=== done ===')
