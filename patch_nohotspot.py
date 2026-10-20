# -*- coding: utf-8 -*-
"""关闭 WiFi 热点（上台不需要建 AP，避免射频反复重置干扰系统）"""
import re, shutil

P = "/root/guitarist_app/config/config.yaml"
try:
    shutil.copy(P, P + ".bak_nohotspot")
except Exception as e:
    print("备份失败:", e)

s = open(P, encoding="utf-8").read()
lines = s.split("\n")
out, in_hs, found = [], False, False
for l in lines:
    if re.match(r"^hotspot:\s*$", l):
        in_hs = True
        out.append(l)
        continue
    if in_hs:
        if l.strip() == "" or l.startswith(" ") or l.startswith("\t"):
            if re.match(r"^\s+enabled:", l):
                out.append(re.sub(r"^\s+enabled:.*", "  enabled: false", l))
                found = True
                continue
        else:
            in_hs = False
    out.append(l)

res = "\n".join(out)
if not found:
    res = re.sub(r"(?m)^hotspot:\s*$", "hotspot:\n  enabled: false", res, count=1)

open(P, "w", encoding="utf-8").write(res)
print("hotspot.enabled 已改:", found, "-> 写入 false")
# 回读校验
i = res.find("\nhotspot:")
print("--- 校验 ---")
print("\n".join(res[i:i + 200].split("\n")[:8]))
