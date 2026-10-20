# -*- coding: utf-8 -*-
"""解析《拨云》STL 图纸：输出每件包围盒尺寸(mm)与体积(cm³)、估算打印用量"""
import os, glob, json
import trimesh

DIR = r"E:\gt\自动吉他演奏装置"
out = []
for f in sorted(glob.glob(os.path.join(DIR, "*.STL"))):
    name = os.path.basename(f)
    try:
        m = trimesh.load(f, force="mesh")
        ext = m.extents  # x,y,z mm
        vol = abs(m.volume) / 1000.0  # cm3
        watertight = m.is_watertight
        # 10% 填充 + 4 层墙近似：实际耗材 ≈ 壳体体积。粗估 = vol*0.35
        est_g = vol * 1.20 * 0.35  # PETG 密度 1.27，取1.20保守
        out.append({
            "name": name, "x": round(ext[0],1), "y": round(ext[1],1), "z": round(ext[2],1),
            "vol_cm3": round(vol,1), "est_g": round(est_g,1), "wt": watertight,
            "tris": len(m.faces)
        })
    except Exception as e:
        out.append({"name": name, "err": str(e)})

print(f"{'文件':<38}{'X':>7}{'Y':>7}{'Z':>7}{'cm3':>8}{'估g':>7}  闭合 三角面")
tot_g = 0
for r in out:
    if "err" in r:
        print(f"{r['name']:<38} ERROR: {r['err']}")
        continue
    tot_g += r["est_g"]
    print(f"{r['name']:<38}{r['x']:>7}{r['y']:>7}{r['z']:>7}{r['vol_cm3']:>8}{r['est_g']:>7}  {'是' if r['wt'] else '否':>4} {r['tris']}")
print(f"\n总估耗材: {tot_g:.0f} g (PETG, 10%填充近似, 不含支撑)")
json.dump(out, open(os.path.join(DIR, "_stl_sizes.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
