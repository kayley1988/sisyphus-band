# -*- coding: utf-8 -*-
"""v7.3 vs 原版顶盖：对称差集分块 + 逐层孔洞对比，证明只改了该改的地方"""
import numpy as np
import trimesh
from shapely.geometry import Polygon

ORIG = "泰山派顶盖.STL"
NEW = "泰山派顶盖_v7.3_1.83彩屏.STL"

o = trimesh.load(ORIG).process(validate=True)
n = trimesh.load(NEW).process(validate=True)
print("原版: %d 面 vol=%.0f bounds=%s" % (len(o.faces), o.volume, np.round(o.bounds, 2).tolist()))
print("v7.3: %d 面 vol=%.0f bounds=%s" % (len(n.faces), n.volume, np.round(n.bounds, 2).tolist()))

# ── 1. 对称差集分块：原版有/新版无，新版有/原版无 ──
for tag, a, b in (("原版有→v7.3被填实", o, n), ("v7.3新增实体(原版无)", n, o)):
    d = trimesh.boolean.difference([a, b], engine="manifold")
    parts = d.split(only_watertight=False)
    print("\n[%s] 总体积 %.1f mm3, 分 %d 块:" % (tag, d.volume, len(parts)))
    rows = []
    for p in parts:
        v = abs(p.volume)
        if v < 0.5:
            continue
        b = p.bounds
        rows.append((v, b))
    rows.sort(key=lambda r: -r[0])
    for v, b in rows[:20]:
        print("   vol=%8.1f mm3  X[%6.2f,%6.2f] Y[%5.2f,%5.2f] Z[%6.2f,%6.2f]  (%.1f x %.1f x %.1f)" % (
            v, b[0][0], b[1][0], b[0][1], b[1][1], b[0][2], b[1][2],
            b[1][0]-b[0][0], b[1][1]-b[0][1], b[1][2]-b[0][2]))
    if len(rows) > 20:
        print("   ... 共 %d 块，其余更小" % len(rows))

# ── 2. 逐层孔洞清单对比（只看非蜂窝类孔）──
def holes(m, y):
    sec = m.section(plane_origin=[0, y, 0], plane_normal=[0, 1, 0])
    out = []
    for pts3 in sec.discrete:
        X, Z = pts3[:, 0], pts3[:, 2]
        p = Polygon(np.column_stack([X, Z]))
        b = p.bounds
        out.append((round(p.area, 1), round(p.centroid.x, 2), round(p.centroid.y, 2),
                    round(b[2]-b[0], 2), round(b[3]-b[1], 2)))
    out.sort(key=lambda r: -r[0])
    return out

for y in (3.25,):
    print("\n=== Y=%.2f 截面 面积/质心X/Z/宽/高 ===" % y)
    print("原版:")
    for r in holes(o, y):
        print("   area=%8.1f  @(%.2f,%.2f)  %.1f x %.1f" % r)
    print("v7.3:")
    for r in holes(n, y):
        print("   area=%8.1f  @(%.2f,%.2f)  %.1f x %.1f" % r)

# ── 3. 外形轮廓一致性：各层最大环对比 ──
print("\n=== 外轮廓（最大环）逐层对比 ===")
for y in (0.25, 1.0, 2.0, 3.25, 4.5, 5.5, 6.4):
    res = []
    for m in (o, n):
        areas = []
        for pts3 in m.section(plane_origin=[0, y, 0], plane_normal=[0, 1, 0]).discrete:
            P = Polygon(np.column_stack([pts3[:, 0], pts3[:, 2]]))
            areas.append(P)
        best = max(areas, key=lambda p: p.area)
        res.append((best.area, best.bounds))
    (a0, b0), (a1, b1) = res
    same = abs(a0-a1) < 0.5 and np.allclose(b0, b1, atol=0.05)
    print("Y=%4.2f 原版 %.1fmm2 X[%.2f,%.2f] Z[%.2f,%.2f] | v7.3 %.1fmm2 X[%.2f,%.2f] Z[%.2f,%.2f]  %s" % (
        y, a0, b0[0], b0[2], b0[1], b0[3], a1, b1[0], b1[2], b1[1], b1[3],
        "一致" if same else "★不同"))
