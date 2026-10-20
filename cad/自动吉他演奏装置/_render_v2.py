# -*- coding: utf-8 -*-
"""渲染 v2 顶盖俯视预览（Y=0.3 底面切片，扫描线填充；深灰=有材料、白=孔）"""
import struct, os
import numpy as np
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))

def load(p):
    with open(p,'rb') as f:
        f.read(80); n=struct.unpack('<I',f.read(4))[0]; data=f.read(n*50)
    tris=[]
    for i in range(n):
        off=i*50; v=struct.unpack('<12f',data[off:off+48])
        tris.append((v[3:6],v[6:9],v[9:12]))
    return tris

def slice_xz(tris, yv, step=0.25):
    nx=int(74.5/step)+1; nz=int(55.5/step)+1
    segs=[]
    for t in tris:
        for i in range(3):
            p1,p2=t[i],t[(i+1)%3]
            if (p1[1]-yv)*(p2[1]-yv)>=0: continue
            f=(yv-p1[1])/(p2[1]-p1[1])
            segs.append(((p1[0]+f*(p2[0]-p1[0]), p1[2]+f*(p2[2]-p1[2])),(p2[0],p2[2])))
    m=np.zeros((nx,nz),bool)
    for iz in range(nz):
        z=iz*step; xs=[]
        for (x0,z0),(x1,z1) in segs:
            if (z0-z)*(z1-z)<0:
                f=(z-z0)/(z1-z0); xs.append(x0+f*(x1-x0))
        xs.sort()
        for k in range(0,len(xs)-1,2):
            i0=max(0,int(xs[k]/step)); i1=min(nx-1,int(xs[k+1]/step))
            m[i0:i1+1, iz]=True
    return m, step

def render(m, step, out, scale=8):
    nx,nz=m.shape
    img=Image.new("RGB",(nx*scale,nz*scale),"white"); d=ImageDraw.Draw(img)
    for iz in range(nz):
        for ix in range(nx):
            if m[ix,iz]:
                d.rectangle([ix*scale,iz*scale,(ix+1)*scale-1,(iz+1)*scale-1],fill=(40,40,40))
    img.save(out)
    print("→", out, "(%dx%d px)"%(nx*scale,nz*scale))

mo,ste=slice_xz(load(os.path.join(HERE,"泰山派顶盖.STL")),0.3)
render(mo,ste,os.path.join(HERE,"_preview_v2_orig.png"))
mv,_=slice_xz(load(os.path.join(HERE,"泰山派顶盖_v2.STL")),0.3)
render(mv,ste,os.path.join(HERE,"_preview_v2_new.png"))

# 蜂窝区验证
mbot,_=slice_xz(load(os.path.join(HERE,"泰山派顶盖_v2.STL")),0.3)
step=ste
hx0=int(33/step);hx1=int(67/step);hz0=int(14/step);hz1=int(18/step)
print("\n蜂窝区(X33-67 Z14-18) 底面材料格数:", int(mbot[hx0:hx1,hz0:hz1].sum()), "(≈0=真挖穿了)")

# 蜂窝区验证
mbot=slice_xz(load(os.path.join(HERE,"泰山派顶盖_v2.STL")),0.3)
step=0.25
hz=int(14/step); hz1=int(18/step); hx0=int(33/step); hx1=int(67/step)
print("\n蜂窝区(X33-67 Z14-18) 底面材料格数:", int(mbot[hz:hz1, hx0:hx1].sum()), "(≈0=真挖穿了)")
print("DONE")
