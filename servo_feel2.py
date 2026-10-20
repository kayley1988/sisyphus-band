# servo_feel2.py -- 手感判决窗口 v2
# 10s 准备 -> 3 轮 [ HOLD 20s (含 90->110->70->90 缓慢摆动, 每步 5s) / FREE 20s ]
# 每 5 秒打印心跳，便于对着时间轴回忆手感。
#   紧 = 通电+有效PWM（且能看到缓慢摆动）
#   松 = OE 切断、无输出，可自由转动
import json
import time
import urllib.request

BASE = 'http://127.0.0.1:5000'


def post(path, payload=None):
    req = urllib.request.Request(BASE + path, method='POST',
                                 data=json.dumps(payload or {}).encode(),
                                 headers={'Content-Type': 'application/json'})
    try:
        return urllib.request.urlopen(req, timeout=30).read().decode()[:60].replace('\n', ' ')
    except Exception as e:
        return 'ERR %s' % e


def stamp():
    return time.strftime('%H:%M:%S')


def release(retries=3):
    for _ in range(retries):
        r = post('/api/servo/release')
        if not r.startswith('ERR'):
            return r
        time.sleep(1.0)
    return r


print('=== FEEL TEST v2 START ===')
print('hand: on ANY servo gear, no force, just feel the resistance')
print('')

print('[%s] PREP (10s) -- put your hand on a gear NOW' % stamp())
for k in range(2):
    time.sleep(5.0)
    print('    prep %ds left' % (10 - (k + 1) * 5))

seq = [90, 110, 70, 90]

for rnd in range(3):
    # ---------- HOLD 20s ----------
    print('[%s] R%d >>> HOLD 20s  (feel TIGHT, and watch the gear move)' % (stamp(), rnd + 1))
    t0 = time.time()
    i = 0
    while time.time() - t0 < 20:
        ang = seq[i % len(seq)]
        post('/api/servo/jog', {'chord_angle': ang, 'ensure_mode': (i == 0)})
        print('      jog -> %d deg   (hold %ds left)' % (ang, int(20 - (time.time() - t0))))
        time.sleep(5.0)
        i += 1
    # ---------- FREE 20s ----------
    print('[%s] R%d >>> FREE 20s  (feel LOOSE, free to turn by hand)' % (stamp(), rnd + 1))
    release()
    t0 = time.time()
    while time.time() - t0 < 20:
        time.sleep(5.0)
        print('      free %ds left' % int(20 - (time.time() - t0)))

print('')
print('[%s] restore standby' % stamp())
print(release())
print('=== OK ===')
