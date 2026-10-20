# -*- coding: utf-8 -*-
"""同步修正本地仓库里两份 servo_config 的通道映射（与板上实装 / 固件 XIAOZHI 表一致）。"""
import io, os

NEW_CHORD = "chord_channel_mapping,PWM0,PCA15,PCA13,PCA11,PCA9,PCA7,PWM1,PCA16,PCA14,PCA12,PCA10,PCA8"
NEW_STRUM = "strum_channel_mapping,PCA2,PCA4,PCA6,PCA1,PCA3,PCA5"

BASE = r"E:/音乐/拨云_开源参考/liguanxi_Automatic_guitar/config"
TARGETS = ["servo_config.csv", "servo_config.example.csv"]

for name in TARGETS:
    p = os.path.join(BASE, name)
    if not os.path.exists(p):
        print("SKIP(不存在)", p)
        continue
    with io.open(p, "r", encoding="utf-8", newline="") as f:
        raw = f.read()
    nl = "\r\n" if "\r\n" in raw else "\n"
    out, hits = [], 0
    for ln in raw.split(nl):
        if ln.startswith("chord_channel_mapping"):
            hits += 1; out.append(NEW_CHORD)
        elif ln.startswith("strum_channel_mapping"):
            hits += 1; out.append(NEW_STRUM)
        else:
            out.append(ln)
    with io.open(p, "w", encoding="utf-8", newline="") as f:
        f.write(nl.join(out))
    print("已改", name, "替换行数=", hits)
