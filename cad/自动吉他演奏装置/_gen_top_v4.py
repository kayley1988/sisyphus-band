"""v4 顶盖：参数化干净重建（不在坏 STL 上做布尔）
74.5 x 55.5 平板，Y 厚 6.5，4 角 M3 定位柱（沉入板内 d8）
  ① 屏幕窗 33.8 x 31.1 中心(50.5, 30.75)
  ② 4 圆腔：左大(30,28,d20) 左小(30,42,d10) 右大(59,24,d22) 右上小(62,42,d12)
  ③ 屏幕下方一排 2mm 蜂窝（Z≈13.5）
"""
import trimesh, numpy as np

OUT = "泰山派顶盖_v4.STL"
X0, X1, Z0, Z1 = 0.0, 74.5, 0.0, 55.5
Y0, Y1 = 0.0, 6.5

def box_c(x0, x1, z0, z1):
    b = trimesh.creation.box(extents=[x1-x0, Y1-Y0, z1-z0])
    b.apply_translation([(x0+x1)/2, (Y0+Y1)/2, (z0+z1)/2])
    return b

def cyl(cx, cz, r):
    c = trimesh.creation.cylinder(radius=r, height=Y1-Y0, sections=48)
    R = np.array([[1,0,0,0],[0,0,-1,0],[0,1,0,0],[0,0,0,1]], dtype=np.float64)
    c.apply_transform(R)
    c.apply_translation([cx, (Y0+Y1)/2, cz])
    return c

# 主板
board = trimesh.creation.box(extents=[X1-X0, Y1-Y0, Z1-Z0])
board.apply_translation([(X0+X1)/2, (Y0+Y1)/2, (Z0+Z1)/2])

# ④ 4 角定位柱（M3 热熔螺母 d8，沉入板内）
for cx, cz in [(3.5,3.5),(71.0,3.5),(3.5,52.0),(71.0,52.0)]:
    board = board.union(cyl(cx, cz, 4.0))

# ① 屏幕窗
screen = box_c(50.5-16.9, 50.5+16.9, 30.75-15.55, 30.75+15.55)

# ② 4 圆腔
circles = [
    cyl(30.0, 28.0, 10.0),
    cyl(30.0, 42.0, 5.0),
    cyl(59.0, 24.0, 11.0),
    cyl(62.0, 42.0, 6.0),
]

# ③ 蜂窝
holes = []
x = 38.0
while x <= 63.0:
    holes.append(cyl(x, 13.5, 1.1))
    x += 2.4

# 减
for c in [screen] + circles + holes:
    board = board.difference(c)

trimesh.repair.fix_normals(board)
board = board.process(validate=True)
print("v4: %d 面, 体积 %.0f mm³, watertight=%s" % (len(board.faces), board.volume, board.is_watertight))
board.export(OUT)
print("已写出", OUT)
