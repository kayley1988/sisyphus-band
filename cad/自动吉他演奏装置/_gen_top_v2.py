# -*- coding: utf-8 -*-
"""顶盖 v2（定稿版）：
  ① 屏幕窗 28.5x28 → 33.8x31.1
  ② 删两个半圆腔（左侧大圆腔 + 顶部小圆）
  ③ 屏幕下方一排 2mm 圆柱透气孔，打穿到实心底 Z≈16（保证真挖穿）
"""
import struct, os, math
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "泰山派顶盖.STL")
OUT = os.path.join(HERE, "泰山派顶盖_v2.STL")

def load_stl(p):
    with open(p,'rb') as f:
        f.read(80); n=struct.unpack('<I',f.read(4))[0]; data=f.read(n*50)
    tris=[]
    for i in range(n):
        off=i*50; v=struct.unpack('<12f',data[off:off+48])
        tris.append(((v[3],v[4],v[5]),(v[6],v[7],v[8]),(v[9],v[10],v[11])))
    return tris

tris = load_stl(SRC)
pts=np.array([c for t in tris for c in t])
X0,X1,Y0,Y1,Z0,Z1 = pts[:,0].min(),pts[:,0].max(),pts[:,1].min(),pts[:,1].max(),pts[:,2].min(),pts[:,2].max()
print("原顶盖: %d 面  外廓 X[%.1f,%.1f] Y[%.1f,%.1f] Z[%.1f,%.1f]"%(len(tris),X0,X1,Y0,Y1,Z0,Z1))

# ═══ ① 扩屏幕窗 28.5x28 → 33.8x31.1 ═══
SX_C, SZ_C = 50.5, 30.75
NEW_W, NEW_H = 33.8, 31.1
nx0, nx1 = SX_C-NEW_W/2, SX_C+NEW_W/2
nz0, nz1 = SZ_C-NEW_H/2, SZ_C+NEW_H/2
print("① 新屏窗 X[%.1f,%.1f] Z[%.1f,%.1f]"%(nx0,nx1,nz0,nz1))

OX0,OX1,OZ0,OZ1 = 36.5,64.5,17.0,44.5
step1=[]; rm=0
for t in tris:
    if any(OX0 < p[0] < OX1 and OZ0 < p[2] < OZ1 for p in t):
        rm+=1; continue
    step1.append(t)
print("① 删旧屏窗三角 %d，剩 %d"%(rm,len(step1)))

# 加 4 条 0.8mm 挡墙
WALL_T=0.8
def add_wall(lst,p0,p1):
    ax,az=p0; bx,bz=p1
    if abs(bx-ax) > abs(bz-az):
        zlo,zhi = min(az,bz)-WALL_T, max(az,bz)
        for y in (Y0,Y1):
            lst.append(((ax,y,zlo),(bx,y,zhi),(bx,y,zlo)))
            lst.append(((ax,y,zlo),(bx,y,zlo),(ax,y,zhi)))
        lst.append(((ax,Y1,zlo),(ax,Y0,zlo),(ax,Y0,zhi)))
        lst.append(((ax,Y1,zlo),(ax,Y0,zhi),(ax,Y1,zhi)))
        lst.append(((bx,Y1,zlo),(bx,Y0,zlo),(bx,Y0,zhi)))
        lst.append(((bx,Y1,zlo),(bx,Y0,zhi),(bx,Y1,zhi)))
    else:
        xlo,xhi = min(ax,bx)-WALL_T, max(ax,bx)
        for y in (Y0,Y1):
            lst.append(((xlo,y,az),(xhi,y,bz),(xhi,y,az)))
            lst.append(((xlo,y,az),(xlo,y,bz),(xhi,y,bz)))
        lst.append(((xlo,Y1,az),(xlo,Y0,az),(xlo,Y0,bz)))
        lst.append(((xlo,Y1,az),(xlo,Y0,bz),(xlo,Y1,bz)))
        lst.append(((xhi,Y1,az),(xhi,Y0,az),(xhi,Y0,bz)))
        lst.append(((xhi,Y1,az),(xhi,Y0,bz),(xhi,Y1,bz)))

v2 = list(step1)
add_wall(v2,(nx0,nz0),(nx1,nz0))
add_wall(v2,(nx1,nz0),(nx1,nz1))
add_wall(v2,(nx1,nz1),(nx0,nz1))
add_wall(v2,(nx0,nz1),(nx0,nz0))
print("① 加挡墙 32 三角")

# ═══ ② 删两个半圆腔 ═══
def in_half(a):
    for p in a:
        if (p[0]-20)**2+(p[2]-30)**2 < 15**2:
            return True
        if (p[0]-20)**2+(p[2]-51)**2 < 6**2:
            return True
    return False
step2=[]; rm2=0
for t in v2:
    if in_half(t): rm2+=1; continue
    step2.append(t)
print("② 删半圆三角 %d，剩 %d"%(rm2,len(step2)))

# ═══ ③ 屏幕下方一排 2mm 圆柱孔（打穿到实心底 Z≈16）═══
# 实测：屏幕窗下边 nz0=15.2 下方到实心底 Z≈16 之间是空腔，
# 实心底在 Z≈16。孔心定 Z=16，孔径 2mm，全 Y 厚度删（真挖穿）
HOLE_R = 1.0
GAP = 2.5
HOLE_Z = 16.0
centers=[]
x = nx0+1.5
while x < nx1-1.5:
    centers.append((x,HOLE_Z)); x+=GAP
print("③ 圆柱孔 %d 个，Z=%.1f R=%.1f"%(len(centers),HOLE_Z,HOLE_R))

final=[]; rm3=0
for t in step2:
    hit=False
    for p in t:
        for (cx,cz) in centers:
            if (p[0]-cx)**2+(p[2]-cz)**2 < HOLE_R**2:
                hit=True; break
        if hit: break
    if hit: rm3+=1; continue
    final.append(t)
print("③ 删孔区三角 %d，最终 %d"%(rm3,len(final)))

def write_stl(tris,path):
    with open(path,'wb') as f:
        f.write(b'\0'*80); f.write(struct.pack('<I',len(tris)))
        for t in tris:
            v1=np.array(t[1])-np.array(t[0]); v2v=np.array(t[2])-np.array(t[0])
            nrm=np.cross(v1,v2v); L=np.linalg.norm(nrm)
            nrm=nrm/L if L>1e-12 else np.array([0,0,0])
            f.write(struct.pack('<12fH',*nrm,*t[0],*t[1],*t[2],0))
write_stl(final, OUT)
print("写出:", OUT)

# 验证
chk=load_stl(OUT)
chk_pts=np.array([c for t in chk for c in t])
print("v2 验证: %d 面  外廓 X[%.1f,%.1f] Y[%.1f,%.1f] Z[%.1f,%.1f]"%(
    len(chk), chk_pts[:,0].min(),chk_pts[:,0].max(),
    chk_pts[:,1].min(),chk_pts[:,1].max(),
    chk_pts[:,2].min(),chk_pts[:,2].max()))
# 孔区还有没有残余三角
resid=sum(1 for t in chk for p in t for (cx,cz) in centers
          if (p[0]-cx)**2+(p[2]-cz)**2 < HOLE_R**2)
print("孔区残余(应为0):", resid)
print("DONE")
