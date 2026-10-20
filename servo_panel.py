#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""servo_panel.py v2 — 舵机校准台（指板可视化 + 旋钮卡片，本机可用版）

与作者原版 /servo 页同样式的皮，但功能全部落在本机可用的通路上：
  - 读配置：直接读 /root/guitarist_app/config/servo_config.csv（实时，不缓存）
  - 存配置：① 写回 csv  ② 通知主程序 guitar.service(localhost:5000) 应用并同步 ESP32
  - 单通道驱动：自己开 /dev/ttyS3 注入单通道帧（原版页缺的能力）
    · 按压点：按官方公式算角度（含弦4-6镜像反转）后驱动
    · 零点偏移：驱动到 home_chord_angle + null_offset 预览
    · 拨弦：驱动到 strum_angles 的低点/高点

安全与稳健（2026-10-12 加固）
────────────────────────────────
  1) 默认只监听 127.0.0.1 —— PC 侧本来就经 adb forward 访问 127.0.0.1:5002，
     局域网里别人摸不到这个能直接驱舵机的页面。
     要放开（手机同 WiFi 用 / 隧道）：`SERVO_PANEL_HOST=0.0.0.0 python3 servo_panel.py` 或 `--host 0.0.0.0`
  2) 写 csv 原子化：先写 .tmp → fsync → 备份 .bak → os.replace，
     中途断电也不会留下半截配置。
  3) 串口不打架：与主程序 guitar.service 共用 /dev/ttyS3，
     主程序在演奏时一律拒绝驱动（409），只读配置不受影响。
     帧级另加线程锁，避免两路请求把一帧写花。想强驱可加 `?force=1`。

