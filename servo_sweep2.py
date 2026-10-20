#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""和弦舵机宽范围慢扫（找"齿条缩回"那个角度）。
每步 6 秒，共 6 步 = 36 秒。纯 ASCII 输出。"""
import json
import time
import urllib.request

BASE = "http://127.0.0.1:5000"


def post(path, data=None):
    body = json.dumps(data).encode() if data is not None else None
    hdr = {"Content-Type": "application/json"} if body else {}
    r = urllib.request.Request(BASE + path, data=body, headers=hdr,
                               method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(r, timeout=25) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as e:
        return {"err": str(e)[:90]}


print("=== SWEEP START (chord) ===")
steps = [90, 120, 60, 120, 60, 90]
for i, a in enumerate(steps):
    t = time.strftime("%H:%M:%S")
    r = post("/api/servo/jog", {"chord_angle": a, "ensure_mode": True})
    ok = r.get("success", False) if isinstance(r, dict) else False
    print("[%s] step%d  chord -> %3d   ok=%s" % (t, i + 1, a, ok))
    time.sleep(6)
print("=== SWEEP DONE (停在 90) ===")
