"""v5 顶盖：只动 3 处，其余保留原样（不碰定位柱/左侧按键腔）
  ① 旧屏幕窗 (X[36.5,64.5] Z[17,44.5]) 扩到 33.8 x 31.1，中心 (50.5, 30.75)
  ② 删屏幕窗两侧的两个半圆切角（你 3D 图里的两个大圆）
     左半圆 圆心(X=34.5, Z=30.75) r=15
     右半圆 圆心(X=66.5, Z=30.75) r=15
  ③ 屏幕下方蜂窝一排 2mm 圆柱孔（Z≈13.4，原 4 条横长空腔之一）
用 trimesh 布尔：板 = 原 STL（先 process 清理） - 减数体
"""
import trimesh, numpy as np

OUT = "泰山派顶盖_v5.STL"
m = trimesh.load("泰山派顶盖.STL")
m = m.process(validate=True)
print("原顶盖 %d 面, watertight=%s, 体积 %.0f" % (len(m.faces), m.is_watertight, m.volume))

Y0, Y1 = 0.0, 6.5
Y_LO, Y_HI = -1.0, 7.5

def thru_box(cx, cz, w, h):
    b = trimesh.creation.box(extents=[w, Y_HI - Y_LO, h])
    b.apply_translation([cx, (Y_LO + Y_HI) / 2, cz])
    return b

def thru_cyl(cx, cz, r, sections=48):
    c = trimesh.creation.cylinder(radius=r, height=Y_HI - Y_LO, sections=sections)
    R = np.array([[1,0,0,0],[0,0,-1,0],[0,1,0,0],[0,0,0,1]], dtype=np.float64)
    c.apply_transform(R)
    c.apply_translation([cx, (Y_LO + Y_HI) / 2, cz])
    return c

cuts = []
# ① 屏幕窗 33.8 x 31.1，中心 (50.5, 30.75)
cuts.append(thru_box(50.5, 30.75, 33.8, 31.1))
# ② 两个半圆切角（贴屏幕窗左右边缘）
cuts.append(thru_cyl(34.5, 30.75, 15.0))   # 左半圆
cuts.append(thru_cyl(66.5, 30.75, 15.0))   # 右半圆
# ③ 蜂窝一排（Z≈13.4 那条空腔，11 个 2mm 圆柱）
x = 38.0
while x <= 63.0:
    cuts.append(thru_cyl(x, 13.4, 1.1))
    x += 2.4

result = m
for c in cuts:
    result = result.difference(c)
trimesh.repair.fix_normals(result)
result = result.process(validate=True)
print("v5: %d 面, 体积 %.0f, watertight=%s" % (len(result.faces), result.volume, result.is_watertight))
result.export(OUT)
print("已写出", OUT)
