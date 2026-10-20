# -*- coding: utf-8 -*-
"""对原版 拨片1.STL 做逐层截面分析：搞清洞的真实位置/尺寸/是否端面开口，以及整体形状。"""
import trimesh, numpy as np
from PIL import Image, ImageDraw

MESH = trimesh.load(r"E:\gt\自动吉他演奏装置\拨片1.STL")
print("包围盒:", MESH.bounds)
bmin, bmax = MESH.bounds
print("尺寸 mm: X %.2f  Y %.2f  Z %.2f" % tuple(bmax - bmin))

# 沿 X 每 0.5mm 切一层，栅格化 YZ 截面（0.05mm 分辨率），拼成一张对比图
xs = np.arange(bmin[0] + 0.25, bmax[0], 0.5)
res = 0.05
H = int((bmax[1] - bmin[1]) / res) + 2
W = int((bmax[2] - bmin[2]) / res) + 2
ys = bmin[1] + np.arange(H) * res
zs = bmin[2] + np.arange(W) * res
cell = 10
img = Image.new("L", (W * cell + 40, H * cell), 255)
dr = ImageDraw.Draw(img)
for i, x in enumerate(xs):
    sec = MESH.section(plane_origin=[x, 0, 0], plane_normal=[1, 0, 0])
    occ = np.zeros((H, W), dtype=bool)
    if sec is not None:
        # 三角化截面多边形后栅格化
        try:
            tri, _ = trimesh.triangles.points_to_tris(sec.vertices) if False else (None, None)
        except Exception:
            pass
        for ent in sec.entities:
            pts = sec.vertices[ent.points][:, 1:3]  # Y,Z
            from PIL import Image as _I
            poly = [((p[1] - bmin[2]) / res, (H - 1 - (p[0] - bmin[1]) / res)) for p in pts]
            dr.polygon(poly, fill=120)
    # 标 X
    ox = 20 + i * cell
    img2 = img  # 直接在原图上把这一列画出来不行——改为每层单独画再贴
for i, x in enumerate(xs):
    pass

# 上面一版布局复杂，改简单：每层单独小图，横向拼接
tiles = []
for i, x in enumerate(xs):
    tile = Image.new("L", (W, H), 255)
    td = ImageDraw.Draw(tile)
    sec = MESH.section(plane_origin=[x, 0, 0], plane_normal=[1, 0, 0])
    if sec is not None:
        for ent in sec.entities:
            pts = sec.vertices[ent.points][:, 1:3]
            poly = [((p[1] - bmin[2]) / res, (H - 1 - (p[0] - bmin[1]) / res)) for p in pts]
            td.polygon(poly, fill=60)
    tiles.append(tile)
n = len(tiles)
sheet = Image.new("L", (W * n + (n - 1) * 6, H), 255)
for i, t in enumerate(tiles):
    sheet.paste(t.resize((W, H)), (i * (W + 6), 0))
sheet = sheet.resize((sheet.width * 8, sheet.height * 8), Image.NEAREST)
sheet.save(r"E:\gt\自动吉他演奏装置\_拨片1_逐层截面.png")
print("截面层数:", n, " 每层 X =", " ".join("%.1f" % v for v in xs))
print("saved _拨片1_逐层截面.png")

# 端面开口检查：X=bmax-0.05 处截面存在且含洞轮廓？
for x in (bmax[0] - 0.05, bmin[0] + 0.05):
    sec = MESH.section(plane_origin=[x, 0, 0], plane_normal=[1, 0, 0])
    print("X=%.2f 截面实体数: %s" % (x, "无" if sec is None else len(sec.entities)))

# 实心体素估计洞的净空：用 ray 测内部，沿 X 在中部打网格
ys_f = np.arange(bmin[1] + 0.1, bmax[1], 0.1)
zs_f = np.arange(bmin[2] + 0.1, bmax[2], 0.1)
xf = 22.0  # 洞区间中部
origin = np.tile([xf, 0, 0], (len(ys_f) * len(zs_f), 1))
origin[:, 1] = np.repeat(ys_f, len(zs_f))
origin[:, 2] = np.tile(zs_f, len(ys_f))
dirv = np.tile([1.0, 0, 0], (len(origin), 1))
hit = MESH.ray.intersects_location(origin, dirv, multiple_hits=True)
occ = np.zeros((len(ys_f), len(zs_f)), dtype=bool)
if len(hit[0]):
    locs = hit[0]
    idx = hit[1]
    cnt = np.zeros((len(ys_f), len(zs_f)), dtype=int)
    for p, k in zip(locs, idx):
        cnt[int(round((p[1] - bmin[1] - 0.1) / 0.1)), int(round((p[2] - bmin[2] - 0.1) / 0.1))] += 1
    occ = (cnt % 2) == 1
inside_rows = np.where(occ.any(axis=1))[0]
inside_cols = np.where(occ.any(axis=0))[0]
print("X=22 实心区: Y %.2f..%.2f  Z %.2f..%.2f" % (
    ys_f[inside_rows[0]], ys_f[inside_rows[-1]],
    zs_f[inside_cols[0]], zs_f[inside_cols[-1]]))
# 画实心/空洞图
t2 = Image.new("L", (len(zs_f), len(ys_f)), 255)
px = t2.load()
for a in range(len(ys_f)):
    for b in range(len(zs_f)):
        if not occ[a, b]:
            px[b, a] = 0  # 空洞=黑
t2 = t2.resize((len(zs_f) * 20, len(ys_f) * 20), Image.NEAREST)
t2.save(r"E:\gt\自动吉他演奏装置\_拨片1_X22内腔.png")
print("saved _拨片1_X22内腔.png (黑=空腔)")
