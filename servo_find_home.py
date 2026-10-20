#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""和弦舵机分档慢扫 —— 找出"齿轮缩回去"的角度。
每档停 4.5 秒，共 9 档 = 约 41 秒。纯 ASCII 输出。"""
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


# 0) 先解开可能卡住的 BUSY 状态
r = post("/api/songs/stop")
print("[%s] unlock (songs/stop) -> %s" % (stamp(), str(r)[:110]))

# 1) 分档慢扫：30 -> 150，步进 15 度
steps = [30, 45, 60, 75, 90, 105, 120, 135, 150]
print("=== FIND-HOME SWEEP START (chord) ===")
print("idx  angle   (watch the gear: which one is RETRACTED?)")
for i, a in enumerate(steps):
    t = stamp()
    r = post("/api/servo/jog", {"chord_angle": a, "ensure_mode": True})
    ok = r.get("success", False) if isinstance(r, dict) else False
    print("[%s] step %d/9  chord -> %3d deg   ok=%s" % (t, i + 1, a, ok))
    time.sleep(4.5)

# 2) 收尾：先停在一个中间位，避免突兀
post("/api/servo/jog", {"chord_angle": 90, "ensure_mode": True})
print("=== SWEEP DONE (停在 90) ===")
