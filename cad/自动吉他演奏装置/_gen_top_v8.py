# -*- coding: utf-8 -*-
"""顶盖 v8（1.54 寸 240x240 屏终版）—— 在 v7.3 基础上只改屏幕窗：

  背景：实物屏已定案（2026-10-12 用户照片，PCB 丝印
        "IPS_1.54' LCD / DRIVER IC: ST7789 / INTERFACE: SPI / RESOLUTION: 240*240"）。
        v7.3 的窗 35.9x30.5 是按 1.83 寸(240x284, 显示区 29.52x34.93)开的，
        比实物屏大 8x3mm，直接装会露缝漏光 → 重开窗。

  v8 改动（相对 v7.3，仅两处）：
  ① 屏幕窗：35.9(X) x 30.5(Z) → **28.7(X) x 28.7(Z)** @ (47.5, 29.5)
     —— 1.54 寸 240x240 显示区 27.72 x 27.72mm + 单边 0.5mm 间隙（沿用 v6 惯例）。
        显示区正方形，横装竖装等价；窗心沿用 v7.3 已验证位 (47.5, 29.5)。
        新窗右缘 61.85（v7.3 为 65.45），距 d3 销孔(67.8,33.8) 4.4mm，更安全；
        上缘 43.85（v7.3 为 44.75），距 d7 螺钉孔更远。
  ② 填附属小槽：旧 1.3 寸 OLED 附属槽 3.40 x 14.00 @ (63.1, 30.4)（STL 实测）。
     v7.3 大窗右缘 65.45 能完整盖住它；v8 窗右缘只到 61.85，
     槽的 X[61.85, 64.80] 段会露出 → 整槽填实（反正全在新窗外）。

  沿用 v7.3 其余全部：填葫芦大圆 d23.5@(22,23.5)、填残留小圆 d10.2@(22,41.2)、
  蜂窝喇叭孔 4 列 x 10 行 = 40 孔（左侧红框区）、边缘缺口/定位孔/螺钉孔/内腔不动。

经验教训（v6/v7.3 踩过的坑，务必保持）：
  - 并集塞子 Y 必须严格取面板 bounds，否则上下凸 1mm
  - 差集切体 Y 一律 [-1, 7.5] 过盈
  - 用 trimesh.boolean 列表式一次差集，不要 Manifold.batch_boolean

⚠️ 打印前校核：显示区 27.72mm 是 1.54 寸 240x240 的行业通用值
   （点距 0.1155mm x 240）。拿卡尺量一下实物显示区边长，
   若与 27.72 差 >0.5mm，改下面的 DISPLAY_MM 重跑即可。
"""
import os
import time
import numpy as np
import trimesh

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "泰山派顶盖.STL")
OUT = os.path.join(HERE, "泰山派顶盖_v8_1.54彩屏.STL")

Y_LO, Y_HI = -1.0, 7.5          # 差集切体用过盈范围
TH = Y_HI - Y_LO
YMID = (Y_LO + Y_HI) / 2.0

# ── 1.54 寸屏参数（改这里即可适配实测值）──
DISPLAY_MM = 27.72              # 显示区边长（正方形）
GAP = 0.5                       # 单边间隙（沿用 v6 惯例）
WIN_W = WIN_H = round(DISPLAY_MM + 2 * GAP, 2)   # 28.72 → 28.7
WIN_CX, WIN_CZ = 47.5, 29.5     # 窗心沿用 v7.3 已验证位

GOURD = (22.0, 23.5, 11.9)      # 填葫芦大圆 d23.5
SMALL = (22.0, 41.2, 5.5)       # 填残留小圆 d10.2（半径 5.5 略过盈，v7.3 同款）
# 填附属小槽（STL 实测 3.40 x 14.00 @ (63.1, 30.4)，外扩 0.4 过盈防缝隙）
SLOT = (63.1, 30.4, 3.4 + 0.8, 14.0 + 0.8)
# 蜂窝喇叭孔：沿用 v7.3 左侧布局，4 列 x 10 行 = 40 孔
HC_XS = [17.5, 20.5, 23.5, 26.5]
HC_Z0, HC_NZ, HC_DZ = 14.5, 10, 2.9


