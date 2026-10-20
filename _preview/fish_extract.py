# -*- coding: utf-8 -*-
"""抠出主金鱼 → 透明背景 PNG。HSV 橙色分割 + 闭运算填孔 + 边缘羽化。"""
from PIL import Image, ImageFilter, ImageDraw
import os

SRC = r"C:\Users\zhouqian\.workbuddy\clipboard-images\clipboard-2026-10-11T04-57-03-907Z-dbd3f611.jpg"
OUT = r"E:\音乐\档3实现\_preview"

img = Image.open(SRC).convert("RGB")
# 裁剪：只取右上主鱼（避开左下第二条鱼；泡泡串不要，代码画动态泡泡）
BOX = (655, 340, 1210, 845)
crop = img.crop(BOX)
W, H = crop.size
print("crop:", crop.size)

hsv = crop.convert("HSV")
Hc, S, V = hsv.split()
ph, ps, pv = Hc.load(), S.load(), V.load()

mask = Image.new("L", (W, H), 0)
mp = mask.load()
for y in range(H):
    for x in range(W):
        h = ph[x, y] * 360.0 / 255.0
        s = ps[x, y] / 255.0
        v = pv[x, y] / 255.0
        # 橙红色系（鱼身/鳍/轮廓深棕都在此范围）；背景是蓝绿水（hue 130~260）
        if (h <= 62 or h >= 325) and s > 0.28 and v > 0.15:
            mp[x, y] = 255

# 闭运算：填鱼眼/鳞片高光的孔洞，连拢鳍尖
mask = mask.filter(ImageFilter.MaxFilter(11)).filter(ImageFilter.MinFilter(11))
mask = mask.filter(ImageFilter.MaxFilter(7)).filter(ImageFilter.MinFilter(7))

# 左下角保护性清零（第二条鱼头部残留）
d = ImageDraw.Draw(mask)
d.rectangle([0, int(H * 0.60), int(W * 0.17), H], fill=0)

# 边缘羽化（缩到显示尺寸后边缘更柔）
mask = mask.filter(ImageFilter.GaussianBlur(1.4))

rgba = crop.convert("RGBA")
rgba.putalpha(mask)
rgba.save(os.path.join(OUT, "fish_cut_raw.png"))

# 棋盘格底预览（检验透明边缘）
def checker(w, h, n=14):
    bg = Image.new("RGB", (w, h), (185, 185, 185))
    dd = ImageDraw.Draw(bg)
    for yy in range(0, h, n):
        for xx in range(0, w, n):
            if (xx // n + yy // n) % 2 == 0:
                dd.rectangle([xx, yy, xx + n - 1, yy + n - 1], fill=(140, 140, 140))
    return bg

prev = checker(W, H)
prev.paste(rgba, (0, 0), rgba)
prev.save(os.path.join(OUT, "fish_cut_check.png"))

# 深蓝底预览（模拟开机图底色，看最终观感）
deep = Image.new("RGB", (W, H), (7, 16, 34))
deep.paste(rgba, (0, 0), rgba)
deep.save(os.path.join(OUT, "fish_cut_deep.png"))
print("done, alpha coverage:",
      sum(1 for y in range(H) for x in range(W) if mp[x, y] > 128) / (W * H))
