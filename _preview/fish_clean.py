# -*- coding: utf-8 -*-
"""只保留最大连通域（主鱼），裁紧边界，输出多档尺寸看 PNG 体积。"""
from PIL import Image, ImageDraw
import os
from collections import deque

OUT = r"E:\音乐\档3实现\_preview"
im = Image.open(os.path.join(OUT, "fish_cut_raw.png"))
W, H = im.size
alpha = im.split()[3]
a = alpha.load()

# ── 8-邻接连通域，保留最大者 ──
seen = [[False] * W for _ in range(H)]
best = []
for y0 in range(H):
    for x0 in range(W):
        if a[x0, y0] > 40 and not seen[y0][x0]:
            q = deque([(x0, y0)])
            seen[y0][x0] = True
            comp = []
            while q:
                x, y = q.popleft()
                comp.append((x, y))
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        nx, ny = x + dx, y + dy
                        if 0 <= nx < W and 0 <= ny < H and not seen[ny][nx] and a[nx, ny] > 40:
                            seen[ny][nx] = True
                            q.append((nx, ny))
            if len(comp) > len(best):
                best = comp
print("largest component px:", len(best))

mask = Image.new("L", (W, H), 0)
mp = mask.load()
for x, y in best:
    mp[x, y] = a[x, y]
im.putalpha(mask)

bbox = mask.getbbox()
print("bbox:", bbox)
tight = im.crop(bbox)
tw, th = tight.size
print("tight size:", tight.size)

tight.save(os.path.join(OUT, "fish_cut_tight.png"))

# 多档缩放看体积（目标：显示宽 ~150px，存 2x ≈ 300px）
for w in (300, 260):
    k = w / tw
    r = tight.resize((w, max(1, int(th * k))), Image.LANCZOS)
    p = os.path.join(OUT, f"fish_{w}.png")
    r.save(p, optimize=True)
    print(f"w={w}", r.size, os.path.getsize(p), "bytes")

# ── 预览：棋盘格 + 深蓝底 ──
def checker(w, h, n=14):
    bg = Image.new("RGB", (w, h), (185, 185, 185))
    dd = ImageDraw.Draw(bg)
    for yy in range(0, h, n):
        for xx in range(0, w, n):
            if (xx // n + yy // n) % 2 == 0:
                dd.rectangle([xx, yy, xx + n - 1, yy + n - 1], fill=(140, 140, 140))
    return bg

prev = checker(tw, th)
prev.paste(tight, (0, 0), tight)
prev.save(os.path.join(OUT, "fish_clean_check.png"))

deep = Image.new("RGB", (tw, th), (7, 16, 34))
deep.paste(tight, (0, 0), tight)
deep.save(os.path.join(OUT, "fish_clean_deep.png"))
print("done")
