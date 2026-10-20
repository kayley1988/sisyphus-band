# -*- coding: utf-8 -*-
"""分析齿条1/2/3.STL 与齿轮.STL 的几何，判断齿条长度用途与压弦头位置。"""
import struct, sys, os
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

def analyze(name):
    v = load_stl(os.path.join(BASE, name))
    pts = v.reshape(-1, 3)
    lo, hi = pts.min(0), pts.max(0)
    print("=" * 78)
    print(f"{name}   三角面={len(v)}   包围盒 x[{lo[0]:.1f},{hi[0]:.1f}] "
          f"y[{lo[1]:.1f},{hi[1]:.1f}] z[{lo[2]:.1f},{hi[2]:.1f}]")
    # 主轴 = 最长边
    span = hi - lo
    axis = int(np.argmax(span))
    print(f"  主轴 = {'xyz'[axis]}  长度 {span[axis]:.1f}mm")

    # 沿主轴分 20 段：统计顶点数 + 垂直方向的截面范围
    N = 24
    edges = np.linspace(lo[axis], hi[axis], N + 1)
    other = [i for i in range(3) if i != axis]
    print(f"  {'段':>3} {'主轴区间':>18} {'顶点数':>6} {'中宽':>7} {'高':>7}  柱")
    maxcnt = 0
    rows = []
    for i in range(N):
        a, b = edges[i], edges[i + 1]
        m = (pts[:, axis] >= a - 1e-6) & (pts[:, axis] <= b + 1e-6)
        cnt = int(m.sum())
        maxcnt = max(maxcnt, cnt)
        if cnt == 0:
            rows.append((a, b, 0, 0.0, 0.0))
            continue
        sub = pts[m]
        w = sub[:, other[0]].max() - sub[:, other[0]].min()
        h = sub[:, other[1]].max() - sub[:, other[1]].min()
        rows.append((a, b, cnt, w, h))
    for a, b, cnt, w, h in rows:
        bar = '#' * int(round(cnt / max(maxcnt, 1) * 40))
        print(f"  {a:7.1f}~{b:7.1f} {cnt:6d} {w:7.2f} {h:7.2f}  {bar}")
    print()

for n in ["齿条1.STL", "齿条2.STL", "齿条3.STL", "齿轮.STL"]:
    analyze(n)
