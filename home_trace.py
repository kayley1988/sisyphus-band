#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""归位追踪：记录 jsonl 增量，发 home，然后只打印新增的 ESP32 行。
用来绕开"日志丢了 1351 行"的不确定性 —— 我们看这一次归位期间 ESP32 到底发了什么。"""
import glob
import json
import os
import time
import urllib.request

BASE = "http://127.0.0.1:5000"
JP = "/root/guitarist_app/logs/system"


def latest():
    fs = sorted(glob.glob(os.path.join(JP, "*.jsonl")))
    return fs[-1] if fs else None


def size(f):
    try:
        return os.path.getsize(f)
    except OSError:
        return 0


def post(path, data=None):
    body = json.dumps(data).encode() if data is not None else None
    hdr = {"Content-Type": "application/json"} if body else {}
    r = urllib.request.Request(BASE + path, data=body, headers=hdr,
                               method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(r, timeout=25) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as e:
        return -1, {"err": str(e)[:100]}


f = latest()
off = size(f)
print("jsonl=%s  start_off=%d" % (os.path.basename(f), off))

print("--- send home ---")
code, r = post("/api/servo/home", {"hold": True})
print("home http=%s -> %s" % (code, json.dumps(r, ensure_ascii=False)[:200]))

time.sleep(8)

print("--- new ESP32 lines ---")
n = 0
with open(f, "rb") as fh:
    fh.seek(off)
    for raw in fh.read().split(b"\n"):
        raw = raw.strip()
        if not raw:
            continue
        try:
            d = json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            continue
        m = (d.get("message") or "")
        s = d.get("source", "")
        if s == "esp32" and ("servo_driver" in m or "servo_cmd" in m
                             or "SERVO" in m or "DISSEQ" in m or "home" in m):
            n += 1
            print(d.get("ts_utc", "")[11:19], m[:160])
print("matched=%d" % n)
print("=== DONE ===")
