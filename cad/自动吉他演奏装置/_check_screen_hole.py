# -*- coding: utf-8 -*-
"""顶盖俯视分析：三层切片 + 扫描线填充 + 通孔连通域

用途：确定泰山派顶盖面板的现有开孔尺寸与最大可开孔空间。
"""
import struct

PATH = r"E:\gt\自动吉他演奏装置\泰山派顶盖.STL"
STEP = 0.5


def load_stl(path):
    tris = []
    with open(path, "rb") as f:
        f.read(80)
        n = struct.unpack("<I", f.read(4))[0]
        for _ in range(n):
            f.read(12)
            v = [struct.unpack("<3f", f.read(12)) for _ in range(3)]
            f.read(2)
            tris.append(v)
    return tris


def slice_segments(tris, yv):
    segs = []
    for t in tris:
        pts = []
        for i in range(3):
            a, b = t[i], t[(i + 1) % 3]
            if (a[1] - yv) * (b[1] - yv) < 0:
                f = (yv - a[1]) / (b[1] - a[1])
                pts.append((a[0] + f * (b[0] - a[0]), a[2] + f * (b[2] - a[2])))
        if len(pts) == 2:
            segs.append((pts[0], pts[1]))
    return segs


def fill_mask(segs, nx, nz, step):
    """扫描线（等奇偶）填充：每个 z 行求线段交点，成对填充。"""
    m = [[False] * nz for _ in range(nx)]
    for iz in range(nz):
        z = iz * step
        xs = []
        for (x0, z0), (x1, z1) in segs:
            if (z0 - z) * (z1 - z) < 0:
                f = (z - z0) / (z1 - z0)
                xs.append(x0 + f * (x1 - x0))
        xs.sort()
        for k in range(0, len(xs) - 1, 2):
            i0 = int(round(xs[k] / step))
            i1 = int(round(xs[k + 1] / step))
            for ix in range(max(0, i0), min(nx - 1, i1) + 1):
                m[ix][iz] = True
    return m


def main():
    tris = load_stl(PATH)
    nx, nz = int(74.5 / STEP) + 1, int(55.5 / STEP) + 1
    ys = [v[1] for t in tris for v in t]
    print("外廓 X74.5 Y(厚)%.1f Z55.5 mm" % (max(ys) - min(ys)))

    top = fill_mask(slice_segments(tris, 6.2), nx, nz, STEP)
    bot = fill_mask(slice_segments(tris, 0.3), nx, nz, STEP)

    # 俯视 ASCII：'#'=顶壁有材料  '='=只有底/侧壁  ' '=通透
    print("\n俯视图（每字符 1mm，X→右，Z↓，上=Z大）:")
    for iz in range(nz - 1, -1, -2):
        row = ""
        for ix in range(0, nx, 2):
            t = top[ix][iz] or (ix + 1 < nx and top[ix + 1][iz]) or (iz + 1 < nz and top[ix][iz + 1])
            b = bot[ix][iz] or (ix + 1 < nx and bot[ix + 1][iz]) or (iz + 1 < nz and bot[ix][iz + 1])
            row += "#" if t else ("=" if b else " ")
        print(row)

    # 顶面通透区连通域（= 顶壁上的开孔/窗口）
    seen = [[False] * nz for _ in range(nx)]
    comps = []
    for ix in range(nx):
        for iz in range(nz):
            if not top[ix][iz] and not seen[ix][iz]:
                stack = [(ix, iz)]
                seen[ix][iz] = True
                cells = []
                border = False
                while stack:
                    x, z = stack.pop()
                    cells.append((x, z))
                    if x == 0 or z == 0 or x == nx - 1 or z == nz - 1:
                        border = True
                    for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        ax, az = x + dx, z + dz
                        if 0 <= ax < nx and 0 <= az < nz and not top[ax][az] and not seen[ax][az]:
                            seen[ax][az] = True
                            stack.append((ax, az))
                if not border and len(cells) >= 4:
                    xs = [c[0] for c in cells]
                    zs = [c[1] for c in cells]
                    comps.append((len(cells), min(xs) * STEP, max(xs) * STEP,
                                  min(zs) * STEP, max(zs) * STEP))
    comps.sort(key=lambda c: -c[0])
    print("\n顶壁上的开孔（连通域，按面积降序）:")
    print("  %-14s %-24s %s" % ("尺寸(mm)", "X范围", "Z范围"))
    for n, x0, x1, z0, z1 in comps[:10]:
        print("  %5.1f x %5.1f  X[%5.1f,%5.1f]  Z[%5.1f,%5.1f]  (%d 格)"
              % (x1 - x0 + STEP, z1 - z0 + STEP, x0, x1, z0, z1, n))


if __name__ == "__main__":
    main()
