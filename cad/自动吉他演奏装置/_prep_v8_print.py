# -*- coding: utf-8 -*-
"""把 泰山派顶盖_v8(1.54屏) + 电池盖板_v2 摆到同一打印盘，导出可切片 3MF
  顶盖: 74.5(X) x 6.5(Y 厚) x 55.5(Z) → 绕 X 轴 +90° 平放（Y=0 唇边面朝下）
  电池盖: 65(X) x 85(Y) x 4(Z 厚) → 天然平放
  同时导出两个平放 STL，供 bambu-studio CLI 直接切片
"""
import os
import numpy as np
import trimesh

HERE = os.path.dirname(os.path.abspath(__file__))
TOP = os.path.join(HERE, "泰山派顶盖_v8_1.54彩屏.STL")
BATT = os.path.join(HERE, "电池盖板_v2_走线孔.STL")
OUT3MF = os.path.join(HERE, "同一盘_顶盖v8+电池盖v2.3mf")
JOB = os.path.join(HERE, "_print_job")

GAP = 15.0
PLATE = 256.0

top = trimesh.load(TOP)
batt = trimesh.load(BATT)
print("顶盖 v8 原始尺寸:", np.round(top.extents, 2).tolist())
print("电池盖 v2 尺寸  :", np.round(batt.extents, 2).tolist())

R = trimesh.transformations.rotation_matrix(np.pi / 2.0, [1, 0, 0])
top.apply_transform(R)
top.apply_translation([0, -top.bounds[0][1], -top.bounds[0][2]])
batt.apply_translation([0, -batt.bounds[0][1], -batt.bounds[0][2]])
print("顶盖 v8 平放后  :", np.round(top.extents, 2).tolist(), " 底面 z=%.2f" % top.bounds[0][2])
print("电池盖 v2 平放后:", np.round(batt.extents, 2).tolist(), " 底面 z=%.2f" % batt.bounds[0][2])

w1, d1 = top.extents[0], top.extents[1]
w2, d2 = batt.extents[0], batt.extents[1]
top.apply_translation([0, (max(d1, d2) - d1) / 2.0, 0])
batt.apply_translation([w1 + GAP, (max(d1, d2) - d2) / 2.0, 0])

gmin = np.minimum(top.bounds[0], batt.bounds[0])
gmax = np.maximum(top.bounds[1], batt.bounds[1])
gc = (gmin + gmax) / 2.0
top.apply_translation([-gc[0], -gc[1], 0])
batt.apply_translation([-gc[0], -gc[1], 0])

gm = np.minimum(top.bounds[0], batt.bounds[0])
gM = np.maximum(top.bounds[1], batt.bounds[1])
print("\n占盘尺寸: %.1f x %.1f x %.1f" % tuple(gM - gm))
print("留边: X 左 %.1f 右 %.1f | Y 前 %.1f 后 %.1f" % (
    gm[0] + PLATE/2, PLATE/2 - gM[0], gm[1] + PLATE/2, PLATE/2 - gM[1]))

scene = trimesh.Scene()
scene.add_geometry(top, node_name="泰山派顶盖_v8_1.54彩屏", geom_name="top_cover_v8")
scene.add_geometry(batt, node_name="电池盖板_v2_走线孔", geom_name="battery_cover_v2")
scene.export(OUT3MF)
print("\n已写出 %s (%.0f KB)" % (OUT3MF, os.path.getsize(OUT3MF) / 1024))

# 平放 STL（给 CLI 切片用）
os.makedirs(JOB, exist_ok=True)
p_top = os.path.join(JOB, "top_v8_flat.stl")
p_batt = os.path.join(JOB, "batt_v2_flat.stl")
top.export(p_top)
batt.export(p_batt)
print("已写出", p_top)
print("已写出", p_batt)

sc = trimesh.load(OUT3MF)
print("回读: %d 个对象 %s" % (len(sc.geometry), list(sc.geometry.keys())))
for n, g in sc.geometry.items():
    print("   %s  尺寸 %s  底面z=%.2f" % (n, np.round(g.extents, 2).tolist(), g.bounds[0][2]))
