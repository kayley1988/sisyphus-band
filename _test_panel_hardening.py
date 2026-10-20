#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_test_panel_hardening.py — servo_panel.py 加固项的离线回归测试（PC 上跑，不碰硬件）。

覆盖 2026-10-12 三处加固：
  ① 默认只监听 127.0.0.1（HOST 常量）
  ② cfg_put 原子写：正常替换 + 生成 .bak + 写坏时原文件完好
  ③ 串口互斥：_send 帧级锁（RLock 可重入，serial_guard 内再 _send 不自杀）
     main_is_playing() 主程序不通时 fail-open（放行）

用法: python3 _test_panel_hardening.py
"""
import os
import sys
import types
import tempfile
import threading

# ── 1) 先把板子专有依赖打桩，好在 PC 上 import servo_panel ──
_fc = types.ModuleType('frame_codec')


class _Frame:
    def __init__(self, *a):
        self.a = a

    def serialize(self):
        return bytes([0xAA]) + bytes(str(self.a), 'ascii')


_fc.make_servo_single = lambda *a: _Frame('S', *a)
_fc.make_servo_ctrl = lambda *a: _Frame('C', *a)
_fc.make_mode_switch = lambda *a: _Frame('M', *a)
sys.modules['frame_codec'] = _fc

_ft = types.ModuleType('frame_types')


class EspMode:
    NORMAL = 0


_ft.EspMode = EspMode
sys.modules['frame_types'] = _ft

_fl = types.ModuleType('flask')


class _App:
    def __init__(self, *a, **k):
        pass

    def route(self, *a, **k):
        def deco(f):
            return f
        return deco

    def run(self, *a, **k):
        pass


_fl.Flask = _App
_fl.request = types.SimpleNamespace(args={})
_fl.jsonify = lambda *a, **k: {'json': a}
_fl.Response = lambda *a, **k: ('resp', a)
sys.modules['flask'] = _fl

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import servo_panel as sp          # noqa: E402

OK = []
FAIL = []


def check(name, cond, extra=''):
    (OK if cond else FAIL).append(name)
    print(('  PASS  ' if cond else '  FAIL  ') + name + (('  ' + str(extra)) if extra else ''))


print('=== ① 默认监听地址 ===')
check('HOST 默认 127.0.0.1', sp.HOST == '127.0.0.1', sp.HOST)
_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'servo_panel.py'),
            encoding='utf-8').read()
check('不再硬编码 0.0.0.0', "app.run(host='0.0.0.0'" not in _src)
check('main() 用变量 host', 'app.run(host=host' in _src)
check('支持 --host 放开', "'--host'" in _src)

print('=== ② cfg_put 原子写 ===')
td = tempfile.mkdtemp(prefix='servo_panel_test_')
sp.CFG = os.path.join(td, 'servo_config.csv')
with open(sp.CFG, 'w', encoding='utf-8') as f:
    f.write('# comment\n')
    f.write('home_chord_angle,90\n')
    f.write('null_offset,0,0,0,0,0,0,0,0,0,0,0,0\n')
    for i in range(6):
        f.write('strum_angles_%d,20,60\n' % (i + 1))
    for s in range(6):
        for fr in range(4):
            f.write('press_offset_%d_%d,10\n' % (s + 1, fr + 1))
    f.write('chord_channel_mapping,PWM0,PWM1,PWM2,PWM3,PWM4,PWM5,PWM6,PWM7,PWM8,PWM9,PWM10,PWM11\n')
    f.write('strum_channel_mapping,PCA0,PCA1,PCA2,PCA3,PCA4,PCA5\n')
    f.write('keep_me,42\n')

cfg = sp.cfg_get()
cfg['null_offset'][0] = 7.5
cfg['home_chord_angle'] = 88
n = sp.cfg_put(cfg)
check('写回返回键数 >0', n > 0, n)
check('.bak 已生成', os.path.exists(sp.CFG + '.bak'))
check('无残留 .tmp', not os.path.exists(sp.CFG + '.tmp'))
after = sp.cfg_get()
check('新值已生效(home=88)', abs(after['home_chord_angle'] - 88) < 1e-6, after['home_chord_angle'])
check('新值已生效(null0=7.5)', abs(after['null_offset'][0] - 7.5) < 1e-6, after['null_offset'][0])
txt = open(sp.CFG, encoding='utf-8').read()
check('未知键 keep_me 保留', 'keep_me,42' in txt)
check('注释行保留', '# comment' in txt)

# 模拟写坏：把临时目录设成只读写不进去 → 原文件必须完好
good = open(sp.CFG, encoding='utf-8').read()
os.chmod(sp.CFG, 0o444)
ro = sp.CFG + '.tmp'
os.makedirs(ro)                       # 让 open(tmp,'w') 因是目录而失败
try:
    sp.cfg_put(cfg)
    wrote_err = False
except Exception:
    wrote_err = True
check('写入失败时抛异常(不静默)', wrote_err)
check('失败后原 csv 未被破坏', open(sp.CFG, encoding='utf-8').read() == good)
os.rmdir(ro)
os.chmod(sp.CFG, 0o644)

print('=== ③ 串口互斥 / 播放守卫 ===')
check('_io_lock 可重入', type(sp._io_lock).__name__ == 'RLock', type(sp._io_lock).__name__)

# serial_guard 内再 _send（模拟 bulk 循环）不应死锁
sent = []
sp._fd = 1


def _fake_send(frame):
    with sp._io_lock:
        sent.append(frame.serialize())
    return len(frame.serialize())


# 用真实 _send 会写 fd=1；这里换掉 fd 指向内存不可行 → 直接测 RLock 重入
done = threading.Event()


def _nested():
    with sp.serial_guard():
        with sp._io_lock:
            pass
    done.set()


t = threading.Thread(target=_nested, daemon=True)
t.start()
check('serial_guard 内再取 _io_lock 不死锁', done.wait(2.0))

# main_is_playing：5000 不通 → False（fail-open）
check('主程序不通时 fail-open', sp.main_is_playing() is False)

print('=== ④ 端到端：假主程序回 playing=true 时被 409 挡住 ===')
import json as _json                                            # noqa: E402
import http.server as _hs                                       # noqa: E402

_state = {'playing': True}


class _H(_hs.BaseHTTPRequestHandler):
    def do_GET(self):
        body = _json.dumps({'playing': _state['playing'], 'song': '稻香'}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


_srv = _hs.HTTPServer(('127.0.0.1', 0), _H)
_port = _srv.server_address[1]
threading.Thread(target=_srv.serve_forever, daemon=True).start()

sp.MAIN = 'http://127.0.0.1:%d' % _port
sp.PLAYING_API = sp.MAIN + '/api/songs/playing'
# 串口在 PC 上不存在（且 os.O_NOCTTY 是 Unix 专有）→ 把 fd() 直接指向一个临时文件
_tty_fh = open(os.path.join(td, 'fake_tty.bin'), 'wb')
sp.fd = lambda: _tty_fh.fileno()
sp._fd = _tty_fh.fileno()

sp.request = types.SimpleNamespace(args={})                     # 无 force
res_playing = sp.api_move()
check('演奏中 /api/move 被挡', isinstance(res_playing, tuple) and res_playing[1] == 409,
      res_playing if not isinstance(res_playing, tuple) else res_playing[1])

sp.request = types.SimpleNamespace(args={'tag': 'PWM0', 'ang': '100', 'force': '1'})
res_force = sp.api_move()
check('?force=1 可强过（真发到串口）', not (isinstance(res_force, tuple) and res_force[1] == 409),
      res_force)

_state['playing'] = False
sp.request = types.SimpleNamespace(args={'tag': 'PWM0', 'ang': '100'})
res_idle = sp.api_move()
check('非演奏时放行', not (isinstance(res_idle, tuple) and res_idle[1] == 409), res_idle)

_forced_bytes = open(os.path.join(td, 'fake_tty.bin'), 'rb').read()
check('强制路径确实写了串口帧', len(_forced_bytes) > 0, '%d bytes' % len(_forced_bytes))

_srv.shutdown()

print('\n==== 结果: %d 通过 / %d 失败 ====' % (len(OK), len(FAIL)))
if FAIL:
    print('失败项: ' + ', '.join(FAIL))
sys.exit(1 if FAIL else 0)
