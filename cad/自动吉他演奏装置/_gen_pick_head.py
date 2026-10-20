# -*- coding: utf-8 -*-
"""
生成「拨片头（尼龙）」STL —— 与《拨云》拨片1.STL 同坐标系。

拨片1 末端实测（_check_cavity.py 200×200 网格）：
    X 18.0 .. 26.0 是一段中空套筒
    外截面：Y 0..2.50 (厚 2.50) × Z 0..5.00 (宽 5.00)
    内腔（洞）：约 Y 1.00..1.50 (高 ~0.50) × Z 1.25..3.75 (宽 ~2.50)
    ⇒ 洞是一个扁槽：0.50mm(厚) × 2.50mm(宽) × 8mm(深)

拨片头几何（10-07 按 Z 截图修正）：
    不是套帽，而是「插头」——细柄插进拨片末端洞里，外面带限位肩 + 尖头。
    X 18.0 .. 28.5（柄插 8mm，限位肩厚 1mm，尖头外伸 2.5mm）
"""
import struct, os, math

OUT1 = r"E:\gt\自动吉他演奏装置\拨片头_尼龙.STL"
OUT2 = r"E:\gt\自动吉他演奏装置\拨片1+头_装配预览.STL"
SRC = r"E:\gt\自动吉他演奏装置\拨片1.STL"

# 洞中心（= 拨片末端外截面中心，洞实际中心偏移不大，用这个简化）
CY, CZ = 1.25, 2.50

# 插入柄（洞实测 0.67×2.50，双边各留 0.13mm 间隙）
SHANK_H = 0.42          # 柄厚（Y）
SHANK_W = 2.24          # 柄宽（Z）
SHANK_R = 0.08          # 柄截面圆角
SHANK_X0, SHANK_X1 = 18.0, 26.0   # 插入深度 8mm

# 限位肩（贴在拨片端面 x=26 上）
FLANGE_H = 4.60
FLANGE_W = 7.10
FLANGE_R = 1.20
FLANGE_X0, FLANGE_X1 = 26.0, 27.0

# 过渡锥（法兰 → 尖头根）
CONE_X0, CONE_X1 = 27.0, 27.5
CONE_H = 3.20
CONE_W = 5.00
CONE_R = 0.80

# 尖头
TIP_X = 28.5            # 尖点
TIP_H = 2.00
TIP_W = 3.00


tridx = []


def add(p0, p1, p2, target):
    ux, uy, uz = p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]
    vx, vy, vz = p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2]
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    if nx * target[0] + ny * target[1] + nz * target[2] < 0:
        p1, p2 = p2, p1
    tridx.append((tuple(p0), tuple(p1), tuple(p2)))


