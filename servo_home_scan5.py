#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""5 位置归位扫描：0/45/90/135/180，各停 10 秒。找拨片收平的角度。"""
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


plan = [("1", 0), ("2", 45), ("3", 90), ("4", 135), ("5", 180)]
print("=== HOME SCAN-5 START ===")
for tag, a in plan:
    ok = False
    for attempt in (1, 2):          # 失败就重试一次，避免临时拒收
        t = stamp()
        r = post("/api/servo/jog", {"chord_angle": a, "ensure_mode": True})
        ok = r.get("success", False) if isinstance(r, dict) else False
        print("[%s] >>> POS %s = %3d deg  ok=%s%s" % (t, tag, a, ok, "" if ok else "  (retry)"))
        if ok:
            break
        time.sleep(1.5)
    time.sleep(10)

post("/api/servo/jog", {"chord_angle": 90, "ensure_mode": True})
print("=== SCAN-5 DONE (停在 90) ===")
