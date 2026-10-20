# -*- coding: utf-8 -*-
"""
拨片头（PETG）——照原版 拨片1.STL 的真实内腔尺寸设计（不再猜）。
内腔实测（trimesh 切原版拨片1）：
    开口端 X=26，槽从 X≈18 到 26（深 8mm）
    内环 Y 0.90..1.60（高 0.70，中心 1.25）× Z 1.25..3.75（宽 2.50，中心 2.50）
设计：
    细柄 0.50×2.30，插入 7.8mm（双边留 0.10 间隙）
    限位肩 1.7(Y)×3.8(Z)×1.2(X)，贴在拨片端面上
    尖头 4.0mm 外伸，宽度 3.0 收窄到圆尖（吉他拨片尖），厚 1.0
"""
import numpy as np
import trimesh
from trimesh.creation import box, extrude_polygon
from trimesh.transformations import translation_matrix

OUT = r"E:\gt\自动吉他演奏装置\拨片头_PETG.STL"
OUT_ASM = r"E:\gt\自动吉他演奏装置\拨片1+头_装配预览_v2.STL"
PICK = r"E:\gt\自动吉他演奏装置\拨片1.STL"

CY, CZ = 1.25, 2.50          # 内腔中心

# ---- 细柄 ----
S_T, S_W, S_L = 0.50, 2.30, 7.8
shank = box(extents=[S_L, S_T, S_W])
shank.apply_translation([S_L / 2, CY, CZ])

# ---- 限位肩 ----
F_T, F_W, F_L = 1.7, 3.8, 1.2
flange = box(extents=[F_L, F_T, F_W])
flange.apply_translation([S_L + F_L / 2, CY, CZ])

# ---- 尖头：X-Z 平面轮廓挤出（Y 厚 1.0，中心 CY）----
TIP_L, TIP_W, TIP_T = 4.0, 3.0, 1.0
x0 = S_L + F_L
poly = [
    (x0, CZ - TIP_W / 2),
    (x0 + TIP_L * 0.55, CZ - TIP_W / 2 * 0.55),
    (x0 + TIP_L - 0.8, CZ - 0.45),
    (x0 + TIP_L, CZ),                # 尖点
    (x0 + TIP_L - 0.8, CZ + 0.45),
    (x0 + TIP_L * 0.55, CZ + TIP_W / 2 * 0.55),
    (x0, CZ + TIP_W / 2),
]
tip = extrude_polygon(__import__("shapely").geometry.Polygon(poly), height=TIP_T)
# extrude_polygon 的多边形第2坐标落在全局 Y、挤出方向是全局 Z —— 旋转 90°(绕X) 让宽度回到 Z、厚度回到 Y
import math
from trimesh.transformations import rotation_matrix as _rot
tip.apply_transform(_rot(math.pi / 2, [1, 0, 0]))   # Y'<-Z, Z'<-Y
tip.apply_transform(translation_matrix([0, 1.75, 0]))  # 厚度对中到 CY=1.25

head = trimesh.util.concatenate([shank, flange, tip])
head = head.union(shank).union(flange).union(tip) if False else head
# 用 manifold 做真并集
try:
    import manifold3d  # noqa
    head = trimesh.boolean.union([shank, flange, tip])
    print("布尔并集 OK")
except Exception as e:
    print("布尔并集失败(%s)，退化为拼接" % e)
head.export(OUT)
print("体积 %.3f cm3  估算重量(PETG 1.27 实心) %.2f g" % (head.volume / 1000, head.volume / 1000 * 1.27))
bb = head.bounds
print("包围盒: X %.2f..%.2f (%.2f)  Y %.2f..%.2f (%.2f)  Z %.2f..%.2f (%.2f)" % (
    bb[0][0], bb[1][0], bb[1][0] - bb[0][0],
    bb[0][1], bb[1][1], bb[1][1] - bb[0][1],
    bb[0][2], bb[1][2], bb[1][2] - bb[0][2]))
print("watertight:", head.is_watertight)

# ---- 装配预览：尖头插进原版拨片1 ----
pick = trimesh.load(PICK)
head_asm = head.copy()
head_asm.apply_transform(translation_matrix([26.0 - S_L, 0, 0]))  # 细柄底对到槽底
asm = trimesh.boolean.union([pick, head_asm])
asm.export(OUT_ASM)
print("装配预览已导出:", OUT_ASM)

# ---- 渲染三视图 PNG ----
from PIL import Image, ImageDraw
def silhouette(mesh, ax_h, ax_v, px_per_mm, title, flipv=True):
    bmin, bmax = mesh.bounds
    W = int((bmax[ax_h] - bmin[ax_h]) * px_per_mm) + 20
    H = int((bmax[ax_v] - bmin[ax_v]) * px_per_mm) + 20
    img = Image.new("RGB", (W, H), (255, 255, 255))
    dr = ImageDraw.Draw(img)
    # 采样切片栅格化太慢 → 直接投影像素：用密集射线太慢，改用三角投影填充
    pts2 = []
    for tri in mesh.triangles:
        for p in tri:
            a = (p[ax_h] - bmin[ax_h]) * px_per_mm + 10
            b = (p[ax_v] - bmin[ax_v]) * px_per_mm + 10
            if flipv:
                b = H - b
            pts2.append((a, b))
    # 画所有边（线框近似剪影）
    i = 0
    for tri in mesh.triangles:
        ps = pts2[i:i + 3]; i += 3
        dr.polygon(ps, fill=(40, 60, 160))
    img.save(title)
    print("saved", title)

silhouette(head, 0, 2, 40, r"E:\gt\自动吉他演奏装置\_尖头_俯视XZ.png")
silhouette(head, 0, 1, 40, r"E:\gt\自动吉他演奏装置\_尖头_侧视XY.png")
silhouette(asm, 0, 2, 12, r"E:\gt\自动吉他演奏装置\_装配预览v2_俯视.png")
