# -*- coding: utf-8 -*-
"""分切片透视拨片1：沿长度方向每 1mm 输出截面(Y厚 × Z宽)轮廓，用于设计配套拨片头"""
import struct, sys, os

def load_stl(path):
    with open(path, 'rb') as f:
        f.read(80)
        n = struct.unpack('<I', f.read(4))[0]
        data = f.read(n * 50)
    tris = []
    for i in range(n):
        off = i * 50
        v = struct.unpack('<12f', data[off:off + 48])
        tris.append((v[3:6], v[6:9], v[9:12]))
    return tris

path = sys.argv[1]
step = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
tris = load_stl(path)
pts = []
for a, b, c in tris:
    pts += [a, b, c]
xs = [p[0] for p in pts]; ys = [p[1] for p in pts]; zs = [p[2] for p in pts]
x0, x1 = min(xs), max(xs)
y0, y1 = min(ys), max(ys)
z0, z1 = min(zs), max(zs)
print(f"{os.path.basename(path)}: X {x0:.2f}..{x1:.2f}  Y {y0:.2f}..{y1:.2f}  Z {z0:.2f}..{z1:.2f}")

print(f"\n{'x区间':>14} | {'Y范围':>16} | {'Z范围':>16} | 点数 | 截面图(Z宽→, Y厚↑)")
nb = int((x1 - x0) / step) + 1
for k in range(nb):
    a = x0 + k * step
    b = a + step
    sel = [p for p in pts if a - 1e-6 <= p[0] < b + 1e-6]
    if not sel:
        print(f"{a:6.1f}..{b:5.1f} | {'-':>16} | {'-':>16} |  0   |")
        continue
    sy = [p[1] for p in sel]; sz = [p[2] for p in sel]
    # 栅格: 列=Z(宽), 行=Y(厚)
    W, H = 16, 8
    grid = [[' '] * W for _ in range(H)]
    for p in sel:
        cz = int((p[2] - z0) / (z1 - z0 or 1) * (W - 1))
        cy = int((p[1] - y0) / (y1 - y0 or 1) * (H - 1))
        grid[H - 1 - cy][cz] = '#'
    row0 = ''.join(grid[0])
    # 只打印首尾行与包围盒数字，节省篇幅
    print(f"{a:6.1f}..{b:5.1f} | {min(sy):6.2f}..{max(sy):6.2f} | {min(sz):6.2f}..{max(sz):6.2f} | {len(sel):4d} |")
    for r in grid:
        print(f"{'':>14} | {'':>16} | {'':>16} |      | {''.join(r)}")
