# -*- coding: utf-8 -*-
"""顶盖 v7.3（横装屏幕 + 蜂窝移左侧）—— 在 v6 基础上按用户指正：
  ① 删残留小圆孔：原顶盖大喇叭圆(葫芦 d23.5 @X22,Z23.5)正上方还有一个
     d10.2 圆孔 (@X22, Z41.2) → 圆柱塞填实
  ② 屏幕横装（长边沿外壳长边 X 向）：窗 35.9(X) x 30.5(Z) @ (47.5,29.5)
     —— v7.1 已验证位：右缘 65.45 距 d3 销孔 0.8mm，上缘 44.75 距 d7 螺钉孔 0.5mm，
        且完整盖住旧屏开口(右缘 64.75)与附属小槽，无需再填实
  ③ 蜂窝喇叭孔不在屏幕下方！整体搬到左侧红框区（原葫芦位置）：
     4 列 x 10 行 = 40 孔，列心 X 17.5/20.5/23.5/26.5（中心 22.0），
     行心 Z 14.5~40.6（间距 2.9），右缘距窗口左壁 1.9mm

其余与 v6 相同：填葫芦大圆、边缘缺口/定位孔/螺钉孔/内部空腔全部保留。
经验教训（v6 踩过的坑，务必保持）：
  - 并集塞子 Y 必须严格取面板 bounds，否则上下凸 1mm
  - 差集切体 Y 一律 [-1, 7.5] 过盈（v6 的矩形窗贴面切是隐患）
  - 用 trimesh.boolean 列表式一次差集，不要 Manifold.batch_boolean
"""
import os
import time
import numpy as np
import trimesh

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "泰山派顶盖.STL")
OUT = os.path.join(HERE, "泰山派顶盖_v7.3_1.83彩屏.STL")

Y_LO, Y_HI = -1.0, 7.5          # 差集切体用过盈范围
TH = Y_HI - Y_LO
YMID = (Y_LO + Y_HI) / 2.0

# v7.3：屏幕横装（landscape，长边沿外壳长边 X 向）
WIN_CX, WIN_CZ, WIN_W, WIN_H = 47.5, 29.5, 35.9, 30.5
GOURD = (22.0, 23.5, 11.9)       # 填葫芦大圆
SMALL = (22.0, 41.2, 5.5)        # 填残留小圆 d10.2
# 蜂窝喇叭孔：搬到左侧红框区（原葫芦位置），4 列 x 10 行 = 40 孔
HC_XS = [17.5, 20.5, 23.5, 26.5]           # 列心 X（中心 22.0）
HC_Z0, HC_NZ, HC_DZ = 14.5, 10, 2.9        # 行心 Z：14.5 ~ 40.6


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

# ── 并集填实（塞子 Y 严格取面板范围）──
def y_box(cx, cz, w, h, y0, y1):
    """XZ 平面矩形块，Y 从 y0 到 y1"""
    b = trimesh.creation.box(extents=[w, y1 - y0, h])
    b.apply_translation([cx, (y0 + y1) / 2.0, cz])
    return b


plugs = [
    z2y_cyl(GOURD[0], GOURD[1], GOURD[2], PY0, PY1),
    z2y_cyl(SMALL[0], SMALL[1], SMALL[2], PY0, PY1),
    # v7.3 横装窗口右缘 65.45 > 旧屏开口右缘 64.75，完整盖住 → 无需填实条
]
b = trimesh.boolean.union([m] + plugs, engine="manifold")
print("填葫芦+小圆后: watertight=%s vol=%.0f Y[%.2f,%.2f]" % (
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
print("v7 结果: %d 面, watertight=%s, vol=%.0f, 开放边=%d, 用时 %.1fs" % (
    len(res.faces), res.is_watertight, res.volume,
    _open_n, time.time() - t0))
res.export(OUT)

chk = trimesh.load(OUT)
print("回读复检: %d 面, watertight=%s, vol=%.0f" % (
    len(chk.faces), chk.is_watertight, chk.volume))
print("已写出", OUT)
