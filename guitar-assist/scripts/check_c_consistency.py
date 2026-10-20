#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C 端 mech 模块静态核对

本机没有 C 编译器, 用两条"能做的"验证兜底:

  1) 常量表核对: 把 chord_angles.c 里的 FRET_OFF / SWAP456 / K / 默认参数
     逐个抽出来, 跟《拨云》真实值比对 —— 这是最危险的一类 bug(表抄错)。
  2) 语法解析: 用 pycparser 解析 mech 模块(宏先做文本展开), 抓语法错误。

用法:
  env -u PYTHONPATH PYTHONPATH=<site-packages> python check_c_consistency.py
退出码 0 = 全过。
"""

import os
import re
import sys
import io
import contextlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BOYUN_ROOT = os.environ.get(
    "BOYUN_ROOT", r"E:\音乐\拨云_开源参考\liguanxi_Automatic_guitar")
sys.path.insert(0, os.path.join(BOYUN_ROOT, "src", "guitarist"))

OK, FAIL = [], []


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("OK" if cond else "XX", name,
                           ("  <- " + str(detail)) if (detail and not cond) else ""))


with contextlib.redirect_stderr(io.StringIO()):
    from servo_controller import ServoController
SC = ServoController()

REF_FRET = {0: (0, None, 0, None), 1: (1, 0, 0, None), 2: (-1, 1, 0, None),
            3: (0, None, 1, 2), 4: (0, None, -1, 3)}
REF_SWAP = {1: (-1, 1, 0, None), 2: (1, 0, 0, None),
            3: (0, None, -1, 3), 4: (0, None, 1, 2)}
REF_K = [0, 2, 4, 6, 8, 10]


def strip_comments(t):
    t = re.sub(r'/\*.*?\*/', '', t, flags=re.S)
    return t


def grab_body(src, name):
    """取 `name ... = { ... };` 里最外层花括号内的内容(花括号配对扫描)。"""
    i = src.find(name)
    if i < 0:
        return None
    j = src.find('{', i)
    if j < 0:
        return None
    depth = 0
    for k in range(j, len(src)):
        if src[k] == '{':
            depth += 1
        elif src[k] == '}':
            depth -= 1
            if depth == 0:
                return src[j + 1:k]
    return None


def to_rows(body):
    """把初始化体转成行: 有内层 {} 则每个内层一组, 否则整段一组。"""
    if body is None:
        return None
    if '{' in body:
        rows = []
        for grp in re.findall(r'\{([^{}]*)\}', body):
            rows.append([float(x) for x in re.findall(r'-?\d+\.?\d*', grp)])
        return rows
    return [[float(x) for x in re.findall(r'-?\d+\.?\d*', body)]]


def main():
    print("=" * 68)
    print("guitar-assist C 端 mech 模块 静态核对")
    print("=" * 68)

    ca = strip_comments(open(os.path.join(ROOT, "src/mech/chord_angles.c"),
                             encoding="utf-8").read())

    # ---- 1. FRET_OFF ----
    print("\n— 1. FRET_OFF 表 —")
    rows = to_rows(grab_body(ca, "FRET_OFF"))
    got = [[int(r[0]), int(r[1]), int(r[2]), int(r[3])] for r in (rows or [])]
    want = [[(-1 if x is None else x) for x in REF_FRET[f]] for f in range(5)]
    check("FRET_OFF 5 行与拨云 _FRET_OFFSETS 一致", got == want,
          "got=%s want=%s" % (got, want))

    # ---- 2. SWAP456 ----
    print("\n— 2. SWAP456 表(弦4-6 机械反转) —")
    rows = to_rows(grab_body(ca, "SWAP456"))
    got = [[int(r[0]), int(r[1]), int(r[2]), int(r[3])] for r in (rows or [])]
    want = [[0, 0, 0, 0]] + [[(-1 if x is None else x) for x in REF_SWAP[f]]
                             for f in range(1, 5)]
    check("SWAP456 0..4 行与拨云一致(0 行为占位)", got == want,
          "got=%s want=%s" % (got, want))

    # ---- 3. K ----
    print("\n— 3. K 索引表 —")
    b = grab_body(ca, "K[GA_MECH_STRINGS]") or grab_body(ca, "K[6]")
    got = [int(float(x)) for x in re.findall(r'-?\d+', b)] if b else None
    check("K == [0,2,4,6,8,10]", got == REF_K, got)

    # ---- 4. 出厂默认参数 ----
    print("\n— 4. 出厂默认参数 —")
    b = grab_body(ca, "d_null")
    got = [int(float(x)) for x in re.findall(r'-?\d+', b)] if b else None
    check("默认 null_offset 12 项一致", got == [int(x) for x in SC.null_offset], got)

    rows = to_rows(grab_body(ca, "d_press"))
    got = [[float(x) for x in r] for r in (rows or [])]
    want = [[float(x) for x in row] for row in SC._press_offsets]
    check("默认 press_offsets 6x4 一致", got == want, "got=%s want=%s" % (got, want))

    rows = to_rows(grab_body(ca, "d_strum"))
    got = [[float(x) for x in r] for r in (rows or [])]
    want = [[float(r[0]), float(r[1])] for r in SC._strum_angles]
    check("默认 strum_angles 6x2 一致", got == want, "got=%s want=%s" % (got, want))

    # ---- 5. 关键常量 ----
    print("\n— 5. 关键常量 —")
    h = strip_comments(open(os.path.join(ROOT, "src/mech/chord_angles.h"),
                            encoding="utf-8").read())
    fc = strip_comments(open(os.path.join(ROOT, "src/mech/frame_codec.h"),
                             encoding="utf-8").read())
    m = re.search(r'#define\s+GA_PRESS_ZERO\s+([\d.]+)', h)
    check("GA_PRESS_ZERO == 90", bool(m) and float(m.group(1)) == 90.0,
          m.group(1) if m else None)
    check("STRUM 限位 == [55,125]",
          re.search(r'GA_STRUM_ANGLE_MIN\s+55', h) and re.search(r'GA_STRUM_ANGLE_MAX\s+125', h))
    check("SYNC == AA 55", re.search(r'GA_FRAME_SYNC0\s+0xAA', fc)
          and re.search(r'GA_FRAME_SYNC1\s+0x55', fc))
    check("CRC poly == 0x1021", re.search(r'GA_CRC16_POLY\s+0x1021', fc))

    # ---- 6. pycparser 语法解析 ----
    print("\n— 6. C 语法解析 (pycparser) —")
    try:
        from pycparser import c_parser
    except ImportError:
        check("pycparser 可用", False, "未安装, 跳过语法检查")
        return 1 if FAIL else 0

    # 宏做文本展开(parser 不跑真正的预处理)
    MACROS = {
        "GA_FRAME_MAX_TOTAL": "253", "GA_FRAME_MAX_PAYLOAD": "247",
        "GA_FRAME_SYNC0": "0xAA", "GA_FRAME_SYNC1": "0x55",
        "GA_CRC16_POLY": "0x1021",
        "GA_MECH_CHORD_CH": "12", "GA_MECH_STRUM_CH": "6",
        "GA_MECH_STRINGS": "6", "GA_MECH_TOTAL_CH": "18",
        "GA_STRUM_ANGLE_MIN": "55.0f", "GA_STRUM_ANGLE_MAX": "125.0f",
        "GA_PRESS_ZERO": "90.0f",
        "GA_MECH_DEFAULT_DEV": '"/dev/ttyS3"', "GA_MECH_DEFAULT_BAUD": "921600",
        # 注意: 不要把枚举名(GA_SERVO_CH_*/GA_SERVO_CTRL_*/GA_ESP_MODE_*)
        # 放进来 —— 它们在代码里是定义处, 展开会把 "NAME = 0" 变成 "0 = 0"。
        "UI_APP_LEFT_KEYS": "6", "UI_QUICK_CHORDS": "6",
        "UI_W": "240", "UI_H": "240",
    }
    PRE = (
        "typedef unsigned char uint8_t;\n"
        "typedef unsigned short uint16_t;\n"
        "typedef unsigned int uint32_t;\n"
        "typedef unsigned long size_t;\n"
        "typedef long ssize_t;\n"
        "typedef struct _FILE FILE;\n"
        "typedef int pid_t;\n"
    )

    def sanitize(text):
        text = strip_comments(text)
        # 整体移除 C++ 链接守卫(开/闭各一处) —— 否则去掉指令后会留下裸花括号
        text = re.sub(
            r'#\s*ifdef\s+__cplusplus\s*\n\s*extern\s+"C"\s*\{\s*\n\s*#\s*endif', '', text)
        text = re.sub(
            r'#\s*ifdef\s+__cplusplus\s*\n\s*\}\s*\n\s*#\s*endif', '', text)
        # 删除预处理指令; 用状态机吃掉反斜杠续行(否则续行的 '\' 会变非法字符)
        out = []
        in_cont = False
        for ln in text.split('\n'):
            if in_cont:
                in_cont = ln.rstrip().endswith('\\')
                continue
            if ln.lstrip().startswith('#'):
                in_cont = ln.rstrip().endswith('\\')
                continue
            out.append(ln.replace('extern "C"', ''))
        text = '\n'.join(out)
        for k, v in MACROS.items():
            text = re.sub(r'\b%s\b' % re.escape(k), v, text)
        text = re.sub(r'\bNULL\b', '((void*)0)', text)
        return text

    BASE = ["src/mech/frame_codec.h", "src/mech/chord_angles.h", "src/mech/mech_driver.h"]
    UI = ["src/ui/ui_draw.h", "src/ui/ui_screen.h", "src/ui/input.h",
          "src/ui/ui_app.h", "src/mech/mech_bridge.h"]

    units = {
        "frame_codec":   BASE + ["src/mech/frame_codec.c"],
        "chord_angles":  BASE + ["src/mech/chord_angles.c"],
        "mech_driver":   BASE + ["src/mech/mech_driver.c"],
        "mech_bridge":   BASE + UI + ["src/mech/mech_bridge.c"],
        "ui_demo_wire":  BASE + UI + ["tools/ui_demo.c"],
        "mech_selftest": BASE + ["tools/mech_selftest.c"],
    }
    parser = c_parser.CParser()
    for name, files in units.items():
        text = PRE
        for f in files:
            text += "\n" + sanitize(open(os.path.join(ROOT, f), encoding="utf-8").read())
        try:
            parser.parse(text, filename=name)
            check("%s 语法解析通过" % name, True)
        except Exception as e:
            msg = " | ".join(str(e).split('\n')[0:2])
            check("%s 语法解析通过" % name, False, msg)

    print("\n" + "=" * 68)
    print("结果: %d 通过 / %d 失败" % (len(OK), len(FAIL)))
    for f in FAIL:
        print("  - %s" % f)
    print("=" * 68)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