def ring2(cy, cz, h, w, r, n=64):
    """圆角矩形闭合环（逆时针，从 +X 方向看）。返回 [(y,z)...]"""
    a, b = h / 2.0, w / 2.0
    r = min(r, a - 1e-6, b - 1e-6)
    m = max(2, n // 8)
    seq = []
    for i in range(m + 1):
        seq.append((b, -(a - r) + 2 * (a - r) * i / m))
    for i in range(1, m):
        t = math.pi / 2 * i / m
        seq.append((b - r + r * math.cos(t), a - r + r * math.sin(t)))
    for i in range(m + 1):
        seq.append((b - r - 2 * (b - r) * i / m, a))
    for i in range(1, m):
        t = math.pi / 2 * i / m
        seq.append((-(b - r) - r * math.sin(t), a - r + r * math.cos(t)))
    for i in range(m + 1):
        seq.append((-b, a - r - 2 * (a - r) * i / m))
    for i in range(1, m):
        t = math.pi / 2 * i / m
        seq.append((-(b - r) - r * math.cos(t), -(a - r) - r * math.sin(t)))
    for i in range(m + 1):
        seq.append((-(b - r) + 2 * (b - r) * i / m, -a))
    for i in range(1, m):
        t = math.pi / 2 * i / m
        seq.append((b - r + r * math.sin(t), -(a - r) - r * math.cos(t)))
    return [(cy + y, cz + z) for z, y in seq]


def sweep_sections(keys, step):
    """keys: [(x, h, w, r), ...]，返回 [(x, ring)]"""
    secs = []
    for i in range(len(keys) - 1):
        x0, h0, w0, r0 = keys[i]
        x1, h1, w1, r1 = keys[i + 1]
        n = max(1, int(round((x1 - x0) / step)))
        for k in range(n):
            t = k / n
            x = x0 + (x1 - x0) * t
            h = h0 + (h1 - h0) * t
            w = w0 + (w1 - w0) * t
            r = r0 + (r1 - r0) * t
            secs.append((x, ring2(CY, CZ, h, w, r)))
    x, h, w, r = keys[-1]
    secs.append((x, ring2(CY, CZ, h, w, r)))
    return secs


def tube(secs):
    """用截面序列生成管状/实心侧面"""
    for i in range(len(secs) - 1):
        x0, r0 = secs[i]
        x1, r1 = secs[i + 1]
        n = len(r0)
        for j in range(n):
            j2 = (j + 1) % n
            p00 = (x0, r0[j][0], r0[j][1])
            p01 = (x0, r0[j2][0], r0[j2][1])
            p11 = (x1, r1[j2][0], r1[j2][1])
            p10 = (x1, r1[j][0], r1[j][1])
            mid = ((p00[1] + p11[1]) / 2, (p00[2] + p11[2]) / 2)
            tgt = (0.0, mid[0] - CY, mid[1] - CZ)
            add(p00, p01, p11, tgt)
            add(p00, p11, p10, tgt)


def cap_ring(x, ring, normal_dir, center=None):
    """实心截面封盖，边方向与 tube 截面边反向以闭合"""
    n = len(ring)
    c = center if center else (x, CY, CZ)
    for j in range(n):
        j2 = (j + 1) % n
        a = (x, ring[j][0], ring[j][1])
        b = (x, ring[j2][0], ring[j2][1])
        add(c, b, a, (normal_dir, 0.0, 0.0))


def cone_to_tip(secs, tip):
    """最后一个截面 ring 收束到尖点 tip"""
    x_last, r_last = secs[-1]
    n = len(r_last)
    for j in range(n):
        j2 = (j + 1) % n
        a = (x_last, r_last[j][0], r_last[j][1])
        b = (x_last, r_last[j2][0], r_last[j2][1])
        add(b, a, tip, (1.0, 0.0, 0.0))


# 把插头做成一个连续的 sweep：柄 → 法兰 → 锥 → 尖头根
# 其中柄到法兰用一个很短的锥形/圆弧过渡，避免突变面桥接问题
keys = [
    (SHANK_X0, SHANK_H, SHANK_W, SHANK_R),          # x=18，柄根部
    (SHANK_X1 - 0.5, SHANK_H, SHANK_W, SHANK_R),    # x=25.5，柄快到头
    (FLANGE_X0, FLANGE_H * 0.85, FLANGE_W * 0.85, FLANGE_R * 0.7),  # x=26，开始扩肩
    (FLANGE_X1, FLANGE_H, FLANGE_W, FLANGE_R),      # x=27，法兰外径
    (CONE_X1, CONE_H, CONE_W, CONE_R),              # x=27.5，锥根
]

secs = sweep_sections(keys, 0.25)

# 后端封盖（x=18）
cap_ring(SHANK_X0, secs[0][1], -1.0)

# 侧壁连续 sweep
tube(secs)

# 尖端
tip = (TIP_X, CY, CZ)
cone_to_tip(secs, tip)


# ---------- 配合检查：柄插进洞里 ----------
def load_stl_pts(path):
    with open(path, 'rb') as f:
        f.read(80)
        n = struct.unpack('<I', f.read(4))[0]
        data = f.read(n * 50)
    out = []
    for i in range(n):
        off = i * 50
        v = struct.unpack('<12f', data[off:off + 48])
        out += [v[0:3], v[3:6], v[6:9]]
    return out


def point_in_mesh(p, tris):
    """沿 +Z 方向射线判断点是否在网格内部（避开沿 X 方向的洞壁 grazing）"""
    x0, y0, z0 = p
    cnt = 0
    for a, b, c in tris:
        zs = [a[2], b[2], c[2]]
        if max(zs) <= z0:
            continue
        ax, ay, az = a
        e1x, e1y = b[0] - ax, b[1] - ay
        e2x, e2y = c[0] - ax, c[1] - ay
        det = e1x * e2y - e1y * e2x
        if abs(det) < 1e-9:
            continue
        rx, ry = x0 - ax, y0 - ay
        u = (rx * e2y - ry * e2x) / det
        v = (ry * e1x - rx * e1y) / det
        if u < -1e-6 or v < -1e-6 or u + v > 1 + 1e-6:
            continue
        t = az + u * (b[2] - az) + v * (c[2] - az) - z0
        if t > 1e-6:
            cnt += 1
    return cnt % 2 == 1


all_pts = load_stl_pts(SRC)
src_tris = [(all_pts[i], all_pts[i + 1], all_pts[i + 2]) for i in range(0, len(all_pts), 3)]

# 洞实测尺寸（从 _check_cavity.py axis=z 扫描）
HOLE_H = 0.67
HOLE_W = 2.50
# 几何配合：柄截面是否完全在洞截面内（含 0.05mm 安全 margin）
margin = 0.05
y_gap = (HOLE_H - SHANK_H) / 2 - margin
z_gap = (HOLE_W - SHANK_W) / 2 - margin
print(f"配合检查：洞 {HOLE_H:.2f}×{HOLE_W:.2f} / 柄 {SHANK_H:.2f}×{SHANK_W:.2f} "
      f"→ 双边间隙 Y={y_gap:.3f}mm, Z={z_gap:.3f}mm")
if y_gap > 0 and z_gap > 0:
    print("  ✅ 柄几何上可插入洞中")
else:
    print(f"  ❌ 间隙不足（Y={y_gap:.3f}, Z={z_gap:.3f}）")

# ---------- 输出 ----------
def write_stl(path, tri_list):
    with open(path, 'wb') as f:
        f.write(b'WorkBuddy pick-head generator'.ljust(80, b'\0'))
        f.write(struct.pack('<I', len(tri_list)))
        for p0, p1, p2 in tri_list:
            ux, uy, uz = p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]
            vx, vy, vz = p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2]
            nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
            L = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
            f.write(struct.pack('<12f', nx / L, ny / L, nz / L,
                                p0[0], p0[1], p0[2], p1[0], p1[1], p1[2], p2[0], p2[1], p2[2]))
            f.write(struct.pack('<H', 0))


