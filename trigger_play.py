#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""trigger_play.py — 从板子内部调管理页的演奏接口（验证「点歌即弹」这条路）。

板内 localhost 调用，绕开 adb 传中文参数的编码坑，也避开系统代理。
用法: python3 trigger_play.py [歌名]      默认《稻香》
"""
import json
import sys
import time
import urllib.request

song = sys.argv[1] if len(sys.argv) > 1 else "稻香"
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 强制直连

BASE = "http://127.0.0.1:5000"


def call(path, payload=None):
    if payload is None:
        req = urllib.request.Request(BASE + path, method="GET")
    else:
        req = urllib.request.Request(
            BASE + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST")
    with opener.open(req, timeout=20) as r:
        return r.status, r.read().decode("utf-8")


print("=== 健康检查 ===")
try:
    print(call("/api/health"))
except Exception as e:
    print("health FAIL:", e)

print("=== 发起演奏: %s ===" % song)
try:
    print(call("/api/songs/play", {"song": song}))
except Exception as e:
    print("play FAIL:", e)

for i in range(5):
    time.sleep(3)
    try:
        print(call("/api/songs/playing"))
    except Exception as e:
        print("playing FAIL:", e)
