#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""和弦舵机全量程慢扫（0~180 度），找"齿轮缩回去"的角度。
每档停 6 秒，共 10 档 = 约 60 秒。纯 ASCII 输出。"""
import json
import time
import urllib.request

BASE = "http://127.0.0.1:5000"


def post(path, data=None, timeout=25):
    body = json.dumps(data).encode() if data is not None else None
    hdr = {"Content-Type": "application/json"} if body else {}
    r = urllib.request.Request(BASE + path, data=body, headers=hdr,
                               method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as e:
        return {"err": "%s %s" % (type(e).__name__, str(e)[:70])}


def stamp():
    return time.strftime("%H:%M:%S")


steps = [0, 20, 40, 60, 80, 100, 120, 140, 160, 180]
print("=== FULL-RANGE SWEEP START (chord 0-180) ===")
print("idx  angle   (which angle = gear RETRACTED / not sticking out?)")
for i, a in enumerate(steps):
    t = stamp()
    r = post("/api/servo/jog", {"chord_angle": a, "ensure_mode": True})
    ok = r.get("success", False) if isinstance(r, dict) else False
    print("[%s] step %d/10  chord -> %3d deg   ok=%s" % (t, i + 1, a, ok))
    time.sleep(6)

post("/api/servo/jog", {"chord_angle": 90, "ensure_mode": True})
print("=== SWEEP DONE (停在 90) ===")
