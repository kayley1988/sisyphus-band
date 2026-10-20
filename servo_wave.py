# servo_wave.py -- 大幅慢摆验证（90 -> 110 -> 70 -> 110 -> 70 -> 90）
import json
import time
import urllib.request

BASE = 'http://127.0.0.1:5000'


def post(p, payload=None):
    req = urllib.request.Request(BASE + p, method='POST',
                                 data=json.dumps(payload or {}).encode(),
                                 headers={'Content-Type': 'application/json'})
    try:
        return urllib.request.urlopen(req, timeout=20).read().decode()[:120].replace('\n', ' ')
    except Exception as e:
        return 'ERR %s' % e


print('=== WAVE START ===')
seq = [90, 110, 70, 110, 70, 90]
for i, a in enumerate(seq):
    r = post('/api/servo/jog', {'chord_angle': a, 'ensure_mode': (i == 0)})
    print('[%s] %3d deg -> %s' % (time.strftime('%H:%M:%S'), a, r))
    time.sleep(3.0)
print('[%s] home hold=true' % time.strftime('%H:%M:%S'))
print(post('/api/servo/home', {'hold': True}))
print('=== OK ===')
