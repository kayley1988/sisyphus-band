#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_test_press_formula_parity.py — 按压角公式「前后端对拍」测试（PC 上跑，不碰硬件）。

背景：
  按压角公式在本项目里被实现了两遍 ——
    · 后端 servo_panel.py: angles_for_press()
    · 前端 servo_panel.html: pressCalc()
  改一处忘另一处，就会出现「面板里看着是压到位，实际驱动的角度不是那个」。
  本测试不复制公式，而是**直接取两份真实实现**跑同一个输入矩阵，逐个比对：
    · 后端：import servo_panel（打桩板端依赖）调用真实 angles_for_press()
    · 前端：从 servo_panel.html 正则抽出 pressCalc() 源码，交给 node 真跑
  任何一侧改了公式而另一侧没跟上，本测试立刻变红。

口径（本项目最容易踩的坑，务必记牢）：
  按压/松弦的基准角 = PRESS_ZERO(90) + 零点偏移 —— **不是「装机角 + 零点偏移」**。
    · 装机角 home_chord_angle 只用于主程序「一键归位」esp32_home_servos()，
      那里是 12 路统一平角、**不加** null_offset；
    · 零点偏移的定义是相对的 90（servo_controller.py:890 `real_zero_angle - 90.0`）。
  曾经前端用装机角当基准，导致装机角≠90 时零点与压深整体偏 (装机角-90)。

