# -*- coding: utf-8 -*-
"""从用户参考图（油画鱼缸）裁出右上那条吐泡泡的金鱼，出候选裁剪预览。"""
from PIL import Image
import os

SRC = r"C:\Users\zhouqian\.workbuddy\clipboard-images\clipboard-2026-10-11T04-57-03-907Z-dbd3f611.jpg"
OUT = r"E:\音乐\档3实现\_preview"
os.makedirs(OUT, exist_ok=True)

img = Image.open(SRC).convert("RGB")
W, H = img.size
print("source size:", W, H)

# 候选裁剪（比例坐标 l,t,r,b）——目标：右上金鱼 + 鱼嘴左上的泡泡串
crops = {
    "fish_wide":  (0.38, 0.22, 0.95, 0.68),   # 宽框：含全部泡泡串
    "fish_tight": (0.46, 0.27, 0.93, 0.66),   # 紧框
    "fish_body":  (0.52, 0.30, 0.95, 0.64),   # 只鱼身（不含泡泡）
}
for name, (l, t, r, b) in crops.items():
    box = (int(l * W), int(t * H), int(r * W), int(b * H))
    c = img.crop(box)
    c.save(os.path.join(OUT, f"fish_{name}.png"))
    print(name, box, c.size)
