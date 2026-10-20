"""v3 顶盖：用 trimesh 布尔运算重做（面封闭，不斜边破洞）
  ① 屏幕窗扩到 33.8 x 31.1（布尔减长方体）
  ② 删右侧两个大圆腔（布尔减 2 圆柱）
  ③ 屏幕下方一排 2mm 圆柱蜂窝孔（布尔减一排小圆柱）
  其余（左侧腔、定位柱、卡扣、按键窗）全保留
"""
import trimesh, numpy as np

SRC = "泰山派顶盖.STL"
OUT = "泰山派顶盖_v3.STL"

m = trimesh.load(SRC)
print("原顶盖 %d 面, 体积 %.0f mm³" % (len(m.faces), m.volume))

# 外廓 74.5 x 6.5 x 55.5，Y 是厚度（0~6.5）
# 布尔减的实体都要在 Y 方向贯穿（Y -1 ~ 7.5），保证挖穿
Y_LO, Y_HI = -1.0, 7.5

def thru_box(cx, cz, w, h):
    """XZ 平面上的长方体，Y 贯穿"""
    b = trimesh.creation.box(extents=[w, Y_HI - Y_LO, h])
    b.apply_translation([cx, (Y_LO + Y_HI) / 2, cz])
    return b

def thru_cyl(cx, cz, r):
    """XZ 平面上的圆柱（轴沿 Y），贯穿"""
    c = trimesh.creation.cylinder(radius=r, height=Y_HI - Y_LO, sections=48)
    # trimesh cylinder 默认轴沿 Z；绕 X 轴转 90° 让轴落到 Y
    R = np.array([[1, 0, 0, 0],
                  [0, 0, -1, 0],
                  [0, 1, 0, 0],
                  [0, 0, 0, 1]], dtype=np.float64)
    c.apply_transform(R)
    c.apply_translation([cx, (Y_LO + Y_HI) / 2, cz])
    return c

# ① 屏幕窗：原中心 X=50.5 Z=30.75，扩到 33.8 x 31.1
screen = thru_box(50.5, 30.75, 33.8, 31.1)

# ② 删所有大圆腔（右侧一上一下 + 左侧大圆腔，全清掉）
#    底薄板 Y=0.3 实扫：左侧大圆腔 圆心(X=30,Z=28) 直径≈20；下方小圆 (X=30,Z=42) 直径≈8
#    右侧两个（你 3D 图）：上小圆 (X=62,Z=42) 直径≈12，下大圆 (X=59,Z=24) 直径≈22
circle_big_L   = thru_cyl(30.0, 28.0, 10.0)   # 左侧大圆腔
circle_small_L = thru_cyl(30.0, 42.0, 5.0)    # 左侧下方小圆
circle_big_R   = thru_cyl(59.0, 24.0, 11.0)   # 右侧下大圆
circle_small_R = thru_cyl(62.0, 42.0, 6.0)     # 右侧上小圆

# ③ 蜂窝：屏幕窗正下方那条实心底（Z≈13.5，避开窗），一排 2mm 圆柱孔
HOLE_R = 1.1
GAP = 2.4
holes = []
x = 38.0
while x <= 63.0:
    holes.append(thru_cyl(x, 13.5, HOLE_R))
    x += GAP

cuts = [screen, circle_big_L, circle_small_L, circle_big_R, circle_small_R] + holes
print("减数体:", len(cuts), "个 (屏幕窗1 + 大圆4 + 蜂窝%d)" % len(holes))

# 一次性布尔减
result = m
for c in cuts:
    result = result.difference(c)

# 清理
trimesh.repair.fix_normals(result)
result = result.process(validate=True)
print("结果: %d 面, 体积 %.0f mm³, watertight=%s" % (len(result.faces), result.volume, result.is_watertight))
result.export(OUT)
print("已写出", OUT)
