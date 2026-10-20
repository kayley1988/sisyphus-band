# -*- coding: utf-8 -*-
"""重新精确测量拨片1 末端内腔（洞）尺寸。"""
import struct

def load_tris(path):
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

def point_in_mesh(p, tris, axis='z'):
    """沿指定轴正方向射线判断点是否在网格内部"""
    x0, y0, z0 = p
    cnt = 0
    for a, b, c in tris:
        ax, ay, az = a
        bx, by, bz = b
        cx, cy, cz = c
        if axis == 'z':
            # 射线沿 +Z：r(t) = (x0, y0, z0+t), t>0
            # 解 a + u(b-a) + v(c-a) = (x0, y0, z0+t)
            e1x, e1y = bx - ax, by - ay
            e2x, e2y = cx - ax, cy - ay
            det = e1x * e2y - e1y * e2x
            if abs(det) < 1e-9:
                continue
            rx, ry = x0 - ax, y0 - ay
            u = (rx * e2y - ry * e2x) / det
            v = (ry * e1x - rx * e1y) / det
            if u < -1e-6 or v < -1e-6 or u + v > 1 + 1e-6:
                continue
            t = az + u * (bz - az) + v * (cz - az) - z0
        elif axis == 'y':
            e1x, e1z = bx - ax, bz - az
            e2x, e2z = cx - ax, cz - az
            det = e1x * e2z - e1z * e2x
            if abs(det) < 1e-9:
                continue
            rx, rz = x0 - ax, z0 - az
            u = (rx * e2z - rz * e2x) / det
            v = (rz * e1x - rx * e1z) / det
            if u < -1e-6 or v < -1e-6 or u + v > 1 + 1e-6:
                continue
            t = ay + u * (by - ay) + v * (cy - ay) - y0
        else:  # axis == 'x'
            e1y, e1z = by - ay, bz - az
            e2y, e2z = cy - ay, cz - az
            det = e1y * e2z - e1z * e2y
            if abs(det) < 1e-9:
                continue
            ry, rz = y0 - ay, z0 - az
            u = (ry * e2z - rz * e2y) / det
            v = (rz * e1y - ry * e1z) / det
            if u < -1e-6 or v < -1e-6 or u + v > 1 + 1e-6:
                continue
            t = ax + u * (bx - ax) + v * (cx - ax) - x0
        if t > 1e-6:
            cnt += 1
    return cnt % 2 == 1

def scan_section(path, x, ys=100, zs=100, axis='z'):
    tris = load_tris(path)
    # 找包围盒
    pts = [p for tri in tris for p in tri if abs(p[0] - x) < 2.0]
    ymin, ymax = min(p[1] for p in pts), max(p[1] for p in pts)
    zmin, zmax = min(p[2] for p in pts), max(p[2] for p in pts)
    grid = [[False] * zs for _ in range(ys)]
    for iy in range(ys):
        y = ymin + (ymax - ymin) * (iy + 0.5) / ys
        for iz in range(zs):
            z = zmin + (zmax - zmin) * (iz + 0.5) / zs
            grid[iy][iz] = point_in_mesh((x, y, z), tris, axis)
    return grid, ymin, ymax, zmin, zmax

def measure_hole(grid, ymin, ymax, zmin, zmax):
    ys, zs = len(grid), len(grid[0])
    # 对每个 y 行，找内部点连续段
    # 洞 = 被内部点包围的非内部区域
    # 简化：找非内部点区域，且该区域被内部点完全包围
    # 这里先用阈值法：统计严格内部为空/接近空的行列
    hole_ys = set()
    hole_zs = set()
    for iy in range(ys):
        for iz in range(zs):
            if not grid[iy][iz]:
                # 检查这个空点是否被内部点包围（上下左右）
                up = iy > 0 and grid[iy-1][iz]
                dn = iy < ys-1 and grid[iy+1][iz]
                lf = iz > 0 and grid[iy][iz-1]
                rt = iz < zs-1 and grid[iy][iz+1]
                if (up or dn) and (lf or rt):
                    hole_ys.add(iy)
                    hole_zs.add(iz)
    if not hole_ys:
        return None
    dy = (max(hole_ys) - min(hole_ys) + 1) / ys * (ymax - ymin)
    dz = (max(hole_zs) - min(hole_zs) + 1) / zs * (zmax - zmin)
    cy = ymin + (sum(hole_ys) / len(hole_ys) + 0.5) / ys * (ymax - ymin)
    cz = zmin + (sum(hole_zs) / len(hole_zs) + 0.5) / zs * (zmax - zmin)
    return dy, dz, cy, cz

path = r"E:\gt\自动吉他演奏装置\拨片1.STL"
for x in [18.5, 25.5]:
    print(f"\n=== x={x} ===")
    grid, ymin, ymax, zmin, zmax = scan_section(path, x, 60, 60, 'z')
    m = measure_hole(grid, ymin, ymax, zmin, zmax)
    if m:
        dy, dz, cy, cz = m
        print(f"  洞约 {dy:.2f}(Y) × {dz:.2f}(Z), 中心 Y={cy:.2f}, Z={cz:.2f}")
    print("  剖面（#=内部 .=外部，Y 行从上到下）：")
    for iy in range(len(grid)):
        s = ''.join('#' if grid[iy][iz] else '.' for iz in range(len(grid[0])))
        if '#' in s:
            print(f"    {s}")
