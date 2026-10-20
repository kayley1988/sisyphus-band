# 舵机供电判定：对比 失能 / 使能 / 摆动 三个状态下的电流读数
# 若舵机有电，「使能/摆动」时电流应明显高于「失能」
# 用法（板上）: python3 /tmp/servo_power_probe.py
import json
import time
import urllib.request

BASE = 'http://127.0.0.1:5000'


def post(path, payload=None):
    req = urllib.request.Request(BASE + path, method='POST',
                                 data=json.dumps(payload or {}).encode(),
                                 headers={'Content-Type': 'application/json'})
    try:
        return urllib.request.urlopen(req, timeout=20).read().decode()[:100]
    except Exception as e:
        return 'ERR %s' % e


def sample(sec=2.5):
    try:
        r = urllib.request.urlopen(BASE + '/api/power/stream', timeout=10)
    except Exception as e:
        return 'ERR %s' % e
    t0 = time.time()
    out = []
    for line in r:
        s = line.decode('utf-8', 'ignore').strip()
        if s.startswith('data:'):
            try:
                out.append(json.loads(s[5:]))
            except Exception:
                pass
        if time.time() - t0 > sec:
            break
    try:
        r.close()
    except Exception:
        pass
    if not out:
        return 'no data'
    n = len(out)
    return 'n=%-4d 电压=%.3fV  和弦电流=%.4fA  拨弦电流=%.4fA  温度=%.1f' % (
        n, sum(o['v'] for o in out) / n, sum(o['cc'] for o in out) / n,
        sum(o['sc'] for o in out) / n, sum(o['t'] for o in out) / n)


print('A) 保持力矩中（当前状态） : ' + sample())
print('B) 发 DISABLE 失能        : ' + post('/api/servo/release'))
time.sleep(2.0)
print('   失能后                 : ' + sample())
print('C) ENABLE + 归位 90       : ' + post('/api/servo/jog', {'chord_angle': 90, 'ensure_mode': True}))
time.sleep(2.0)
print('   使能后（有保持力矩）   : ' + sample())
print('D) 摆动到 60              : ' + post('/api/servo/jog', {'chord_angle': 60, 'ensure_mode': False}))
print('   摆动过程               : ' + sample(2.0))
time.sleep(1.0)
print('E) 回 90 + 保持力矩       : ' + post('/api/servo/home', {'hold': True}))
time.sleep(1.5)
print('   回位后                 : ' + sample())
print('=== done ===')