def z2y_cyl(cx, cz, r, y0, y1, sections=64):
    """XZ 平面圆柱，Y 轴从 y0 到 y1。"""
    c = trimesh.creation.cylinder(radius=r, height=y1 - y0, sections=sections)
    R = np.array([[1, 0, 0, 0], [0, 0, -1, 0], [0, 1, 0, 0], [0, 0, 0, 1]],
                 dtype=np.float64)
    c.apply_transform(R)
    c.apply_translation([cx, (y0 + y1) / 2.0, cz])
    return c


def thru_rect(cx, cz, w, h):
    b = trimesh.creation.box(extents=[w, TH, h])
    b.apply_translation([cx, YMID, cz])
    return b


t0 = time.time()
m = trimesh.load(SRC).process(validate=True)
print("原顶盖: %d 面, watertight=%s" % (len(m.faces), m.is_watertight))

PY0, PY1 = float(m.bounds[0][1]), float(m.bounds[1][1])
print("面板 Y [%.2f, %.2f]" % (PY0, PY1))


def y_box(cx, cz, w, h, y0, y1):
    """XZ 平面矩形块，Y 从 y0 到 y1（塞子专用，严格取面板范围）"""
    b = trimesh.creation.box(extents=[w, y1 - y0, h])
    b.apply_translation([cx, (y0 + y1) / 2.0, cz])
    return b


# ── 并集填实（塞子 Y 严格取面板范围）──
plugs = [
    z2y_cyl(GOURD[0], GOURD[1], GOURD[2], PY0, PY1),
    z2y_cyl(SMALL[0], SMALL[1], SMALL[2], PY0, PY1),
    y_box(SLOT[0], SLOT[1], SLOT[2], SLOT[3], PY0, PY1),   # v8 新增：填附属小槽
]
b = trimesh.boolean.union([m] + plugs, engine="manifold")
print("填葫芦+小圆+附属槽后: watertight=%s vol=%.0f Y[%.2f,%.2f]" % (
    b.is_watertight, b.volume, b.bounds[0][1], b.bounds[1][1]))

# ── 一次差集：屏幕窗 + 蜂窝 40 孔（切体 Y 过盈 [-1,7.5]）──
cutters = [thru_rect(WIN_CX, WIN_CZ, WIN_W, WIN_H)]
n = 0
for k in range(HC_NZ):
    zc = HC_Z0 + HC_DZ * k
    for xc in HC_XS:
        cutters.append(z2y_cyl(xc, zc, 1.15, Y_LO, Y_HI, sections=32))
        n += 1
print("切体: 窗 1 (%.1fx%.1f @%.1f,%.1f) + 蜂窝 %d" % (
    WIN_W, WIN_H, WIN_CX, WIN_CZ, n))

res = trimesh.boolean.difference([b] + cutters, engine="manifold")
res.merge_vertices()
res.update_faces(res.nondegenerate_faces())
res.update_faces(res.unique_faces())
trimesh.repair.fill_holes(res)
trimesh.repair.fix_normals(res)
res = res.process(validate=True)
_pairs = trimesh.grouping.group_rows(res.edges_sorted, require_count=2)
_open_n = len(res.edges) - 2 * len(_pairs)
print("v8 结果: %d 面, watertight=%s, vol=%.0f, 开放边=%d, 用时 %.1fs" % (
    len(res.faces), res.is_watertight, res.volume,
    _open_n, time.time() - t0))
res.export(OUT)

chk = trimesh.load(OUT)
print("回读复检: %d 面, watertight=%s, vol=%.0f" % (
    len(chk.faces), chk.is_watertight, chk.volume))
print("已写出", OUT)
