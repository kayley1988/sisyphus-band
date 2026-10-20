# -*- coding: utf-8 -*-
"""扫描装配件几何：找舵机安装位 / 齿条导向槽的深度分层，判断三种齿条用在哪。"""
import struct, os
import numpy as np

BASE = os.path.dirname(os.path.abspath(__file__))


def load_stl(path):
    with open(path, 'rb') as f:
        f.read(80)
        n = struct.unpack('<I', f.read(4))[0]
        raw = f.read(n * 50)
    data = np.frombuffer(raw, dtype=np.uint8).reshape(n, 50)
    v = data[:, 12:48].copy().view('<f4').reshape(n, 3, 3)
    return v.astype(np.float64)


def scan(name, axis=None, bins=30):
    p = os.path.join(BASE, name)
    if not os.path.exists(p):
        print(f"[missing] {name}")
        return
    v = load_stl(p)
    pts = v.reshape(-1, 3)
    lo, hi = pts.min(0), pts.max(0)
    span = hi - lo
    if axis is None:
        axis = int(np.argmax(span))
    print("=" * 80)
    print(f"{name}  三角面={len(v)}  bbox x[{lo[0]:.1f},{hi[0]:.1f}] "
          f"y[{lo[1]:.1f},{hi[1]:.1f}] z[{lo[2]:.1f},{hi[2]:.1f}]")
    print(f"  主轴(最长) = {'xyz'[axis]}  跨度 {span[axis]:.1f}mm  "
          f"(三轴跨度 x={span[0]:.1f} y={span[1]:.1f} z={span[2]:.1f})")
    other = [i for i in range(3) if i != axis]
    edges = np.linspace(lo[axis], hi[axis], bins + 1)
    maxcnt = 1
    rows = []
    for i in range(bins):
        a, b = edges[i], edges[i + 1]
        m = (pts[:, axis] >= a - 1e-6) & (pts[:, axis] <= b + 1e-6)
        cnt = int(m.sum())
        maxcnt = max(maxcnt, cnt)
        if cnt:
            sub = pts[m]
            w = sub[:, other[0]].max() - sub[:, other[0]].min()
            h = sub[:, other[1]].max() - sub[:, other[1]].min()
        else:
            w = h = 0.0
        rows.append((a, b, cnt, w, h))
    print(f"  {'区间':>18} {'顶点':>6} {'宽':>7} {'高':>7}  柱")
    for a, b, cnt, w, h in rows:
        bar = '#' * int(round(cnt / maxcnt * 46))
        print(f"  {a:7.1f}~{b:7.1f} {cnt:6d} {w:7.1f} {h:7.1f}  {bar}")
    print()


for n in ["和弦舵机支架总装.STL", "和弦支架底座.STL", "舵机固定架.STL"]:
    scan(n)
