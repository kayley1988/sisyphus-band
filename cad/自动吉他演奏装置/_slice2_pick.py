# -*- coding: utf-8 -*-
"""对拨片1 沿长度方向做平面切片，精确求每处截面尺寸（用于设计配套拨片头）"""
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

def slice_x(tris, xc):
    """平面 x=xc 与网格相交，返回 (y,z) 点集"""
    out = []
    for a, b, c in tris:
        pts = [a, b, c]
        ins = []
        for i in range(3):
            p, q = pts[i], pts[(i + 1) % 3]
            if (p[0] - xc) * (q[0] - xc) < 0 or (abs(p[0] - xc) < 1e-9 and abs(q[0] - xc) > 1e-9):
                t = (xc - p[0]) / (q[0] - p[0])
                ins.append((p[1] + t * (q[1] - p[1]), p[2] + t * (q[2] - p[2])))
            elif abs(p[0] - xc) < 1e-9:
                ins.append((p[1], p[2]))
        # 去重
        for pt in ins:
            if not any(abs(pt[0] - o[0]) < 1e-6 and abs(pt[1] - o[1]) < 1e-6 for o in out):
                out.append(pt)
    return out

path = sys.argv[1]
tris = load_stl(path)
allp = [p for t in tris for p in t]
x0 = min(p[0] for p in allp); x1 = max(p[0] for p in allp)
print(f"{os.path.basename(path)}  X {x0:.2f}..{x1:.2f}")
print(f"{'x':>7} {'Ymin':>7}{'Ymax':>7} {'厚':>6} {'Zmin':>7}{'Zmax':>7} {'宽':>6} {'交点':>5}")
step = 0.5
x = x0 + 0.01
while x <= x1:
    s = slice_x(tris, x)
    if s:
        ys = [p[0] for p in s]; zs = [p[1] for p in s]
        print(f"{x:7.2f} {min(ys):7.2f}{max(ys):7.2f} {max(ys)-min(ys):6.2f} {min(zs):7.2f}{max(zs):7.2f} {max(zs)-min(zs):6.2f} {len(s):5d}")
    else:
        print(f"{x:7.2f} {'-':>7}{'-':>7} {'-':>6} {'-':>7}{'-':>7} {'-':>6} {0:5d}")
    x += step
