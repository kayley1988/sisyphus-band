# -*- coding: utf-8 -*-
"""渲染电池盖板：三视图 + 立体图，看清结构与开孔面"""
import os
import numpy as np
import trimesh
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

HERE = os.path.dirname(os.path.abspath(__file__))
STL = os.path.join(HERE, "电池盖板.STL")
OUTD = os.path.join(HERE, "preview_batt")
os.makedirs(OUTD, exist_ok=True)

m = trimesh.load(STL)
V = m.vertices.copy()
F = m.faces
n = m.face_normals
print("尺寸:", np.round(m.bounds[1] - m.bounds[0], 2).tolist())

light = np.array([0.3, -0.6, 0.74]); light /= np.linalg.norm(light)


def shade(mesh, base):
    nn = mesh.face_normals
    lum = 0.35 + 0.65 * np.clip(nn @ light, 0, 1)
    return np.clip(lum[:, None] * np.array(base)[None, :] * 1.85, 0, 1)


# ── 立体图（上视 & 下视）──
for tag, view in (("top", (35, -60)), ("bot", (-35, 120))):
    fig = plt.figure(figsize=(9, 8), dpi=130)
    ax = fig.add_subplot(111, projection="3d")
    pc = Poly3DCollection(V[F], facecolors=shade(m, (0.42, 0.53, 0.66)), edgecolors="none")
    ax.add_collection3d(pc)
    ax.set_xlim(0, 65); ax.set_ylim(0, 85); ax.set_zlim(0, 4)
    ax.set_box_aspect((65, 85, 12))
    ax.view_init(elev=view[0], azim=view[1])
    ax.set_axis_off()
    ax.set_title("电池盖板 65 x 85 x 4  (%s)" % tag, fontsize=10)
    fig.tight_layout()
    p = os.path.join(OUTD, "batt_%s.png" % tag)
    fig.savefig(p, facecolor="white"); plt.close(fig)
    print("已写出", p)

# ── 沿长度方向（Y）的剖面：XZ 截面轮廓 ──
fig, axes = plt.subplots(6, 1, figsize=(10, 9), dpi=130)
for ax, y in zip(axes, [0.5, 1.0, 2.0, 3.0, 4.5, 20.0]):
    sec = m.section(plane_origin=[0, y, 0], plane_normal=[0, 1, 0])
    if sec is None:
        continue
    for p in sec.discrete:
        ax.plot(np.append(p[:, 0], p[0, 0]), np.append(p[:, 2], p[0, 2]), "-", lw=1, color="#2b6cb0")
    ax.set_title("Y=%.1f 截面 (X %0.0f~%0.0f, Z 0~4)" % (y, V[:, 0].min(), V[:, 0].max()), fontsize=9)
    ax.set_xlim(-2, 67); ax.set_ylim(-1, 5); ax.set_aspect("equal"); ax.grid(alpha=.25)
fig.tight_layout()
p = os.path.join(OUTD, "batt_sections.png")
fig.savefig(p, facecolor="white"); plt.close(fig)
print("已写出", p)

# ── 俯视轮廓（沿 Z 投影）──
top = m.section(plane_origin=[0, 0, 3.6], plane_normal=[0, 0, 1])   # 上表面附近
bot = m.section(plane_origin=[0, 0, 0.4], plane_normal=[0, 0, 1])   # 下表面附近
fig, axes = plt.subplots(1, 2, figsize=(12, 7.6), dpi=130)
for ax, sec, name in ((axes[0], top, "Z=3.6 靠上"), (axes[1], bot, "Z=0.4 靠下")):
    if sec is not None:
        for p in sec.discrete:
            ax.plot(np.append(p[:, 0], p[0, 0]), np.append(p[:, 1], p[0, 1]), "-", lw=1.2, color="#b03a2e")
    ax.set_title(name + "  (X 0~65, Y 0~85)", fontsize=10)
    ax.set_xlim(-3, 68); ax.set_ylim(-3, 88); ax.set_aspect("equal"); ax.grid(alpha=.25)
    ax.invert_yaxis()
fig.tight_layout()
p = os.path.join(OUTD, "batt_plan.png")
fig.savefig(p, facecolor="white"); plt.close(fig)
print("已写出", p)
