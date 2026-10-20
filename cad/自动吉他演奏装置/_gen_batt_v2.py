# -*- coding: utf-8 -*-
"""电池盖板 v2 —— 中间开走线长方孔
  板: 65(X) x 85(Y) x 4(Z 厚)
  孔: 长 65 沿 Y（板长边方向，两端各留 10mm → Y 10~75）
      宽 30 沿 X（居中 → X 17.5~47.5）
      贯穿 4mm 厚度
  不碰：四周唇边台阶、Y0~5 的卡扣凸起/弹性缝/指孔
"""
import os
import time
import numpy as np
import trimesh

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "电池盖板.STL")
OUT = os.path.join(HERE, "电池盖板_v2_走线孔.STL")

# 长方孔参数（可微调）
HOLE_LEN = 65.0    # 沿 Y：85 - 10 - 10
HOLE_W = 30.0      # 沿 X：居中
MARGIN = 10.0      # 长边两端各留

t0 = time.time()
m = trimesh.load(SRC).process(validate=True)
b = m.bounds
print("原盖板: %d 面, watertight=%s, 尺寸 %s" % (
    len(m.faces), m.is_watertight, np.round(b[1] - b[0], 2).tolist()))

# 板中心
cx = (b[0][0] + b[1][0]) / 2.0            # X 中心 32.5
y0 = b[0][1] + MARGIN                     # Y 起 10
y1 = b[1][1] - MARGIN                     # Y 止 75
ycz = (y0 + y1) / 2.0
zlo, zhi = b[0][2] - 1.0, b[1][2] + 1.0   # 厚度方向过盈

cutter = trimesh.creation.box(extents=[HOLE_W, y1 - y0, zhi - zlo])
cutter.apply_translation([cx, ycz, (zlo + zhi) / 2.0])

res = trimesh.boolean.difference([m, cutter], engine="manifold")
res.merge_vertices()
res.update_faces(res.nondegenerate_faces())
res.update_faces(res.unique_faces())
trimesh.repair.fill_holes(res)
trimesh.repair.fix_normals(res)
res = res.process(validate=True)
_pairs = trimesh.grouping.group_rows(res.edges_sorted, require_count=2)
_open_n = len(res.edges) - 2 * len(_pairs)
print("v2 结果: %d 面, watertight=%s, vol=%.0f, 开放边=%d, 用时 %.1fs" % (
    len(res.faces), res.is_watertight, res.volume, _open_n, time.time() - t0))
res.export(OUT)

chk = trimesh.load(OUT)
print("回读复检: %d 面, watertight=%s, vol=%.0f" % (
    len(chk.faces), chk.is_watertight, chk.volume))
print("已写出", OUT)
