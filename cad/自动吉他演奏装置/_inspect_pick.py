# -*- coding: utf-8 -*-
"""透视《拨云》拨片1.STL 的几何形状：输出包围盒 + 三个投影面的 ASCII 轮廓图"""
import struct, sys, glob, os

def load_stl(path):
    with open(path, 'rb') as f:
        head = f.read(80)
        n = struct.unpack('<I', f.read(4))[0]
        data = f.read(n * 50)
    tris = []
    for i in range(n):
        off = i * 50
        v = struct.unpack('<12f', data[off:off + 48])
        tris.append((v[3:6], v[6:9], v[9:12]))
    return tris

def ascii_plot(pts, ax_h, ax_v, w=76, h=24, title=""):
    xs = [p[ax_h] for p in pts]
    ys = [p[ax_v] for p in pts]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    dx = (x1 - x0) or 1e-9
    dy = (y1 - y0) or 1e-9
    grid = [[' '] * w for _ in range(h)]
    for x, y in zip(xs, ys):
        cx = int((x - x0) / dx * (w - 1))
        cy = int((y - y0) / dy * (h - 1))
        grid[h - 1 - cy][cx] = '#'
    print(f"\n--- {title}  横轴 {x0:.2f}..{x1:.2f}mm  纵轴 {y0:.2f}..{y1:.2f}mm ---")
    for row in grid:
        print('|' + ''.join(row) + '|')

for path in sys.argv[1:]:
    tris = load_stl(path)
    pts = []
    for a, b, c in tris:
        pts += [a, b, c]
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]; zs = [p[2] for p in pts]
    print(f"\n===== {os.path.basename(path)} =====")
    print(f"三角面 {len(tris)}")
    print(f"X {min(xs):.2f}..{max(xs):.2f}  (长 {max(xs)-min(xs):.2f})")
    print(f"Y {min(ys):.2f}..{max(ys):.2f}  (厚 {max(ys)-min(ys):.2f})")
    print(f"Z {min(zs):.2f}..{max(zs):.2f}  (宽 {max(zs)-min(zs):.2f})")
    ascii_plot(pts, 0, 1, title="侧视 X(长) × Y(厚)")
    ascii_plot(pts, 0, 2, title="俯视 X(长) × Z(宽)")
    ascii_plot(pts, 2, 1, title="端面 Z(宽) × Y(厚)")
