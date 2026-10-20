#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""三位置对比：60 / 90 / 120，各停 12 秒。找哪个角度拨片收得最平。"""
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


plan = [("A", 60), ("B", 90), ("C", 120)]
print("=== HOME A/B/C TEST START ===")
for tag, a in plan:
    t = stamp()
    r = post("/api/servo/jog", {"chord_angle": a, "ensure_mode": True})
    ok = r.get("success", False) if isinstance(r, dict) else False
    print("[%s] >>> POSITION %s = %3d deg   ok=%s   (hold 12s)" % (t, tag, a, ok))
    time.sleep(12)

post("/api/servo/jog", {"chord_angle": 90, "ensure_mode": True})
print("=== A/B/C DONE (停在 B=90) ===")
