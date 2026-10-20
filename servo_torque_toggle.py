# 力矩开关循环：判定"指令是否真的到达固件"
# 每轮: 释放力矩(松) 5s -> 使能+归位(紧) 5s，共 3 轮
# 用户手放在齿轮上，若能感到"松-紧-松-紧"节奏，则指令确实到达了固件
import json
import time
import urllib.request

BASE = 'http://127.0.0.1:5000'


def post(path, payload=None):
    req = urllib.request.Request(BASE + path, method='POST',
                                 data=json.dumps(payload or {}).encode(),
                                 headers={'Content-Type': 'application/json'})
    try:
        return urllib.request.urlopen(req, timeout=20).read().decode()[:90]
    except Exception as e:
        return 'ERR %s' % e


for i in range(5):
    print('[%s] 第%d轮 >>> 释放力矩（松）5 秒' % (time.strftime('%H:%M:%S'), i + 1))
    r1 = post('/api/servo/release')
    time.sleep(5.0)

    print('[%s] 第%d轮 >>> 使能 + 归位90（紧）5 秒' % (time.strftime('%H:%M:%S'), i + 1))
    r2 = post('/api/servo/jog', {'chord_angle': 90, 'ensure_mode': True})
    time.sleep(5.0)
    print('     release=%s | jog=%s' % (r1[:40], r2[:40]))

print('[%s] 结束，保持力矩' % time.strftime('%H:%M:%S'))
print(post('/api/servo/home', {'hold': True}))
print('=== done ===')
