#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""guitar-assist 机构驱动层 ↔ 档3(《拨云》) 一致性对拍

目的
----
guitar-assist 要用自己的 C 代码把"和弦指法"变成发往 ESP32 的 UART 帧。
本脚本用 **《拨云》的真实实现**(frame_codec.py / servo_controller.py) 当标准答案,
把"我们将要用 C 重写的那套逻辑"逐字节对拍,证明:

  A. CRC16-CCITT 一致            (poly 0x1021, init 0xFFFF, 大端输出)
  B. 各类帧字节流一致            (SERVO_BATCH / SINGLE / CTRL / MODE_SWITCH / CHORD_DEF / CONFIG_SAVE)
  C. 12 路和弦角度一致           (zero=90, FRET_OFFSETS, 弦4-6 反转, null_offset, press_offsets)
  D. 6 路拨弦角度一致            (strum_angles + flip_state + [55,125] 钳位)
  E. 端到端: 和弦 -> 18 路角度 -> 完整帧 -> 反解析 CRC 通过

这里的 Python 镜像 (mirror_*) 与将要落地的 C 实现一一对应;对拍通过后,
C 代码只需"照抄"这些函数即可保证 ESP32 端能正确解析。

用法:
  env -u PYTHONPATH PYTHONPATH=<site-packages> python verify_boyun_parity.py