用法: python3 _test_press_formula_parity.py
退出码: 0=全部一致  1=存在不一致  3=缺 node 跳过（已在 deploy_panel.bat 里作为上板前置关卡）
"""
import os
import re
import sys
import json
import types
import random
import tempfile
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
NODE_CANDIDATES = [
    r'C:\Users\zhouqian\.workbuddy\binaries\node\versions\22.22.2-6\node.exe',
    'node',
]

OK = []
FAIL = []


def check(name, cond, detail=''):
    (OK if cond else FAIL).append(name)
    print(('  [PASS] ' if cond else '  [FAIL] ') + name + ('' if cond else '  -> ' + str(detail)))


# ── 1) 打桩板端依赖，import 真实 servo_panel ─────────────────────────────
def load_panel():
    _fc = types.ModuleType('frame_codec')

    class _Frame:
        def __init__(self, *a):
            self.a = a

        def serialize(self):
            return bytes([0xAA])

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

    class _RQ:
        args = {}

    _fl.Flask = _App
    _fl.request = _RQ()
    _fl.jsonify = lambda *a, **k: (a[0] if a else None)
    _fl.Response = lambda *a, **k: None
    sys.modules['flask'] = _fl

    sys.path.insert(0, HERE)
    import servo_panel                      # noqa: E402
    return servo_panel


# ── 2) 从 html 抽出 pressCalc 源码（花括号配对）───────────────────────────
def extract_press_calc(html_path):
    src = open(html_path, encoding='utf-8').read()
    m = re.search(r'function\s+pressCalc\s*\([^)]*\)\s*\{', src)
    if not m:
        raise SystemExit('未在 %s 中找到 pressCalc 定义' % html_path)
    i = m.end() - 1
    depth = 0
    for j in range(i, len(src)):
        if src[j] == '{':
            depth += 1
        elif src[j] == '}':
            depth -= 1
            if depth == 0:
                return src[m.start():j + 1]
    raise SystemExit('pressCalc 花括号不配对')


# ── 3) 造输入矩阵 ────────────────────────────────────────────────────────
def build_matrix(n_null_pat=6, n_press_pat=4):
    rng = random.Random(20261012)
    homes = [90.0, 89.0, 92.5, 85.0, 95.0]
    null_pool = [-8.0, -4.5, -1.0, 0.0, 1.5, 3.0, 6.5, 9.0]
    press_pool = [-6.0, 0.0, 4.5, 11.0, 17.5, 24.0, 30.0]

    nulls = [[rng.choice(null_pool) for _ in range(12)] for _ in range(n_null_pat)]
    press = []
    for _ in range(n_press_pat):
        press.append([[rng.choice(press_pool) for _ in range(4)] for _ in range(6)])

    cfgs = []
    for h in homes:
        for nl in nulls:
            for pr in press:
                cfgs.append({'home': h, 'null': nl, 'press': pr})
    return cfgs


# ── 4) 前端：真跑 node ───────────────────────────────────────────────────
JS_DRIVER = r'''
const fs = require('fs');
var PRESS_ZERO = 90;
var homeAngle = 90;
var dataNull = new Array(12).fill(0);
var dataPress = [];
for (let i = 0; i < 6; i++) dataPress.push([0, 0, 0, 0]);

/*__PRESS_CALC__*/

const cfgs = JSON.parse(fs.readFileSync(process.argv[2], 'utf8')).configs;
const out = [];
for (const c of cfgs) {
  homeAngle = c.home;
  for (let i = 0; i < 12; i++) dataNull[i] = c.null[i];
  for (let s = 0; s < 6; s++) for (let j = 0; j < 4; j++) dataPress[s][j] = c.press[s][j];
  const grid = [];
  for (let s = 0; s < 6; s++) {
    const row = [];
    for (let f = 1; f <= 4; f++) {
      const r = pressCalc(s, f);
      row.push({ ang: r.ang, dir: r.dir, pi: r.pi, ch: r.ch, base: r.base, p: r.p });
    }
    grid.push(row);
  }
  out.push(grid);
}
process.stdout.write(JSON.stringify(out));
'''


def run_node(press_calc_src, cfgs):
    node = None
    for cand in NODE_CANDIDATES:
        if cand == 'node' or os.path.exists(cand):
            node = cand
            break
    if node is None:
        return None                      # 无 node → 上层判为 SKIP（退出码 3）

    td = tempfile.mkdtemp(prefix='parity_')
    js = os.path.join(td, 'driver.js')
    dat = os.path.join(td, 'matrix.json')
    with open(js, 'w', encoding='utf-8') as f:
        f.write(JS_DRIVER.replace('/*__PRESS_CALC__*/', press_calc_src))
    with open(dat, 'w', encoding='utf-8') as f:
        json.dump({'configs': cfgs}, f)
    r = subprocess.run([node, js, dat], capture_output=True, text=True, encoding='utf-8')
    if r.returncode != 0:
        raise SystemExit('node 执行失败:\n' + (r.stderr or '')[:2000])
    return json.loads(r.stdout)


# ── 5) 后端：调真实函数 ──────────────────────────────────────────────────
def run_backend(sp, cfgs):
    out = []
    for c in cfgs:
        cfg = {
            'null_offset': c['null'],
            'press_offsets': c['press'],
            'home_chord_angle': c['home'],
            'chord_map': ['PWM%d' % i for i in range(12)],
            'strum_map': ['PWM%d' % (12 + i) for i in range(6)],
        }
        grid = []
        for s in range(6):
            row = []
            for f in range(1, 5):
                a0, a1 = sp.angles_for_press(cfg, s, f)
                row.append(a1 if f >= 3 else a0)      # f<=2 走 ch0，f>=3 走 ch1
            grid.append(row)
        out.append(grid)
    return out


def main():
    print('=' * 72)
    print('按压角公式 前后端对拍  (_test_press_formula_parity.py)')
    print('=' * 72)

    sp = load_panel()
    html = os.path.join(HERE, 'servo_panel.html')
    js_src = extract_press_calc(html)
    print('已取得前端 pressCalc 源码 %d 字符' % len(js_src))

    cfgs = build_matrix()
    print('输入矩阵：%d 组配置 × 24 个 (弦,品) = %d 次比对\n' % (len(cfgs), len(cfgs) * 24))

    fe = run_node(js_src, cfgs)
    if fe is None:
        print('[SKIP] 未找到 node，无法真跑前端 pressCalc —— 跳过对拍。')
        print('       装上 node 后可完整校验（本机：%s）' % NODE_CANDIDATES[0])
        print('\n==== 结果: SKIP（退出码 3，不是通过）====')
        return 3
    be = run_backend(sp, cfgs)

    print('— A. 结构一致性 —')
    # 方向/索引/通道 三项必须完全一致（与基准角无关）
    dir_ok = pi_ok = ch_ok = True
    for ci, c in enumerate(cfgs):
        for s in range(6):
            for k, f in enumerate([1, 2, 3, 4]):
                jr = fe[ci][s][k]
                s0, i0, s1, i1 = sp.FRET_OFFSETS.get(f, (0, None, 0, None))
                if s >= 3 and f in sp.SWAP:
                    s0, i0, s1, i1 = sp.SWAP[f]
                exp_dir, exp_pi = (s0, i0) if f <= 2 else (s1, i1)
                exp_ch = 2 * s if f <= 2 else 2 * s + 1
                if jr['dir'] != exp_dir:
                    dir_ok = False
                if jr['pi'] != exp_pi:
                    pi_ok = False
                if jr['ch'] != exp_ch:
                    ch_ok = False
    check('方向 dir 前后端一致（含弦4-6 SWAP）', dir_ok)
    check('压深索引 pi 前后端一致', pi_ok)
    check('舵机通道 ch 前后端一致', ch_ok)

    print('\n— B. 基准角口径一致性 —')
    # 硬件口径：基准角 = PRESS_ZERO(90) + 零点偏移，与装机角无关。
    # 依据：servo_controller.py:_compute_chord_angles_only() zero=90；
    #       servo_controller.py:890 标定式 new_null_offset = real_zero_angle - 90.0。
    PZ = 90.0
    base_ok = True
    bad = []
    for ci, c in enumerate(cfgs):
        for s in range(6):
            for k, f in enumerate([1, 2, 3, 4]):
                jr = fe[ci][s][k]
                exp_base = PZ + c['null'][jr['ch']]
                if abs(jr['base'] - exp_base) > 1e-9:
                    base_ok = False
                    bad.append((ci, s, f, jr['base'], exp_base))
    check('前端基准角 = PRESS_ZERO(90) + 零点偏移（与装机角无关）', base_ok, bad[:3])

    print('\n— C. 最终角度对拍（后端驱动角 vs 前端预测角）—')
    mismatch = []
    for ci, c in enumerate(cfgs):
        for s in range(6):
            for k in range(4):
                a_be = be[ci][s][k]
                a_fe = round(fe[ci][s][k]['ang'], 1)
                if abs(a_be - a_fe) > 1e-6:
                    mismatch.append((c['home'], s, k + 1, a_be, a_fe, round(a_fe - a_be, 1)))
    total = len(cfgs) * 24
    check('%d/%d 个 (弦,品) 角度完全一致' % (total - len(mismatch), total),
          not mismatch, '不一致样例(装机角,弦,品,后端角,前端角,差) = %s' % mismatch[:5])

    if mismatch:
        byhome = {}
        for h, s, f, ab, af, d in mismatch:
            byhome.setdefault(h, []).append(d)
        print('\n  按装机角归因（差值 = 装机角 - 90）：')
        for h in sorted(byhome):
            ds = set(byhome[h])
            print('    装机角 %-5s  不一致 %3d 处  差值 %s  (装机角-90 = %+.1f)'
                  % (h, len(byhome[h]), sorted(ds), h - 90))

    print('\n— D. 归位角对拍（后端 base_angles vs 前端 零点角）—')
    bad_base = []
    for ci, c in enumerate(cfgs):
        cfg = {
            'null_offset': c['null'],
            'press_offsets': c['press'],
            'home_chord_angle': c['home'],
            'chord_map': ['PWM%d' % i for i in range(12)],
            'strum_map': ['PWM%d' % (12 + i) for i in range(6)],
        }
        for s in range(6):
            b_be = sp.base_angles(cfg, s)
            b_fe = (round(fe[ci][s][0]['base'], 1), round(fe[ci][s][2]['base'], 1))
            if abs(b_be[0] - b_fe[0]) > 1e-6 or abs(b_be[1] - b_fe[1]) > 1e-6:
                bad_base.append((c['home'], s, b_be, b_fe))
    n_rest = len(cfgs) * 12
    check('%d/%d 个归位角一致' % (n_rest - len(bad_base), n_rest),
          not bad_base, '样例(装机角,弦,后端,前端) = %s' % bad_base[:3])

    print('\n— E. 向后兼容：装机角=90 时新实现 vs 旧实现（硬编码 90 + int 取整）—')
    max_d = 0.0
    n_cmp = 0
    for ci, c in enumerate(cfgs):
        if abs(c['home'] - 90.0) > 1e-9:
            continue
        cfg = {
            'null_offset': c['null'],
            'press_offsets': c['press'],
            'home_chord_angle': c['home'],
            'chord_map': ['PWM%d' % i for i in range(12)],
            'strum_map': ['PWM%d' % (12 + i) for i in range(6)],
        }
        for s in range(6):
            for f in range(1, 5):
                s0, i0, s1, i1 = sp.FRET_OFFSETS.get(f, (0, None, 0, None))
                if s >= 3 and f in sp.SWAP:
                    s0, i0, s1, i1 = sp.SWAP[f]
                a0_old = 90 + c['null'][2 * s] + (s0 * c['press'][s][i0] if i0 is not None else 0)
                a1_old = 90 + c['null'][2 * s + 1] + (s1 * c['press'][s][i1] if i1 is not None else 0)
                old = max(0, min(180, int(round(a1_old if f >= 3 else a0_old))))
                new = be[ci][s][f - 1]
                max_d = max(max_d, abs(old - new))
                n_cmp += 1
    check('装机角=90 时与旧实现最大偏差 %.1f°（≤0.5°，仅来自取整精度统一）' % max_d, max_d <= 0.5,
          'max_d=%s' % max_d)

    print('\n— F. 静态守卫：按压/零点路径不得再回退到「装机角」基准 —')
    html_src = open(html, encoding='utf-8').read()
    residual = [p for p in ('homeAngle+dataNull', 'homeAngle + dataNull',
                            'baseAng-homeAngle', 'baseAng - homeAngle')
                if p in html_src]
    check('servo_panel.html 无 homeAngle±dataNull 残留（否则零点会偏 装机角-90）',
          not residual, residual)
    for pat in ('const PRESS_ZERO = 90', 'PRESS_ZERO+dataNull', 'baseAng-PRESS_ZERO'):
        check('servo_panel.html 含 %r' % pat, pat in html_src)

    # 装机角预览/全部归位必须与主程序 esp32_home_servos() 同口径：平角、不加 null_offset
    py_src = open(os.path.join(HERE, 'servo_panel.py'), encoding='utf-8').read()
    hp = re.search(r'def api_home_preview\(\):(.*?)@app\.route', py_src, re.S)
    hp_src = hp.group(1) if hp else ''
    check('/api/home_preview 不再叠加 null_offset（与一键归位同口径）',
          "c['null_offset']" not in hp_src, hp_src[:200])

    print('\n==== 结果: %d 通过 / %d 失败 ====' % (len(OK), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
