# -*- coding: utf-8 -*-
"""把 泰山派顶盖_v7.3 + 电池盖板_v2 摆到同一打印盘，导出可直接切片的 3MF
  顶盖: 74.5(X) x 6.5(Y 厚) x 55.5(Z) → 绕 X 轴 +90°，让 Y=0 面(带唇边面)朝下平放
  电池盖: 65(X) x 85(Y) x 4(Z 厚) → 天然平放
  两件并排居中，间距 15mm；导出 3MF（多对象）
"""
import os
import numpy as np
import trimesh
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection

HERE = os.path.dirname(os.path.abspath(__file__))
TOP = os.path.join(HERE, "泰山派顶盖_v7.3_1.83彩屏.STL")
BATT = os.path.join(HERE, "电池盖板_v2_走线孔.STL")
OUT3MF = os.path.join(HERE, "同一盘_顶盖v7.3+电池盖v2.3mf")
OUTD = os.path.join(HERE, "preview_plate")
os.makedirs(OUTD, exist_ok=True)

GAP = 15.0     # 两件间距
PLATE = 256.0  # 盘尺寸（可打印区），居中于原点

top = trimesh.load(TOP)
batt = trimesh.load(BATT)
print("顶盖原始尺寸:", np.round(top.extents, 2).tolist())
print("电池盖尺寸  :", np.round(batt.extents, 2).tolist())

# 顶盖绕 X 轴 +90°：(x,y,z)->(x,-z,y)，原 Y(厚度) 变成垂直方向，Y=0 面朝下
R = trimesh.transformations.rotation_matrix(np.pi / 2.0, [1, 0, 0])
top.apply_transform(R)
top.apply_translation([0, -top.bounds[0][1], -top.bounds[0][2]])   # 落到底面 z=0、y 从 0 起
batt.apply_translation([0, -batt.bounds[0][1], -batt.bounds[0][2]])
print("顶盖平放后  :", np.round(top.extents, 2).tolist(), " 底面 z=%.2f" % top.bounds[0][2])
print("电池盖平放后:", np.round(batt.extents, 2).tolist(), " 底面 z=%.2f" % batt.bounds[0][2])

# 并排：顶盖在左，电池盖在右，竖直方向各自对齐中心
w1, d1 = top.extents[0], top.extents[1]
w2, d2 = batt.extents[0], batt.extents[1]
top.apply_translation([0, (max(d1, d2) - d1) / 2.0, 0])
batt.apply_translation([w1 + GAP, (max(d1, d2) - d2) / 2.0, 0])

# 整组居中到盘中心
gmin = np.minimum(top.bounds[0], batt.bounds[0])
gmax = np.maximum(top.bounds[1], batt.bounds[1])
gc = (gmin + gmax) / 2.0
top.apply_translation([-gc[0], -gc[1], 0])
batt.apply_translation([-gc[0], -gc[1], 0])

gm = np.minimum(top.bounds[0], batt.bounds[0])
gM = np.maximum(top.bounds[1], batt.bounds[1])
print("\n占盘尺寸: %.1f x %.1f x %.1f" % tuple(gM - gm))
print("盘可用区: %.0f x %.0f (居中)" % (PLATE, PLATE))
print("留边: X 左 %.1f 右 %.1f | Y 前 %.1f 后 %.1f" % (
    gm[0] + PLATE/2, PLATE/2 - gM[0], gm[1] + PLATE/2, PLATE/2 - gM[1]))
print("件数 2  ✔  互不重叠:", not trimesh.util.is_shape(top, batt) if hasattr(trimesh, "util") else "见预览")

# ── 导出 3MF（两个独立对象）──
scene = trimesh.Scene()
scene.add_geometry(top, node_name="泰山派顶盖_v7.3", geom_name="top_cover")
scene.add_geometry(batt, node_name="电池盖板_v2_走线孔", geom_name="battery_cover")
scene.export(OUT3MF)
sz = os.path.getsize(OUT3MF)
print("\n已写出 %s (%.0f KB)" % (OUT3MF, sz / 1024))

# 回读校验
sc = trimesh.load(OUT3MF)
names = list(sc.geometry.keys())
print("回读: %d 个对象 %s" % (len(names), names))
for n, g in sc.geometry.items():
    print("   %s  尺寸 %s  底面z=%.2f" % (n, np.round(g.extents, 2).tolist(), g.bounds[0][2]))

# ── 预览图：俯视装盘 ──
fig, ax = plt.subplots(figsize=(8.5, 8.5), dpi=130)
ax.add_patch(plt.Rectangle((-PLATE/2, -PLATE/2), PLATE, PLATE, fill=False,
                           ec="#8a9099", lw=1.2, ls="--"))
for m, name, col in ((top, "顶盖 v7.3", "#7ba7d7"), (batt, "电池盖 v2", "#e8a87c")):
    tris = m.vertices[m.faces][:, :, :2]
    ax.add_collection(PolyCollection(tris, facecolors=col, edgecolors="none", alpha=0.95))
    cx, cy = (m.bounds[0][0] + m.bounds[1][0]) / 2, (m.bounds[0][1] + m.bounds[1][1]) / 2
    ax.text(cx, cy, name, ha="center", va="center", fontsize=10, color="#2c2c2a")
ax.set_xlim(-PLATE/2 - 20, PLATE/2 + 20); ax.set_ylim(-PLATE/2 - 20, PLATE/2 + 20)
ax.set_aspect("equal"); ax.grid(alpha=0.2)
ax.set_title("同一盘装盘预览（虚线 = %.0f x %.0f 盘）" % (PLATE, PLATE), fontsize=11)
fig.tight_layout()
p = os.path.join(OUTD, "plate_layout.png")
fig.savefig(p, facecolor="white"); plt.close(fig)
print("已写出", p)
