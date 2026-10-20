#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键归位（装机用）：解锁可能的 BUSY 状态 -> home(hold=True) -> 回读状态。
纯 ASCII 输出，板端运行。"""
import json
import time
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:5000"


def req(path, data=None, timeout=25):
    url = BASE + path
    body = None
    headers = {}
    if data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=body, headers=headers,
                               method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            return e.code, {"raw": "HTTP error"}
    except Exception as e:
        return -1, {"err": "%s: %s" % (type(e).__name__, str(e)[:80])}


def brief(d):
    if isinstance(d, dict):
        dd = d.get("data", d)
        if isinstance(dd, dict):
            return {k: dd[k] for k in ("msg", "chord_angle", "hold", "error")
                    if k in dd} or dd
        return dd
    return d


print("=== 1. 当前状态 ===")
code, st = req("/api/status")
print("status http=%s" % code)
if isinstance(st, dict):
    dd = st.get("data", st)
    if isinstance(dd, dict):
        print("state=%s running_function=%s" % (
            dd.get("current_state"), dd.get("running_function")))

print("=== 2. 若 BUSY 则先收尾 ===")
code, r = req("/api/songs/stop")
print("stop http=%s -> %s" % (code, brief(r)))
time.sleep(1.0)

print("=== 3. 一键归位 (hold=True) ===")
code, r = req("/api/servo/home", {})
print("home http=%s -> %s" % (code, brief(r)))

time.sleep(2.0)
print("=== 4. 归位后状态 ===")
code, r = req("/api/status")
if isinstance(r, dict):
    dd = r.get("data", r)
    if isinstance(dd, dict):
        print("state=%s running_function=%s chord=%s" % (
            dd.get("current_state"), dd.get("running_function"),
            dd.get("chord_angle")))
print("=== DONE ===")
