# fix_busy.py -- 解开卡住的「演奏中」状态，然后实测舵机是否恢复响应
import json
import time
import urllib.request

BASE = 'http://127.0.0.1:5000'


def get(p):
    try:
        return urllib.request.urlopen(BASE + p, timeout=8).read().decode()[:300]
    except Exception as e:
        return 'ERR %s' % e


def post(p, payload=None):
    req = urllib.request.Request(BASE + p, method='POST',
                                 data=json.dumps(payload or {}).encode(),
                                 headers={'Content-Type': 'application/json'})
    try:
        return urllib.request.urlopen(req, timeout=20).read().decode()[:200]
    except Exception as e:
        return 'ERR %s' % e


print('--- 1) BEFORE /api/songs/playing ---')
print(get('/api/songs/playing'))
print('')
print('--- 2) POST /api/songs/stop ---')
print(post('/api/songs/stop'))
time.sleep(3)
print('')
print('--- 3) AFTER /api/songs/playing ---')
print(get('/api/songs/playing'))
print('')
print('--- 4) POST /api/servo/jog 110 (should MOVE now) ---')
print(post('/api/servo/jog', {'chord_angle': 110, 'ensure_mode': True}))
time.sleep(2.5)
print('--- 5) POST /api/servo/jog 70  (should MOVE now) ---')
print(post('/api/servo/jog', {'chord_angle': 70, 'ensure_mode': False}))
time.sleep(2.5)
print('--- 6) POST /api/servo/jog 90  (back to home) ---')
print(post('/api/servo/jog', {'chord_angle': 90, 'ensure_mode': False}))
print('')
print('--- 7) POST /api/servo/home {hold:true} ---')
print(post('/api/servo/home', {'hold': True}))
print('--- END ---')
