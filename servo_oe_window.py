# servo_oe_window.py -- OE (G47) 判决窗口
# 交替 使能/待机，供万用表观察 PCA9685 的 OE 针电压是否随固件变化。
#   ON  (使能) -> 固件把 G47 拉低 -> OE 应为 ~0V
#   OFF (待机) -> 固件把 G47 抬高 -> OE 应为 ~3.3V
# 若读数全程恒定不动 => G47 到 OE 这根线没接通。
import json
import time
import urllib.request

BASE = 'http://127.0.0.1:5000'


def post(path, payload=None):
    req = urllib.request.Request(BASE + path, method='POST',
                                 data=json.dumps(payload or {}).encode(),
                                 headers={'Content-Type': 'application/json'})
    try:
        return urllib.request.urlopen(req, timeout=30).read().decode()[:70].replace('\n', ' ')
    except Exception as e:
        return 'ERR %s' % e


def stamp():
    return time.strftime('%H:%M:%S')


def hold_enabled(dur, ang0=90):
    """使能并保持：每 4 秒轻推一次角度，防止空闲自动松力矩。"""
    ang = ang0
    t0 = time.time()
    post('/api/servo/jog', {'chord_angle': ang, 'ensure_mode': True})
    while time.time() - t0 < dur:
        time.sleep(4.0)
        if time.time() - t0 >= dur:
            break
        ang = ang0 if ang != ang0 else ang0 + 1
        post('/api/servo/jog', {'chord_angle': ang, 'ensure_mode': False})


print('=== OE JUDGE WINDOW START ===')
print('probe: BLACK -> PCA GND, RED -> PCA OE   (DC 20V)')
print('')

segs = [
    ('OFF  standby', 'off', 20),
    ('ON   ENABLED', 'on', 35),
    ('OFF  standby', 'off', 15),
    ('ON   ENABLED', 'on', 35),
    ('OFF  standby', 'off', 15),
]

for name, mode, dur in segs:
    if mode == 'on':
        print('[%s] >>> %-14s (%2ds)  expect OE ~0.0V' % (stamp(), name, dur))
        hold_enabled(dur, 90)
        print('[%s] <<< %s END' % (stamp(), name))
    else:
        r = post('/api/servo/release')
        print('[%s] >>> %-14s (%2ds)  expect OE ~3.3V  resp=%s' % (stamp(), name, dur, r))
        time.sleep(dur)

print('')
print('[%s] restore standby' % stamp())
print(post('/api/servo/release'))
print('=== OK ===')
