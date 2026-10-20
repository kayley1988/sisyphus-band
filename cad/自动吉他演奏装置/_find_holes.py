import struct, numpy as np
from collections import deque

def load(p):
    with open(p,'rb') as f:
        f.read(80); n=struct.unpack('<I',f.read(4))[0]; data=f.read(n*50)
    tris=[]
    for i in range(n):
        off=i*50; v=struct.unpack('<12f',data[off:off+48])
        tris.append(((v[3],v[4],v[5]),(v[6],v[7],v[8]),(v[9],v[10],v[11])))
    return tris

def slice_mat(tris, yv, step=0.25, ext=(0,74.5,0,55.5)):
    x0,x1,z0,z1=ext
    nx=int((x1-x0)/step)+1; nz=int((z1-z0)/step)+1
    m=np.zeros((nx,nz),bool)
    segs=[]
    for t in tris:
        for i in range(3):
            p1,p2=t[i],t[(i+1)%3]
            if (p1[1]-yv)*(p2[1]-yv)>=0: continue
            f=(yv-p1[1])/(p2[1]-p1[1])
            segs.append(((p1[0]+f*(p2[0]-p1[0]), p1[2]+f*(p2[2]-p1[2])),(p2[0],p2[2])))
    for iz in range(nz):
        z=z0+iz*step; xs=[]
        for (ax,az),(bx,bz) in segs:
            if (az-z)*(bz-z)<0:
                f=(z-az)/(bz-az); xs.append(ax+f*(bx-ax))
        xs.sort()
        for k in range(0,len(xs)-1,2):
            i0=max(0,int((xs[k]-x0)/step)); i1=min(nx-1,int((xs[k+1]-x0)/step))
            if i1>=i0: m[i0:i1+1, iz]=True
    return m, step, x0, z0

def cc_holes(mat, step, x0, z0, min_area_mm2=20.0):
    """找被材料包围的内部空腔。mat[ix,iz]=True 表示有材料。"""
    empty = ~mat
    nx,nz = empty.shape          # empty[ix,iz]：ix 是 X 方向(nx)，iz 是 Z 方向(nz)
    seen = np.zeros_like(empty, bool)
    holes=[]
    for iz in range(nz):
        for ix in range(nx):
            if not empty[ix,iz] or seen[ix,iz]:
                continue
            q=deque([(ix,iz)]); seen[ix,iz]=True; cells=[]
            touch_border=False
            while q:
                x,z=q.popleft(); cells.append((x,z))
                if x==0 or z==0 or x==nx-1 or z==nz-1:
                    touch_border=True
                for dx,dz in ((1,0),(-1,0),(0,1),(0,-1)):
                    ax,az=x+dx,z+dz
                    if ax<0 or az<0 or ax>=nx or az>=nz:
                        continue
                    if empty[ax,az] and not seen[ax,az]:
                        seen[ax,az]=True; q.append((ax,az))
            if touch_border:
                continue
            area=len(cells)*step*step
            if area < min_area_mm2:
                continue
            xs=[c[0] for c in cells]; zs=[c[1] for c in cells]
            xa=x0+min(xs)*step; xb=x0+(max(xs)+1)*step
            za=z0+min(zs)*step; zb=z0+(max(zs)+1)*step
            holes.append((area, xa, xb, za, zb))
    holes.sort(key=lambda h:-h[0])
    return holes

if __name__=="__main__":
    import sys
    tr = load("泰山派顶盖.STL")
    mat, step, x0, z0 = slice_mat(tr, 3.0)
    holes = cc_holes(mat, step, x0, z0, min_area_mm2=20.0)
    print("=== 原顶盖 Y=3 截面 内部空腔（面积>20mm²，按大小排）===")
    for i,(area,xa,xb,za,zb) in enumerate(holes[:18]):
        cx=(xa+xb)/2; cz=(za+zb)/2
        print("  腔%02d: 中心(X=%.1f,Z=%.1f)  范围 X[%.1f,%.1f] Z[%.1f,%.1f]  面积≈%.0fmm²  尺寸≈%.1f x %.1f"%(
            i,cx,cz,xa,xb,za,zb,area,xb-xa,zb-za))
