# -*- coding: utf-8 -*-
"""审计 v6 顶盖 v2：直接用 3D 截面环，输出真实 XZ 坐标 + 标注俯视图。"""
import os
import numpy as np
import trimesh
from shapely.geometry import Polygon

HERE = os.path.dirname(os.path.abspath(__file__))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

for name in ["泰山派顶盖_v7_1.83彩屏.STL", "泰山派顶盖.STL"]:
    p = os.path.join(HERE, name)
    m = trimesh.load(p)
    y0, y1 = m.bounds[0][1], m.bounds[1][1]
    print("=" * 72)
    print("%s  Y[%.2f,%.2f]" % (name, y0, y1))
    fig, ax = plt.subplots(figsize=(12, 9))
    seen = set()
    for frac in (0.3, 0.5, 0.7):
        y = y0 + (y1 - y0) * frac
        sec = m.section(plane_origin=[0, y, 0], plane_normal=[0, 1, 0])
        if sec is None:
            continue
        # 手动找闭环：用 discrete paths 的 3D 顶点
        try:
            paths = sec.discrete
        except Exception as e:
            print("  section discrete 失败:", e)
            continue
        n_hole = 0
        for pts3 in paths:
            # pts3: (n,3)
            X, Z = pts3[:, 0], pts3[:, 2]
            poly = Polygon(np.column_stack([X, Z]))
            if not poly.is_valid:
                poly = poly.buffer(0)
            area = poly.area
            c = poly.centroid
            bb = poly.bounds
            w, h = bb[2] - bb[0], bb[3] - bb[1]
            # 外轮廓 = 面积最大
            key = (round(c.x, 1), round(c.y, 1), round(area, 0))
            r_eq = float(np.sqrt(area / np.pi))
            is_circle = abs(w - h) < 0.5 and abs(2 * r_eq - w) < 0.8
            tag = "圆 r=%.2f" % r_eq if is_circle else "非圆"
            n_hole += 1
            print("  Y=%.2f  环: 质心(X=%7.2f, Z=%7.2f) area=%9.1f bbox %5.1fx%5.1f  %s" % (
                y, c.x, c.y, area, w, h, tag))
            ax.add_patch(plt.Circle((c.x, c.y), max(r_eq, 1.2), fill=False,
                                    color="red" if is_circle else "blue", lw=1.2))
            ax.plot(c.x, c.y, "r.", ms=3)
            ax.annotate("%.1f,%.1f r%.1f" % (c.x, c.y, r_eq),
                        (c.x, c.y), fontsize=7, color="darkred")
        print("  -- Y=%.2f 共 %d 个闭环" % (y, n_hole))
    ax.set_aspect("equal")
    ax.grid(True, alpha=0.3)
    ax.set_xlabel("X (mm)"); ax.set_ylabel("Z (mm)")
    ax.set_title(name + " 孔洞审计（红=圆环, 蓝=非圆环, 多层叠加）")
    out = os.path.join(HERE, "_audit_%s.png" % ("v6" if "v6" in name else "orig"))
    fig.savefig(out, dpi=110, bbox_inches="tight")
    print("  图:", out)
    plt.close(fig)