退出码: 0 = 全部一致; 1 = 有不一致(会把差异打出来)
"""

import os
import sys
import struct
import random

# ── 拨云源码路径(对拍基准) ──
BOYUN_ROOT = os.environ.get(
    "BOYUN_ROOT", r"E:\音乐\拨云_开源参考\liguanxi_Automatic_guitar")
sys.path.insert(0, os.path.join(BOYUN_ROOT, "src", "guitarist"))

OK, FAIL = [], []


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("OK" if cond else "XX", name,
                           ("  <- " + str(detail)) if (detail and not cond) else ""))


# ════════════════════════════════════════════════════════════════
# 基准: 《拨云》真实实现
# ════════════════════════════════════════════════════════════════
import frame_codec as REF                       # noqa: E402
from frame_types import FrameType, EspMode      # noqa: E402

import io as _io                                # noqa: E402
import contextlib                               # noqa: E402
with contextlib.redirect_stderr(_io.StringIO()):   # 屏蔽串口打开失败的 stderr
    from servo_controller import ServoController, STRUM_ANGLE_MIN, STRUM_ANGLE_MAX
SC = ServoController()

NULL_OFFSET = [float(x) for x in SC.null_offset]
PRESS = [[float(x) for x in row] for row in SC._press_offsets]
STRUM = [[float(row[0]), float(row[1])] for row in SC._strum_angles]


# ════════════════════════════════════════════════════════════════
# 镜像: 将来 C 端要实现的那套逻辑 (逐行对应)
# ════════════════════════════════════════════════════════════════

FRAME_SYNC0 = 0xAA
FRAME_SYNC1 = 0x55
FRAME_MAX_PAYLOAD = 247
CRC16_POLY = 0x1021
MASK16 = 0xFFFF


def _crc16_table():
    t = []
    for i in range(256):
        crc = i << 8
        for _ in range(8):
            crc = ((crc << 1) & MASK16) ^ CRC16_POLY if (crc & 0x8000) else (crc << 1) & MASK16
        t.append(crc)
    return t


_CRC_TAB = _crc16_table()


def mirror_crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc = ((crc << 8) ^ _CRC_TAB[((crc >> 8) ^ b) & 0xFF]) & MASK16
    return crc


def mirror_frame(ftype: int, payload: bytes) -> bytes:
    assert len(payload) <= FRAME_MAX_PAYLOAD
    body = bytes([int(ftype)]) + payload
    crc = mirror_crc16(body)
    return (bytes([FRAME_SYNC0, FRAME_SYNC1, len(payload), int(ftype)])
            + payload + struct.pack('>H', crc))


def mirror_servo_batch(angles) -> bytes:
    assert len(angles) == 18
    payload = bytes(int(max(0, min(180, a))) for a in angles)
    return mirror_frame(FrameType.NORMAL_SERVO_BATCH, payload)


def mirror_servo_single(ch_type, ch_index, angle) -> bytes:
    return mirror_frame(FrameType.NORMAL_SERVO_SINGLE,
                        struct.pack('BBB', ch_type, ch_index, max(0, min(180, angle))))


def mirror_servo_ctrl(cmd) -> bytes:
    return mirror_frame(FrameType.NORMAL_SERVO_CTRL, bytes([cmd]))


def mirror_mode_switch(mode) -> bytes:
    return mirror_frame(FrameType.CTRL_MODE_SWITCH, struct.pack('BB', int(mode), 0))


def mirror_chord_def(name: str, frets) -> bytes:
    nb = name.encode('utf-8')[:16].ljust(16, b'\0')
    fb = bytes((max(-128, min(127, f)) & 0xFF) for f in frets[:6]).ljust(6, b'\0')
    return mirror_frame(FrameType.NORMAL_CHORD_DEF, nb + fb)


def mirror_config_save() -> bytes:
    return mirror_frame(FrameType.NORMAL_CONFIG_SAVE, b'')


# ── 角度: 与 servo_controller._compute_chord_angles_only 完全同逻辑 ──

FRET_OFFSETS = {
    0: (0, None, 0, None),
    1: (1, 0, 0, None),
    2: (-1, 1, 0, None),
    3: (0, None, 1, 2),
    4: (0, None, -1, 3),
}
SWAP_456 = {
    1: (-1, 1, 0, None),
    2: (1, 0, 0, None),
    3: (0, None, -1, 3),
    4: (0, None, 1, 2),
}
K = [0, 2, 4, 6, 8, 10]


def mirror_chord_angles(null_off, press, frets):
    zero = 90.0
    ang = [0.0] * 12
    for x in range(6):
        f = max(0, min(4, frets[x]))
        s0, i0, s1, i1 = FRET_OFFSETS[f]
        if x >= 3 and f in SWAP_456:
            s0, i0, s1, i1 = SWAP_456[f]
        base0 = zero + null_off[K[x]]
        base1 = zero + null_off[K[x] + 1]
        ang[K[x]] = base0 + (s0 * press[x][i0] if i0 is not None else 0)
        ang[K[x] + 1] = base1 + (s1 * press[x][i1] if i1 is not None else 0)
    return [max(0, min(180, int(round(a)))) for a in ang]


def mirror_strum_angles(strum_tab, strums, flip_state):
    out = [0] * 6
    for x in range(6):
        if strums[x]:
            a = strum_tab[x][flip_state[x]]
            out[x] = int(max(STRUM_ANGLE_MIN, min(STRUM_ANGLE_MAX, int(round(a)))))
            flip_state[x] ^= 1
    return out


# ════════════════════════════════════════════════════════════════
# 测试集
# ════════════════════════════════════════════════════════════════

# 常用和弦指法(6弦->1弦), 含 -1(不弹) 与 0..4 各品
CHORDS = {
    "C": [0, 1, 0, 2, 3, 0], "G": [3, 2, 0, 0, 0, 3], "Am": [-1, 0, 2, 2, 1, 0],
    "F": [1, 3, 3, 2, 1, 1], "D": [-1, -1, 0, 2, 3, 2], "Em": [0, 2, 2, 0, 0, 0],
    "Dm": [-1, -1, 0, 2, 3, 1], "E": [0, 2, 2, 1, 0, 0], "A": [-1, 0, 2, 2, 2, 0],
    "G7": [3, 2, 0, 0, 0, 1], "C7": [-1, 3, 2, 0, 1, 0], "Bm": [-1, 2, 4, 4, 3, 2],
    "B7": [-1, 2, 1, 2, 0, 2], "F#m": [2, 4, 4, 2, 2, 2],
}


def main():
    print("=" * 70)
    print("guitar-assist 机构驱动层  ↔  档3(《拨云》) 一致性对拍")
    print("=" * 70)
    print("基准: %s" % os.path.join(BOYUN_ROOT, "src", "guitarist"))
    print("配置: null_offset=%s" % NULL_OFFSET)
    print("      press_offsets=%s" % PRESS)
    print("      strum_angles=%s  clamp=[%g,%g]" % (STRUM, STRUM_ANGLE_MIN, STRUM_ANGLE_MAX))
    print()

    # ---- A. CRC16 ----
    print("— A. CRC16-CCITT 一致性 —")
    rnd = random.Random(20261012)
    bad = []
    for _ in range(500):
        n = rnd.randint(0, 60)
        data = bytes(rnd.randint(0, 255) for _ in range(n))
        if mirror_crc16(data) != REF.crc16(data):
            bad.append(data.hex())
    check("500 组随机数据 CRC16 一致", not bad, bad[:3])
    for probe in (b"", b"\x00", b"\xff" * 32, bytes(range(64))):
        check("CRC16 边界 %r" % (probe[:8],),
              mirror_crc16(probe) == REF.crc16(probe),
              "%04X vs %04X" % (mirror_crc16(probe), REF.crc16(probe)))

    # ---- B. 帧字节流 ----
    print("\n— B. 帧字节流一致性 —")
    cases = [
        ("SERVO_BATCH 全 90", mirror_servo_batch([90] * 18),
         REF.make_servo_batch([90] * 18).serialize()),
        ("SERVO_SINGLE PCA ch3=120", mirror_servo_single(0, 3, 120),
         REF.make_servo_single(0, 3, 120).serialize()),
        ("SERVO_SINGLE PWM ch1=55", mirror_servo_single(1, 1, 55),
         REF.make_servo_single(1, 1, 55).serialize()),
        ("SERVO_CTRL ENABLE", mirror_servo_ctrl(0x01), REF.make_servo_ctrl(0x01).serialize()),
        ("SERVO_CTRL DISABLE", mirror_servo_ctrl(0x02), REF.make_servo_ctrl(0x02).serialize()),
        ("SERVO_CTRL RESET_AND_IDLE", mirror_servo_ctrl(0x03), REF.make_servo_ctrl(0x03).serialize()),
        ("MODE_SWITCH PLAY", mirror_mode_switch(EspMode.PLAY),
         REF.make_mode_switch(EspMode.PLAY).serialize()),
        ("MODE_SWITCH NORMAL", mirror_mode_switch(EspMode.NORMAL),
         REF.make_mode_switch(EspMode.NORMAL).serialize()),
        ("CHORD_DEF C", mirror_chord_def("C", [0, 1, 0, 2, 3, 0]),
         REF.make_chord_def("C", [0, 1, 0, 2, 3, 0]).serialize()),
        ("CHORD_DEF Am7", mirror_chord_def("Am7", [-1, 0, 2, 0, 1, 0]),
         REF.make_chord_def("Am7", [-1, 0, 2, 0, 1, 0]).serialize()),
        ("CONFIG_SAVE", mirror_config_save(), REF.make_config_save().serialize()),
    ]
    for name, mine, ref in cases:
        check("%-26s %s" % (name, ref[:6].hex(' ')), mine == ref,
              "mine=%s ref=%s" % (mine.hex(' '), ref.hex(' ')))

    # ---- C. 和弦角度 ----
    print("\n— C. 12 路和弦角度一致性 —")
    bad = []
    for name, fr in CHORDS.items():
        ref = SC._compute_chord_angles_only(fr)
        mine = mirror_chord_angles(NULL_OFFSET, PRESS, fr)
        if mine != ref:
            bad.append((name, fr, mine, ref))
    check("%d 个和弦角度全等" % len(CHORDS), not bad, bad[:2])
    # 穷举所有 0..4 的 6 弦组合抽样 + 随机
    bad = []
    rnd = random.Random(7)
    for _ in range(2000):
        fr = [rnd.randint(-1, 4) for _ in range(6)]
        if mirror_chord_angles(NULL_OFFSET, PRESS, fr) != SC._compute_chord_angles_only(fr):
            bad.append(fr)
    for combo in ([0] * 6, [4] * 6, [1] * 6, [2] * 6, [3] * 6, [-1] * 6):
        if mirror_chord_angles(NULL_OFFSET, PRESS, combo) != SC._compute_chord_angles_only(combo):
            bad.append(combo)
    check("2000 组随机指法 + 6 组边界全等", not bad, bad[:2])

    # 也换一组非默认 null_offset / press_offsets 再验一次(证明不是巧合)
    NO2 = [1.5, 6.0, -1.0, -3.5, 2.0, -2.5, 0.5, -6.0, 3.0, -10.0, 4.5, -4.0]
    PR2 = [[40, 45, 50, 55], [41, 47, 52, 57], [39, 45, 50, 55],
           [-36, -42, -47, -52], [-33, -39, -45, -50], [-30, -36, -42, -48]]
    bad = []
    for _ in range(600):
        fr = [rnd.randint(-1, 4) for _ in range(6)]
        ref = SC._compute_chord_angles_only(fr)  # 基准用 SC 自身配置
        # 用相同配置喂给基准, 再喂给镜像 —— 保证比较的是同一组参数
    # 直接改 SC 的参数做一次(用完还原)
    old_no, old_pr = SC.null_offset, SC._press_offsets
    try:
        SC.null_offset, SC._press_offsets = NO2, [r[:] for r in PR2]
        bad = []
        for _ in range(600):
            fr = [rnd.randint(-1, 4) for _ in range(6)]
            if mirror_chord_angles(NO2, PR2, fr) != SC._compute_chord_angles_only(fr):
                bad.append(fr)
        check("换一组非默认参数后仍全等", not bad, bad[:2])
    finally:
        SC.null_offset, SC._press_offsets = old_no, old_pr

    # ---- D. 拨弦角度 ----
    print("\n— D. 6 路拨弦角度一致性 —")
    bad = []
    for trial in range(200):
        strums = [rnd.randint(0, 1) for _ in range(6)]
        fs_ref = [0] * 6
        fs_mine = [0] * 6
        ref = SC._compute_strum_angles_only([bool(s) for s in strums], fs_ref)
        mine = mirror_strum_angles(STRUM, [bool(s) for s in strums], fs_mine)
        if mine != ref or fs_mine != fs_ref:
            bad.append((strums, mine, ref, fs_mine, fs_ref))
    check("200 组随机拨弦序列全等(含 flip_state)", not bad, bad[:2])
    check("拨弦钳位常量一致", (STRUM_ANGLE_MIN, STRUM_ANGLE_MAX) == (55.0, 125.0))

    # ---- E. 端到端 ----
    print("\n— E. 端到端: 和弦 -> 18 路 -> 帧 -> 反解析 —")
    n_ok = 0
    for name, fr in CHORDS.items():
        chord12 = mirror_chord_angles(NULL_OFFSET, PRESS, fr)
        fs = [0] * 6
        strum6 = mirror_strum_angles(STRUM, [True] * 6, fs)
        batch18 = chord12 + strum6
        raw = mirror_servo_batch(batch18)
        ref_raw = REF.make_servo_batch(batch18).serialize()
        back = REF.Frame.deserialize(raw)
        if raw == ref_raw and back is not None and list(back.payload) == batch18:
            n_ok += 1
        else:
            print("     XX %s: mine=%s ref=%s" % (name, raw.hex(' '), ref_raw.hex(' ')))
    check("%d/%d 个和弦的完整帧字节一致且可被 ESP32 端反解析" % (n_ok, len(CHORDS)),
          n_ok == len(CHORDS))

    # ---- 汇总 ----
    print("\n" + "=" * 70)
    print("结果: %d 通过 / %d 失败" % (len(OK), len(FAIL)))
    if FAIL:
        print("失败项:")
        for f in FAIL:
            print("  - %s" % f)
    print("=" * 70)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