write_stl(OUT1, tridx)
print(f"拨片头三角面 {len(tridx)}")

vol = 0.0
for p0, p1, p2 in tridx:
    vol += (p0[0] * (p1[1] * p2[2] - p2[1] * p1[2])
            - p0[1] * (p1[0] * p2[2] - p2[0] * p1[2])
            + p0[2] * (p1[0] * p2[1] - p2[0] * p1[1])) / 6.0
print(f"体积 {abs(vol)/1000:.3f} cm3   估算重量(尼龙1.15, 实心) {abs(vol)/1000*1.15:.2f} g")

from collections import defaultdict
edges = defaultdict(int)
for p0, p1, p2 in tridx:
    for a, b in ((p0, p1), (p1, p2), (p2, p0)):
        edges[(a, b)] += 1
bad = sum(1 for (a, b), c in edges.items() if edges.get((b, a), 0) != 1 or c != 1)
print(f"闭合性检查：非流形/未配对边 {bad} 条（0 = watertight）")

allp = [p for t in tridx for p in t]
print(f"包围盒 X {min(p[0] for p in allp):.2f}..{max(p[0] for p in allp):.2f} "
      f"Y {min(p[1] for p in allp):.2f}..{max(p[1] for p in allp):.2f} "
      f"Z {min(p[2] for p in allp):.2f}..{max(p[2] for p in allp):.2f}")

# 装配预览
def load_stl(path):
    with open(path, 'rb') as f:
        f.read(80)
        n = struct.unpack('<I', f.read(4))[0]
        data = f.read(n * 50)
    out = []
    for i in range(n):
        off = i * 50
        v = struct.unpack('<12f', data[off:off + 48])
        out.append((v[0:3], v[3:6], v[6:9]))
    return out

src = load_stl(SRC)
write_stl(OUT2, src + tridx)
print(f"装配预览三角面 {len(src)+len(tridx)}")
