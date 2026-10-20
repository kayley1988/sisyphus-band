# board_state_probe.py -- 读板子当前状态 + 试一次 jog 看是否被 409 挡住
import json
import urllib.request

BASE = 'http://127.0.0.1:5000'


def get(p):
    try:
        return urllib.request.urlopen(BASE + p, timeout=8).read().decode()[:500]
    except Exception as e:
        return 'ERR %s' % e


def post(p, payload=None):
    req = urllib.request.Request(BASE + p, method='POST',
                                 data=json.dumps(payload or {}).encode(),
                                 headers={'Content-Type': 'application/json'})
    try:
        return urllib.request.urlopen(req, timeout=15).read().decode()[:250]
    except Exception as e:
        return 'ERR %s' % e


print('--- GET /api/status ---')
print(get('/api/status'))
print('')
print('--- GET /api/servo/home/config ---')
print(get('/api/servo/home/config'))
print('')
print('--- POST /api/servo/jog {90, ensure_mode} ---')
print(post('/api/servo/jog', {'chord_angle': 90, 'ensure_mode': True}))
print('')
print('--- POST /api/servo/home {hold:true} ---')
print(post('/api/servo/home', {'hold': True}))
print('--- END ---')