用法: python3 servo_panel.py [port] [--host HOST]      # 默认 5002 / 127.0.0.1
"""
import os
import sys
import time
import json
import shutil
import threading
import contextlib
import urllib.request

try:
    import fcntl                      # Linux 专用（板子上有）；PC 上仅做语法检查时为 None
except ImportError:                   # pragma: no cover
    fcntl = None

sys.path.insert(0, '/root/guitarist_app/src/guitarist')
from frame_codec import make_servo_single, make_servo_ctrl, make_mode_switch
from frame_types import EspMode
from flask import Flask, request, jsonify, Response

PORT_DEV = '/dev/ttyS3'
CFG = '/root/guitarist_app/config/servo_config.csv'
HTML = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'servo_panel.html')
OBS_PATH = '/root/guitarist_app/obs_result.json'
MAIN = 'http://127.0.0.1:5000'          # 主程序（guitar.service）

#: 默认只监听本机（PC 经 adb forward 访问 127.0.0.1:5002）；用 SERVO_PANEL_HOST 放开
HOST = os.environ.get('SERVO_PANEL_HOST', '127.0.0.1')
#: 跨进程串口写锁（板子上多实例/重启竞争时兜底）
LOCK_PATH = '/tmp/servo_panel.lock'
#: 主程序「是否正在演奏」查询口
PLAYING_API = MAIN + '/api/songs/playing'

app = Flask(__name__)

# 官方公式：fret -> (ch0 方向, ch0 press 索引, ch1 方向, ch1 press 索引)
FRET_OFFSETS = {
    0: (0, None, 0, None),
    1: (+1, 0, 0, None),
    2: (-1, 1, 0, None),
    3: (0, None, +1, 2),
    4: (0, None, -1, 3),
}
# 弦4/5/6（下标>=3）舵机镜像安装 → 1↔2 品、3↔4 品交换
SWAP = {
    1: (-1, 1, 0, None),
    2: (+1, 0, 0, None),
    3: (0, None, -1, 3),
    4: (0, None, +1, 2),
}

#: 按压 / 松弦的「零位角」——下位机与上位机播放器统一用这个值，**不要随意改**：
#:   · 上位机 servo_controller.py:_compute_chord_angles_only()  zero = 90
#:     （注释原文：逻辑与 ESP32 chord_engine.cpp compute_angles() 完全一致）
#:   · 上位机 esp32_player.py                                 松弦零位 = 90 + null_offset
#:   · 播放时由 Python 算好 18 路绝对角 → make_servo_batch 下发，所以这两个 90 必须一致
#: 注意：装机角 home_chord_angle 是**归位/停靠姿态**（一键归位、装机器件用），
#:       它**不参与**按压运算。装机角≠90 时，面板显示的「基准角 = 装机角+零点偏移」
#:       会比固件实际使用的 (90+零点偏移) 偏 (装机角-90)。见 _test_press_formula_parity.py。
PRESS_ZERO = 90.0

_fd = None
_busy = threading.Lock()        # 走马灯等「整段动作」的占用标志
_io_lock = threading.RLock()    # 单帧写互斥（可重入：serial_guard 内再 _send 不自杀）
_lock_fd = None


# ───────────────────────── 并发 / 互斥 ─────────────────────────

def _lock_fd_get():
    """跨进程写锁的文件描述符（用文件锁兜住多实例）。失败返回 -1。"""
    global _lock_fd
    if _lock_fd is None:
        try:
            _lock_fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o600)
        except OSError:
            _lock_fd = -1
    return _lock_fd


@contextlib.contextmanager
def serial_guard():
    """把「一整段可能多帧」的舵机动作串行化：进程内线程锁 + 跨进程 flock。"""
    with _io_lock:
        fd = _lock_fd_get()
        locked = False
        if fcntl is not None and fd and fd > 0:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                locked = True
            except OSError:
                pass
        try:
            yield
        finally:
            if locked:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass


def main_is_playing():
    """问主程序是否在演奏。任何异常（主程序停了/端口不通）一律当作「没在演奏」放行。"""
    try:
        req = urllib.request.Request(PLAYING_API, method='GET',
                                     headers={'Accept': 'application/json'})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 强制直连，绕系统代理
        with opener.open(req, timeout=1.5) as r:
            j = json.loads(r.read(2000).decode('utf-8', 'ignore') or '{}')
    except Exception:
        return False
    if not isinstance(j, dict):
        return False
    for k in ('playing', 'is_playing', 'active', 'in_progress'):
        if k in j:
            return bool(j[k])
    if str(j.get('state', j.get('status', ''))).lower() in ('playing', 'play', 'running', 'busy'):
        return True
    if str(j.get('song', j.get('current_song', '')) or '').strip():
        return True
    return False


def _guard_drive():
    """驱动类接口统一前置：主程序在演奏就拒绝（?force=1 可强过）。返回响应或 None。"""
    if request.args.get('force') in ('1', 'true', 'yes'):
        return None
    if main_is_playing():
        return jsonify({'ok': False, 'err': 'playing',
                        'msg': '主程序正在演奏，已拒绝驱动舵机（避免串口冲突）。'
                               '想强制驱动请在 URL 后加 ?force=1'}), 409
    return None


# ───────────────────────── 配置读写 ─────────────────────────

def _read_rows():
    rows = []
    with open(CFG, encoding='utf-8') as f:
        for ln in f:
            ln = ln.rstrip('\n').strip()
            if not ln or ln.startswith('#'):
                continue
            p = [x.strip() for x in ln.split(',')]
            rows.append((p[0], p[1:]))
    return rows


def cfg_get():
    rows = dict(_read_rows())
    null_offset = [float(x) for x in rows['null_offset'][:12]]
    while len(null_offset) < 12:
        null_offset.append(0.0)
    strum_angles = []
    for i in range(6):
        v = rows['strum_angles_%d' % (i + 1)]
        strum_angles.append([float(v[0]), float(v[1])])
    press_offsets = []
    for s in range(6):
        row = []
        for f in range(4):
            row.append(float(rows['press_offset_%d_%d' % (s + 1, f + 1)][0]))
        press_offsets.append(row)
    chord_map = rows['chord_channel_mapping'][:12]
    strum_map = rows['strum_channel_mapping'][:6]
    home = float(rows.get('home_chord_angle', ['90'])[0])
    return {
        'null_offset': null_offset,
        'strum_angles': strum_angles,
        'press_offsets': press_offsets,
        'chord_map': chord_map,
        'strum_map': strum_map,
        'home_chord_angle': home,
    }


def _fmt(v):
    v = float(v)
    return ('%g' % v)


def cfg_put(o):
    """把新值写回 csv（保留原文件行序、注释行、空行与其它未知键），返回写入的键数。"""
    # 用原始行处理（不走 _read_rows，后者会丢掉注释/空行 → 保存一次注释就没了）
    raw = []
    with open(CFG, encoding='utf-8') as f:
        for ln in f:
            raw.append(ln.rstrip('\n'))

    upd = {}
    upd['null_offset'] = [_fmt(x) for x in o['null_offset'][:12]]
    for i in range(6):
        upd['strum_angles_%d' % (i + 1)] = [_fmt(o['strum_angles'][i][0]), _fmt(o['strum_angles'][i][1])]
    for s in range(6):
        for f in range(4):
            upd['press_offset_%d_%d' % (s + 1, f + 1)] = [_fmt(o['press_offsets'][s][f])]
    if o.get('chord_map'):
        upd['chord_channel_mapping'] = list(o['chord_map'][:12])
    if o.get('strum_map'):
        upd['strum_channel_mapping'] = list(o['strum_map'][:6])
    if o.get('home_chord_angle') is not None:
        upd['home_chord_angle'] = [_fmt(o['home_chord_angle'])]

    n = 0
    out = []
    seen = set()
    for ln in raw:
        s = ln.strip()
        if not s or s.startswith('#'):        # 注释/空行原样保留
            out.append(ln)
            continue
        k = s.split(',')[0].strip()
        if k in upd:
            out.append(k + ',' + ','.join(upd[k]))
            seen.add(k)
            n += 1
        else:
            out.append(ln)                    # 未知键原样保留
    for k, v in upd.items():
        if k not in seen:
            out.append(k + ',' + ','.join(v))
            n += 1
    # ── 原子写：.tmp → fsync → 备份 .bak → os.replace ──
    #    任何一步中途断电，原 csv 都还是完整的旧版本，不会变成半截文件。
    tmp = CFG + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        for ln in out:
            f.write(ln + '\n')
        f.flush()
        os.fsync(f.fileno())
    if os.path.exists(CFG):
        try:
            shutil.copy2(CFG, CFG + '.bak')
        except OSError:
            pass
    os.replace(tmp, CFG)
    return n


def _post_main(path, payload, timeout=6):
    """通知主程序应用配置（失败不影响本地 csv 已写入）。"""
    try:
        req = urllib.request.Request(
            MAIN + path,
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'}, method='POST')
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.getcode(), r.read(300).decode('utf-8', 'ignore')
    except Exception as e:
        return 0, str(e)


# ───────────────────────── 串口驱动 ─────────────────────────

def fd():
    global _fd
    if _fd is None:
        _fd = os.open(PORT_DEV, os.O_WRONLY | os.O_NOCTTY | os.O_NONBLOCK)
    return _fd


def _send(frame):
    b = frame.serialize()
    off = 0
    t0 = time.time()
    with _io_lock:                      # 一帧必须整段写完，别和另一路请求交错
        while off < len(b) and time.time() - t0 < 0.5:
            try:
                off += os.write(fd(), b[off:])
            except BlockingIOError:
                time.sleep(0.004)
            except OSError:
                break
    return off


def frame_args(tag):
    if tag.startswith('PWM'):
        return (1, int(tag[3:]))
    return (0, int(tag[3:]))


def move(tag, ang):
    ang = max(0, min(180, int(round(float(ang)))))
    ct, ch = frame_args(tag)
    _send(make_servo_single(ct, ch, ang))
    return ang


def ensure():
    _send(make_mode_switch(EspMode.NORMAL))
    time.sleep(0.04)
    _send(make_servo_ctrl(0x01))
    time.sleep(0.04)


def _clamp_ang(a):
    """夹到 [0,180] 并按面板统一精度取 0.1°（与前端显示/驱动精度一致）。"""
    return round(max(0.0, min(180.0, float(a))), 1)


def angles_for_press(c, s, f):
    """返回该弦该品的 (ch0角, ch1角)（已含 null_offset 与弦4-6反转）。

    基准角 = **PRESS_ZERO(90) + null_offset** —— 与固件/上位机播放器同一口径
    （见文件顶部 PRESS_ZERO 说明）。这里就是「官方公式」的复现，
    用来核对面板标定出来的压深在播放时会不会落在同一个角度。

    ⚠️ 与面板显示的「基准角（装机角+零点偏移）」**不是同一个数**：装机角只决定
    归位/停靠姿态，不参与按压。装机角≠90 时两者相差 (装机角-90)，
    这会直接反映为「面板标定好、播放时压不到底/压过头」。
    """
    s0, i0, s1, i1 = FRET_OFFSETS.get(f, (0, None, 0, None))
    if s >= 3 and f in SWAP:
        s0, i0, s1, i1 = SWAP[f]
    b0 = PRESS_ZERO + c['null_offset'][2 * s]
    b1 = PRESS_ZERO + c['null_offset'][2 * s + 1]
    a0 = b0 + (s0 * c['press_offsets'][s][i0] if i0 is not None else 0)
    a1 = b1 + (s1 * c['press_offsets'][s][i1] if i1 is not None else 0)
    return (_clamp_ang(a0), _clamp_ang(a1))


def base_angles(c, s):
    """该弦两片的**松弦零位**（释放角）= PRESS_ZERO(90) + 零点偏移。

    这是硬件口径，三处一致：
      · 固件 chord_engine.cpp / 上位机 servo_controller.py:_compute_chord_angles_only()
        —— zero = 90，松弦（frets 全 0）= 90 + null_offset；
      · 主程序自己的标定公式 servo_controller.py:890
        `new_null_offset = real_zero_angle - 90.0` —— 零点偏移就是相对 90 定义的；
      · 本面板左右模式的「零点」滑条（前端 lrNullDrive）。

    ⚠️ 不要写成「装机角 + 零点偏移」。装机角 home_chord_angle 只被
    esp32_home_servos() 的「一键归位」使用，而且是 **12 路统一一个平角、不加
    null_offset**（见 esp32_player.py:esp32_home_servos）；它与松弦零位是两个
    概念。写成「装机角+偏移」会在装机角≠90 时凭空偏 (装机角-90)，
    表现为「面板标定完、播放时压不到底/压过头」。

    同样按 0.1° 精度输出 —— 与 null_offset 的标定精度、前端 lrNullDrive()
    的驱动角保持一致（旧实现 int() 取整会与前端差最多 0.5°）。
    """
    return (_clamp_ang(PRESS_ZERO + c['null_offset'][2 * s]),
            _clamp_ang(PRESS_ZERO + c['null_offset'][2 * s + 1]))


def tiles(c):
    out = []
    for i, tag in enumerate(c['chord_map']):
        xian = i // 2 + 1
        pg = '12' if i % 2 == 0 else '34'
        out.append({'tag': tag, 'group': 'chord', 'idx': i,
                    'name': '弦%d·品%s组' % (xian, pg),
                    'sub': 'CHORD#%d · ch%d（管品%s）' % (i + 1, i % 2, pg),
                    'base': base_angles(c, i // 2)[i % 2]})
    for i, tag in enumerate(c['strum_map']):
        out.append({'tag': tag, 'group': 'strum', 'idx': i,
                    'name': '拨弦%d' % (i + 1),
                    'sub': 'STRUM#%d 弦%d' % (i + 1, i + 1),
                    'base': int(round(c['strum_angles'][i][0]))})
    return out


# ───────────────────────── 路由 ─────────────────────────

@app.route('/')
def index():
    with open(HTML, encoding='utf-8') as f:
        return Response(f.read(), mimetype='text/html')


@app.route('/api/cfg', methods=['GET'])
def api_cfg_get():
    try:
        c = cfg_get()
    except Exception as e:
        return jsonify({'ok': False, 'err': str(e)}), 500
    c['ok'] = True
    c['tags'] = tiles(c)
    return jsonify(c)


@app.route('/api/cfg', methods=['POST'])
def api_cfg_post():
    data = request.get_json(force=True, silent=True) or {}
    try:
        cur = cfg_get()
        for k in ('null_offset', 'strum_angles', 'press_offsets', 'chord_map', 'strum_map'):
            if k in data and data[k] is not None:
                cur[k] = data[k]
        if 'home_chord_angle' in data and data['home_chord_angle'] is not None:
            cur['home_chord_angle'] = float(data['home_chord_angle'])
        n = cfg_put(cur)
    except Exception as e:
        return jsonify({'ok': False, 'err': '写 csv 失败: %s' % e}), 500

    code, body = _post_main('/api/servo/config', {
        'null_offset': cur['null_offset'],
        'strum_angles': cur['strum_angles'],
        'press_offsets': cur['press_offsets'],
    })
    home_code, home_body = (0, 'skip')
    if 'home_chord_angle' in data and data['home_chord_angle'] is not None:
        home_code, home_body = _post_main('/api/servo/home',
                                          {'chord_angle': cur['home_chord_angle'],
                                           'hold': False, 'save': True})
    return jsonify({'ok': True, 'written': n,
                    'main': code, 'main_body': body[:200],
                    'home_main': home_code, 'home_body': home_body[:200]})


@app.route('/api/tiles')
def api_tiles():
    try:
        c = cfg_get()
    except Exception as e:
        return jsonify({'ok': False, 'err': str(e)}), 500
    return jsonify({'tiles': tiles(c), 'chord_home': c['home_chord_angle']})


@app.route('/api/move')
def api_move():
    g = _guard_drive()
    if g:
        return g
    tag = request.args.get('tag', '')
    c = cfg_get()
    all_tags = [t['tag'] for t in tiles(c)]
    if tag not in all_tags:
        return jsonify({'ok': False, 'err': 'bad tag'}), 400
    ang = move(tag, request.args.get('ang', 90))
    return jsonify({'ok': True, 'tag': tag, 'ang': ang})


@app.route('/api/press')
def api_press():
    """驱动某弦某品的按压（只动该品对应的那一片）。"""
    g = _guard_drive()
    if g:
        return g
    try:
        s = int(request.args.get('string', 1)) - 1     # 1..6
        f = int(request.args.get('fret', 1))           # 1..4
    except ValueError:
        return jsonify({'ok': False, 'err': 'bad args'}), 400
    c = cfg_get()
    a0, a1 = angles_for_press(c, s, f)
    tag0 = c['chord_map'][2 * s]
    tag1 = c['chord_map'][2 * s + 1]
    if f <= 2:
        ensure(); move(tag0, a0)
        return jsonify({'ok': True, 'tag': tag0, 'ang': a0, 'other': a1})
    ensure(); move(tag1, a1)
    return jsonify({'ok': True, 'tag': tag1, 'ang': a1, 'other': a0})


@app.route('/api/release_press')
def api_release_press():
    """把某弦的两片都退回归位角。"""
    g = _guard_drive()
    if g:
        return g
    try:
        s = int(request.args.get('string', 1)) - 1
    except ValueError:
        return jsonify({'ok': False, 'err': 'bad args'}), 400
    c = cfg_get()
    b0, b1 = base_angles(c, s)
    ensure()
    move(c['chord_map'][2 * s], b0)
    time.sleep(0.02)
    move(c['chord_map'][2 * s + 1], b1)
    return jsonify({'ok': True, 'b0': b0, 'b1': b1})


@app.route('/api/strum_drive')
def api_strum_drive():
    """驱动某路拨弦到指定角度（which=0 低点 / 1 高点，或 ang= 直接给）。"""
    g = _guard_drive()
    if g:
        return g
    try:
        i = int(request.args.get('idx', 1)) - 1
    except ValueError:
        return jsonify({'ok': False, 'err': 'bad idx'}), 400
    c = cfg_get()
    if 'ang' in request.args:
        ang = float(request.args.get('ang'))
    else:
        which = int(request.args.get('which', 0))
        ang = c['strum_angles'][i][which]
    ensure()
    tag = c['strum_map'][i]
    a = move(tag, ang)
    return jsonify({'ok': True, 'tag': tag, 'ang': a})


@app.route('/api/wiggle')
def api_wiggle():
    g = _guard_drive()
    if g:
        return g
    tag = request.args.get('tag', '')
    c = cfg_get()
    all_tags = [t['tag'] for t in tiles(c)]
    if tag not in all_tags:
        return jsonify({'ok': False, 'err': 'bad tag'}), 400
    swing = float(request.args.get('swing', 30))
    dwell = float(request.args.get('dwell', 0.35))
    base = int(request.args.get('base', 90))
    if not _busy.acquire(blocking=False):
        return jsonify({'ok': False, 'err': 'busy'}), 409
    try:
        hi = min(175, base + swing)
        lo = max(5, base - swing)
        ensure()
        for a in (base, hi, base, lo, base):
            move(tag, a)
            time.sleep(dwell)
    finally:
        _busy.release()
    return jsonify({'ok': True, 'tag': tag, 'base': base})


@app.route('/api/all')
def api_all():
    g = _guard_drive()
    if g:
        return g
    ang = request.args.get('ang', '90')
    c = cfg_get()
    with serial_guard():
        ensure()
        for t in tiles(c):
            move(t['tag'], ang)
            time.sleep(0.02)
    return jsonify({'ok': True, 'ang': ang})


@app.route('/api/home')
def api_home():
    """全部归位：与主程序「一键归位」同一口径（esp32_player.py:esp32_home_servos）——
    和弦 12 路统一回到 home_chord_angle 这个**平角（不加 null_offset）**，
    拨弦回到各自的 point_a。这样面板点「全部归位」= 主程序点「一键归位」。
    """
    g = _guard_drive()
    if g:
        return g
    c = cfg_get()
    ts = tiles(c)
    ha = _clamp_ang(c['home_chord_angle'])
    with serial_guard():
        ensure()
        for t in ts:
            move(t['tag'], ha if t['group'] == 'chord' else t['base'])
            time.sleep(0.02)
    return jsonify({'ok': True, 'home_chord': c['home_chord_angle'], 'n': len(ts)})


@app.route('/api/home_preview')
def api_home_preview():
    """拖动装机角滑块时的实时预览：和弦 12 片统一到该平角（与「一键归位」同口径，
    **不加 null_offset**），不动拨弦。"""
    g = _guard_drive()
    if g:
        return g
    ang = float(request.args.get('ang', 90))
    c = cfg_get()
    with serial_guard():
        ensure()
        for tag in c['chord_map']:
            move(tag, ang)
            time.sleep(0.015)
    return jsonify({'ok': True, 'ang': ang})


@app.route('/api/enable')
def api_enable():
    g = _guard_drive()
    if g:
        return g
    ensure()
    return jsonify({'ok': True})


@app.route('/api/release')
def api_release():
    g = _guard_drive()
    if g:
        return g
    _send(make_servo_ctrl(0x02))
    return jsonify({'ok': True})


@app.route('/api/save_obs', methods=['POST'])
def api_save_obs():
    data = request.get_json(force=True, silent=True) or {}
    try:
        with open(OBS_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    except Exception as e:
        return jsonify({'ok': False, 'err': str(e)}), 500
    n = sum(1 for v in data.values() if isinstance(v, dict) and str(v.get('obs', '')).strip())
    return jsonify({'ok': True, 'n': len(data), 'filled': n, 'path': OBS_PATH})


def main():
    args = sys.argv[1:]
    host = HOST
    if '--host' in args:                       # 显式放开监听（手机同 WiFi / 隧道用）
        i = args.index('--host')
        if i + 1 < len(args):
            host = args[i + 1]
            args = args[:i] + args[i + 2:]
    port = int(args[0]) if args else 5002
    c = cfg_get()
    print('servo_panel v2 on %s:%d  home=%s  chord=%s'
          % (host, port, c['home_chord_angle'], c['chord_map']), flush=True)
    if host != '127.0.0.1':
        print('⚠ 监听 %s（非本机）——本页面无鉴权且能直接驱动舵机，'
              '请确认所在网络可信，或前面套一层隧道/鉴权。' % host, flush=True)
    app.run(host=host, port=port, threaded=True)


if __name__ == '__main__':
    main()
