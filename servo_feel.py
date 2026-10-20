# servo_feel.py -- 手感判决窗口
# 3 轮 [保持力矩 15s] <-> [失能 15s]
# 手轻捏任意一个舵机齿轮/齿条：
#   使能段应感到"紧、有阻力、推不动"
#   失能段应感到"松、可自由转动"
# 若全程手感一样 => 舵机根本没得到有效 PWM 或没供电
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


ANG = 90


def seg_on(dur):
    ang = ANG
    post('/api/servo/jog', {'chord_angle': ang, 'ensure_mode': True})
    t0 = time.time()
    while time.time() - t0 < dur:
        time.sleep(4.0)
        if time.time() - t0 >= dur:
            break
        ang = ANG if ang != ANG else ANG + 2
        post('/api/servo/jog', {'chord_angle': ang, 'ensure_mode': False})


print('=== TORQUE FEEL TEST START ===')
print('')
for i in range(3):
    print('[%s] R%d >>> HOLD  (15s)  gear should feel TIGHT / resist hand' % (stamp(), i + 1))
    seg_on(15)
    print('[%s] R%d >>> FREE  (15s)  gear should feel LOOSE / free to turn' % (stamp(), i + 1))
    post('/api/servo/release')
    time.sleep(15)

print('')
print('[%s] restore standby' % stamp())
print(post('/api/servo/release'))
print('=== OK ===')
