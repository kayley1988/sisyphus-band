# -*- coding: utf-8 -*-
"""v6 顶盖（1.83寸彩屏终版）——只动三处，其余一律保留
  ① 屏幕窗：匹配 1.83寸 ST7789B 显示区 29.52×34.93mm（横装）+0.5mm 间隙
     → 35.9 (X) × 30.5 (Z) 直角矩形，中心沿用 (50.5, 30.75)
     （直角而非圆角：圆角弧线会与内部腔体角点 (68, 45.25) 相切产生非流形边）
  ② 删葫芦：面板中部两个上下半圆（X[11,33] Z[12,35]，合成 r≈11.4 大圆）
     → 布尔并一个贯穿圆柱塞子（圆心 X22 Z23.5 r11.9）填实
  ③ 蜂窝喇叭孔：屏幕窗下方 Z 2~14 实心带，4 排错位 40 孔（r1.15，贯穿）
其余（左侧小圆、三个按键孔、边缘缺口、定位柱、卡扣、内部空腔）全部不动。

坐标：X 74.5 宽 / Y 6.5 厚 / Z 55.5 深；所有贯穿体 Y 取 [-1, 7.5]
"""
import os
import time
import numpy as np
import trimesh
from shapely.geometry import Polygon

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "泰山派顶盖.STL")
OUT = os.path.join(HERE, "泰山派顶盖_v6_1.83彩屏.STL")

Y_LO, Y_HI = -1.0, 7.5
TH = Y_HI - Y_LO
YMID = (Y_LO + Y_HI) / 2.0


def thru_cyl(cx, cz, r, sections=64):
    """XZ 平面圆柱，Y 贯穿。trimesh 圆柱默认轴沿 Z，绕 X 转 90° 落到 Y。"""
    c = trimesh.creation.cylinder(radius=r, height=TH, sections=sections)
    R = np.array([[1, 0, 0, 0], [0, 0, -1, 0], [0, 1, 0, 0], [0, 0, 0, 1]],
                 dtype=np.float64)
    c.apply_transform(R)
    c.apply_translation([cx, YMID, cz])
    return c


def thru_rounded_rect(cx, cz, w, h, r, seg=16):
    """XZ 平面圆角矩形，Y 贯穿。先在原点建形，再平移到 (cx, cz)。"""
    pts = []
    c4 = [(w/2 - r, -h/2 + r), (-w/2 + r, -h/2 + r),
          (-w/2 + r, h/2 - r), (w/2 - r, h/2 - r)]
    a0 = [(-np.pi/2, 0), (np.pi, np.pi*1.5), (np.pi/2, np.pi), (0, np.pi/2)]
    for (ccx, ccz), (a1, a2) in zip(c4, a0):
        for t in np.linspace(a1, a2, seg):
            pts.append((ccx + r*np.cos(t), ccz + r*np.sin(t)))
    m2 = trimesh.creation.extrude_polygon(Polygon(pts), height=TH)
    # extrude 沿 Z（z∈[0,TH]）→ 换轴使 Y 为拉伸方向；再把 2D (x,y) 映射成 (X,Z)
    m2.apply_transform(np.array([[1, 0, 0, 0],
                                 [0, 0, 1, 0],
                                 [0, 1, 0, 0],
                                 [0, 0, 0, 1]], dtype=np.float64))
    m2.apply_translation([cx, Y_LO, cz])   # Y∈[0,TH] → [Y_LO,Y_HI]
    if not m2.is_volume:
        m2.invert()
    return m2


def thru_rect(cx, cz, w, h):
    """XZ 平面直角矩形，Y 贯穿（面板 Y 范围内）"""
    b = trimesh.creation.box(extents=[w, PY1 - PY0, h])
    b.apply_translation([cx, (PY0 + PY1) / 2.0, cz])
    return b


t0 = time.time()
m = trimesh.load(SRC).process(validate=True)
print("原顶盖: %d 面, watertight=%s, vol=%.0f, Y范围[%.2f, %.2f]" % (
    len(m.faces), m.is_watertight, m.volume, m.bounds[0][1], m.bounds[1][1]))

# ② 填葫芦（塞子严格取面板 Y 范围，避免并集后上下凸出）
PY0, PY1 = float(m.bounds[0][1]), float(m.bounds[1][1])
plug = trimesh.creation.cylinder(radius=11.9, height=PY1 - PY0, sections=64)
plug.apply_transform(np.array([[1, 0, 0, 0], [0, 0, -1, 0],
                               [0, 1, 0, 0], [0, 0, 0, 1]], dtype=np.float64))
plug.apply_translation([22.0, (PY0 + PY1) / 2.0, 23.5])
b = trimesh.boolean.union([m, plug], engine="manifold")
print("填葫芦后: vol=%.0f watertight=%s Y范围[%.2f, %.2f]" % (
    b.volume, b.is_watertight, b.bounds[0][1], b.bounds[1][1]))

# ① + ③ 一次差集
cutters = [thru_rect(50.5, 30.75, 35.9, 30.5)]
n = 0
for row, zc in enumerate([4.0, 6.9, 9.8, 12.7]):
    x = 36.5 + (1.5 if row % 2 else 0.0)
    while x <= 65.5:
        cutters.append(thru_cyl(x, zc, 1.15, sections=32))
        n += 1
        x += 3.0
print("切体: 屏幕窗 1 + 蜂窝孔 %d" % n)

res = trimesh.boolean.difference([b] + cutters, engine="manifold")
# 加固修复：焊接顶点 → 补洞 → 统一法线，保证导出后仍水密
res.merge_vertices()
res.update_faces(res.nondegenerate_faces())
res.update_faces(res.unique_faces())
trimesh.repair.fill_holes(res)
trimesh.repair.fix_normals(res)
res = res.process(validate=True)
print("v6 结果: %d 面, watertight=%s, vol=%.0f, Y范围[%.2f, %.2f], 用时 %.1fs" % (
    len(res.faces), res.is_watertight, res.volume,
    res.bounds[0][1], res.bounds[1][1], time.time() - t0))
res.export(OUT)

# 导出后回读复检（float32 转换后是否仍水密）
chk = trimesh.load(OUT)
print("回读复检: %d 面, watertight=%s, vol=%.0f" % (
    len(chk.faces), chk.is_watertight, chk.volume))
print("已写出", OUT)
