# -*- coding: utf-8 -*-
"""渲染 v8 顶盖预览：① 俯视开孔图（标注新窗 + v7.3 旧窗对比）② 立体着色图"""
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
STL = os.path.join(HERE, "泰山派顶盖_v8_1.54彩屏.STL")
OUTD = os.path.join(HERE, "preview_v8")
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
d.rectangle([0, 0, nx*SC-1, nz*SC-1], outline=(90, 95, 100), width=2)
d.text((14, 12), "v8 TOP VIEW  74.5 x 55.5 mm  (holes = white)", fill=(30, 30, 30))

# 像素坐标换算：图里 Z 轴上下翻转（y = (nz-1-iz)*SC）
def px(x_mm, z_mm):
    return int(x_mm/step)*SC, (nz-1-int(z_mm/step))*SC

# 新窗 28.7 x 28.7 @ (47.5,29.5) —— 红框
x0, z0 = 47.5-28.7/2, 29.5-28.7/2
d.rectangle([px(x0, z0+28.7), px(x0+28.7, z0)], outline=(190, 20, 20), width=3)
d.text((px(x0, z0+28.7)[0], px(x0, z0+28.7)[1]-16),
       "v8 WIN 28.7x28.7  @(47.5,29.5)", fill=(180, 30, 30))
# 1.54 显示区 27.72 —— 绿框
o = (28.7-27.72)/2
d.rectangle([px(x0+o, z0+o+27.72), px(x0+o+27.72, z0+o)], outline=(20, 140, 60), width=2)
d.text((px(x0+o, z0+o)[0]+2, px(x0+o, z0+o+27.72)[1]-14),
       "1.54\" active 27.72x27.72", fill=(20, 130, 60))
# v7.3 旧窗 35.9 x 30.5 —— 蓝虚框（对比：v8 明显收小）
ox0, oz0 = 47.5-35.9/2, 29.5-30.5/2
for xx in range(int(ox0*2), int((ox0+35.9)*2), 2):
    for zz in (oz0, oz0+30.5):
        a, b = px(xx*0.5, zz)
        d.rectangle([a, b, a+4, b+1], fill=(40, 90, 190))
for zz in range(int(oz0*2), int((oz0+30.5)*2), 2):
    for xx in (ox0, ox0+35.9):
        a, b = px(xx, zz*0.5)
        d.rectangle([a, b, a+1, b+4], fill=(40, 90, 190))
d.text((px(ox0, oz0)[0], px(ox0, oz0)[1]+4),
       "v7.3 old 35.9x30.5 (too big)", fill=(40, 90, 190))
# 蜂窝标注
d.text((px(15.5, 41)[0], px(15.5, 41)[1]), "HONEYCOMB 40 x D2.3", fill=(30, 90, 180))
p1 = os.path.join(OUTD, "v8_top.png")
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
ax.set_title("v8 top cover  ·  1.54\" 240x240 window 28.7x28.7  (Y axis exaggerated x4)",
             fontsize=10, color="#333")
fig.tight_layout()
p2 = os.path.join(OUTD, "v8_3d.png")
fig.savefig(p2, facecolor="white")
print("已写出", p2)
