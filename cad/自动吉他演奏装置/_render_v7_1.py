# -*- coding: utf-8 -*-
"""渲染 v7.1 顶盖预览：① 俯视开孔图（切片法，标注尺寸）② 立体着色图"""
import os
import struct
import numpy as np
import trimesh
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
STL = os.path.join(HERE, "泰山派顶盖_v7.1_1.83彩屏.STL")
OUTD = os.path.join(HERE, "preview_v7.1_1")
os.makedirs(OUTD, exist_ok=True)


def load_tris(p):
    with open(p, "rb") as f:
        f.read(80)
        n = struct.unpack("<I", f.read(4))[0]
        d = f.read(n * 50)
    out = []
    for i in range(n):
        v = struct.unpack("<12f", d[i*50:i*50+48])
        out.append((v[3:6], v[6:9], v[9:12]))
    return out


def mask_at(tris, yv, step=0.25, nx=None, nz=None):
    nx = nx or int(74.5/step) + 1
    nz = nz or int(55.5/step) + 1
    segs = []
    for t in tris:
        pts = []
        for i in range(3):
            a, b = t[i], t[(i+1) % 3]
            if (a[1]-yv) * (b[1]-yv) < 0:
                f = (yv-a[1])/(b[1]-a[1])
                pts.append((a[0]+f*(b[0]-a[0]), a[2]+f*(b[2]-a[2])))
        if len(pts) == 2:
            segs.append(pts)
    m = np.zeros((nx, nz), bool)
    for iz in range(nz):
        z = iz*step
        xs = []
        for (x0, z0), (x1, z1) in segs:
            if (z0-z)*(z1-z) < 0:
                f = (z-z0)/(z1-z0)
                xs.append(x0+f*(x1-x0))
        xs.sort()
        for k in range(0, len(xs)-1, 2):
            m[max(0, int(xs[k]/step)):min(nx-1, int(xs[k+1]/step))+1, iz] = True
    return m, step


tris = load_tris(STL)

# ── ① 俯视开孔图 ──
top, step = mask_at(tris, 6.2)
bot, _ = mask_at(tris, 0.3)
nx, nz = top.shape
SC = 10
img = Image.new("RGB", (nx*SC, nz*SC), (245, 245, 240))
d = ImageDraw.Draw(img)
for ix in range(nx):
    for iz in range(nz):
        y = (nz-1-iz)*SC
        if top[ix, iz] and bot[ix, iz]:
            col = (150, 155, 160)
        elif top[ix, iz]:
            col = (120, 125, 132)
        elif bot[ix, iz]:
            col = (208, 212, 216)
        else:
            col = (255, 255, 255)
        d.rectangle([ix*SC, y, ix*SC+SC-1, y+SC-1], fill=col)
# 标注
d.rectangle([0, 0, nx*SC-1, nz*SC-1], outline=(90, 95, 100), width=2)
d.text((14, 12), "TOP VIEW  74.5 x 55.5 mm  (holes = white)", fill=(30, 30, 30))
d.text((int(29.55/0.25)*SC, int((14.25-3)/0.25)*SC), "SCREEN 35.9x30.5 @(47.5,29.5)", fill=(180, 30, 30))
d.text((int(36.5/0.25)*SC, int((14.5)/0.25)*SC), "HONEYCOMB 40 x D2.3", fill=(30, 90, 180))
d.text((int(9.5/0.25)*SC, int((36)/0.25)*SC), "GOURD AREA FILLED SOLID", fill=(20, 120, 60))
p1 = os.path.join(OUTD, "v7.1_top.png")
img.save(p1)
print("已写出", p1)

# ── ② 立体着色图 ──
mesh = trimesh.load(STL)
V = mesh.vertices.copy()
V = V[:, [0, 2, 1]]            # 重排：X→x, Z→y, Y→z（厚度朝上）
F = mesh.faces
tri = V[F]
n = mesh.face_normals[:, [0, 2, 1]]
light = np.array([0.35, -0.75, 0.56]); light /= np.linalg.norm(light)
lum = 0.30 + 0.70*np.clip(n @ light, 0, 1)
base = np.array([0.36, 0.47, 0.60])
cols = np.clip(lum[:, None] * base[None, :] * 1.9, 0, 1)

fig = plt.figure(figsize=(11, 7.2), dpi=140)
ax = fig.add_subplot(111, projection="3d")
pc = Poly3DCollection(tri, facecolors=cols, edgecolors="none")
ax.add_collection3d(pc)
ax.set_xlim(0, 74.5); ax.set_ylim(0, 55.5); ax.set_zlim(0, 26)
ax.set_box_aspect((74.5, 55.5, 26))
ax.view_init(elev=32, azim=-58)
ax.set_axis_off()
ax.set_title("v7.1 top cover  ·  1.83\" screen window + honeycomb  (Y axis exaggerated x4)",
             fontsize=10, color="#333")
fig.tight_layout()
p2 = os.path.join(OUTD, "v7.1_3d.png")
fig.savefig(p2, facecolor="white")
print("已写出", p2)
