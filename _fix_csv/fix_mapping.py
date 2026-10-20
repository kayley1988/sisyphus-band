# -*- coding: utf-8 -*-
"""把 servo_config.csv 的两行通道映射改成与实装(=固件 XIAOZHI_PROFILE)一致。
依据：18 路逐片实测（2026-10-11 21:22）。
  chord_channel_mapping: idx5 PCA2 -> PCA7 ; idx11 PCA1 -> PCA8
  strum_channel_mapping: PCA4,PCA6,PCA8,PCA3,PCA5,PCA7 -> PCA2,PCA4,PCA6,PCA1,PCA3,PCA5
"""
import io, os, sys

SRC = r"E:/音乐/档3实现/_fix_csv/servo_config.csv"
DST = r"E:/音乐/档3实现/_fix_csv/servo_config_fixed.csv"

NEW_CHORD = "chord_channel_mapping,PWM0,PCA15,PCA13,PCA11,PCA9,PCA7,PWM1,PCA16,PCA14,PCA12,PCA10,PCA8"
NEW_STRUM = "strum_channel_mapping,PCA2,PCA4,PCA6,PCA1,PCA3,PCA5"

with io.open(SRC, "r", encoding="utf-8", newline="") as f:
    raw = f.read()

# 保留原换行风格
nl = "\r\n" if "\r\n" in raw else "\n"
lines = raw.split(nl)

out = []
changed = []
for ln in lines:
    if ln.startswith("chord_channel_mapping"):
        if ln != NEW_CHORD:
            changed.append(("chord", ln, NEW_CHORD))
        out.append(NEW_CHORD)
    elif ln.startswith("strum_channel_mapping"):
        if ln != NEW_STRUM:
            changed.append(("strum", ln, NEW_STRUM))
        out.append(NEW_STRUM)
    else:
        out.append(ln)

new_raw = nl.join(out)
with io.open(DST, "w", encoding="utf-8", newline="") as f:
    f.write(new_raw)

print("换行:", repr(nl), "| 改动条数:", len(changed))
for tag, a, b in changed:
    print("  [%s]\n    - %s\n    + %s" % (tag, a, b))
print("写出:", DST, os.path.getsize(DST), "bytes")
