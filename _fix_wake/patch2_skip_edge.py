#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""收尾：离线 TTS 成功后，跳过在线 edge-tts 尝试（省掉断网时的等待）。"""
import ast
import io
import time
import shutil

p = "/root/guitarist_app/src/guitarist/main.py"
shutil.copy2(p, p + ".bak_%s_wake2" % time.strftime("%Y%m%d_%H%M"))

s = io.open(p, encoding="utf-8").read()
a = '            try:\n                mp3_path = "/tmp/welcome.mp3"'
b = ('            try:\n'
     '                if audio is not None:\n'
     '                    raise RuntimeError("offline-tts-ok")  # 已有离线音频，跳过在线\n'
     '                mp3_path = "/tmp/welcome.mp3"')

n = s.count(a)
print("anchor:", n)
if n == 1:
    io.open(p, "w", encoding="utf-8").write(s.replace(a, b))
    print("patched")
else:
    print("skip（锚点不唯一）")
ast.parse(io.open(p, encoding="utf-8").read())
print("syntax ok")
