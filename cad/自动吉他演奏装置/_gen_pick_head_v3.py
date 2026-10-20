# -*- coding: utf-8 -*-
"""
拨片头（PETG）v3 —— 照 Z 的实物照片形状：平直薄片 + 末端收尖（比例 6:1）。
接口按原版 拨片1.STL 实测扁槽校准（唯一硬接口）：
    槽 Y 0.90..1.60（厚0.70，中心1.25）× Z 1.25..3.75（宽2.50，中心2.50），深 8mm，开口在端面
尺寸：
    直段宽 2.3 / 厚 0.5，插入 7.8
    止退肩 0.9×2.9×1.0（贴端面，盖住槽口防推穿）
    尖段外伸 5.2，宽度 2.3 对称收成圆尖（总长/宽 ≈ 6:1，同实物照片比例）
"""
import math
import numpy as np
import trimesh
from trimesh.creation import box, extrude_polygon
from trimesh.transformations import translation_matrix, rotation_matrix
import shapely

OUT = r"E:\gt\自动吉他演奏装置\拨片头_PETG.STL"
OUT_ASM = r"E:\gt\自动吉他演奏装置\拨片1+头_装配预览_v2.STL"
PICK = r"E:\gt\自动吉他演奏装置\拨片1.STL"

CY, CZ = 1.25, 2.50

S_T, S_W, S_L = 0.50, 2.30, 7.8          # 细柄
F_T, F_W, F_L = 0.90, 2.90, 1.0          # 止退肩
TIP_L, TIP_T = 5.2, 0.50                 # 尖段

x0 = S_L + F_L                            # 尖段起点 8.8
half = S_W / 2
poly = [
    (x0, CZ - half),
    (x0 + TIP_L * 0.45, CZ - half * 0.75),
    (x0 + TIP_L - 0.9, CZ - 0.38),
    (x0 + TIP_L, CZ),                     # 圆尖
    (x0 + TIP_L - 0.9, CZ + 0.38),
    (x0 + TIP_L * 0.45, CZ + half * 0.75),
    (x0, CZ + half),
]
tip = extrude_polygon(shapely.Polygon(poly), height=TIP_T)
tip.apply_transform(rotation_matrix(math.pi / 2, [1, 0, 0]))   # 宽度回 Z、厚度回 Y
tip.apply_transform(translation_matrix([0, (CY - TIP_T / 2) - 0 + 0.75, 0]))
# 旋转后厚度位于 Y -0.5..0 → 平移到 CY-0.25 = 1.0..1.5 → 平移量 = 1.0+0.5 = 1.5? 统一校正：
tip.apply_translation([0, 0.75, 0])  # 微调对中（见下方自动校正）

# 自动把尖段 Y 对中到 CY
b = tip.bounds
tip.apply_translation([0, CY - (b[0][1] + b[1][1]) / 2, 0])

shank = box(extents=[S_L, S_T, S_W]); shank.apply_translation([S_L/2, CY, CZ])
flange = box(extents=[F_L, F_T, F_W]); flange.apply_translation([S_L + F_L/2, CY, CZ])

head = trimesh.boolean.union([shank, flange, tip])
head.export(OUT)
print("体积 %.4f cm3  PETG 实心约 %.3f g" % (head.volume/1000, head.volume/1000*1.27))
bb = head.bounds
print("包围盒: X %.1f..%.1f (%.1f)  Y %.2f..%.2f (%.2f)  Z %.2f..%.2f (%.2f)" % (
    bb[0][0],bb[1][0],bb[1][0]-bb[0][0], bb[0][1],bb[1][1],bb[1][1]-bb[0][1], bb[0][2],bb[1][2],bb[1][2]-bb[0][2]))
print("watertight:", head.is_watertight)

pick = trimesh.load(PICK)
asm_head = head.copy(); asm_head.apply_transform(translation_matrix([26.0 - S_L, 0, 0]))
asm = trimesh.boolean.union([pick, asm_head])
asm.export(OUT_ASM)
print("装配预览:", OUT_ASM)

from PIL import Image, ImageDraw
def silhouette(mesh, ax_h, ax_v, ppm, path):
    b0, b1 = mesh.bounds
    W = int((b1[ax_h]-b0[ax_h])*ppm)+20; H = int((b1[ax_v]-b0[ax_v])*ppm)+20
    img = Image.new("RGB",(W,H),(255,255,255)); dr = ImageDraw.Draw(img)
    i = 0; tris = mesh.triangles
    for tri in tris:
        ps = []
        for p in tri:
            a=(p[ax_h]-b0[ax_h])*ppm+10; c=(p[ax_v]-b0[ax_v])*ppm+10
            ps.append((a, H-c))
        dr.polygon(ps, fill=(40,60,160))
    img.save(path)
silhouette(head, 0, 2, 40, r"E:\gt\自动吉他演奏装置\_尖头v3_俯视XZ.png")
silhouette(head, 0, 1, 40, r"E:\gt\自动吉他演奏装置\_尖头v3_侧视XY.png")
silhouette(asm, 0, 2, 12, r"E:\gt\自动吉他演奏装置\_装配v3_俯视.png")
print("三视图已存")
