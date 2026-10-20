#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""彩屏「设置菜单」离线预览：渲 240×240 菜单屏 PNG，**不碰 SPI、不动琴**。

PC:
    <repo>/.venv/Scripts/python.exe preview_menu.py
板上:
    cd /root/guitarist_app && PREVIEW_OUT=/tmp python3 preview_menu.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("GUITAR_REPO", "E:/音乐/拨云_开源参考/liguanxi_Automatic_guitar")
_CANDS = [
    os.path.join(REPO, "src", "guitarist"),
    os.path.join(HERE, "..", "src", "guitarist"),
    os.path.join(HERE, "src", "guitarist"),
    HERE,
]
SRC = None
for cand in _CANDS:
    if os.path.isfile(os.path.join(cand, "song_library.py")):
        SRC = os.path.abspath(cand)
        break
if SRC is None:
    print("找不到含 song_library.py 的源码目录: %s" % _CANDS, flush=True)
    sys.exit(2)
sys.path.insert(0, SRC)
os.environ.setdefault("GUITAR_USE_LCD", "1")

import numpy as np                      # noqa: E402
from PIL import Image, ImageFont        # noqa: E402


def _pick_cjk():
    for p in ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
              "C:/Windows/Fonts/msyh.ttc",
              "C:/Windows/Fonts/simhei.ttf",
              "C:/Windows/Fonts/simsun.ttc"):
        if os.path.exists(p):
            return p
    return None


CJK = _pick_cjk()


def _stub_hardware_if_needed():
    try:
        import hardware  # noqa: F401
        return "real"
    except Exception:
        pass
    import types
    import threading
    m = types.ModuleType("hardware")
    m.i2c_lock = threading.Lock()
    m.fonts = None
    m.USE_LCD = True
    m.up_button = m.down_button = m.set_button = m.esc_button = None
    sys.modules["hardware"] = m
    return "stub"


print("[preview] CJK = %s | hardware = %s" % (CJK, _stub_hardware_if_needed()), flush=True)
if not CJK:
    print("[preview] 找不到中文字体，版式预览不可信", flush=True)
    sys.exit(1)

import lcd_renderer as LR                                    # noqa: E402
from menu_system import MENU_ITEMS                           # noqa: E402

LR.LCDRenderer._CJK = CJK

OUT_DIR = os.environ.get("PREVIEW_OUT",
                         "/tmp" if os.name != "nt" else os.path.join(HERE, "_preview"))
os.makedirs(OUT_DIR, exist_ok=True)


def _out(name):
    return os.path.join(OUT_DIR, name)


class FakeDisp:
    w = h = 240

    def __init__(self):
        self.buf = None

    def write_frame(self, b):
        self.buf = bytes(b)

    def fill_rgb565(self, rgb):
        pass

    def init(self):
        pass


def save_png(buf, path, scale=2):
    a = np.frombuffer(buf, dtype=">u2").reshape(240, 240)
    r = (((a >> 11) & 0x1F).astype(np.uint16) * 255 // 31).astype(np.uint8)
    g = (((a >> 5) & 0x3F).astype(np.uint16) * 255 // 63).astype(np.uint8)
    b = ((a & 0x1F).astype(np.uint16) * 255 // 31).astype(np.uint8)
    im = Image.fromarray(np.dstack([r, g, b]))
    if scale != 1:
        im = im.resize((240 * scale, 240 * scale), Image.NEAREST)
    im.save(path)


fonts = {
    "font": ImageFont.truetype(CJK, 16),
    "font_small": ImageFont.truetype(CJK, 12),
    "font_tiny": ImageFont.truetype(CJK, 10),
}


def render(name, menu_key, sel):
    menu = MENU_ITEMS[menu_key]
    fake = FakeDisp()
    r = LR.LCDRenderer(fonts, oled=fake)
    r.show_menu(menu["items"], sel, title=menu.get("title"))
    save_png(fake.buf, _out(name))


if __name__ == "__main__":
    root_items = MENU_ITEMS["root"]["items"]
    # root 共 10 项；"舵机调整" 在第 7 项（index 6）
    render("menu_root_servo.png", "root", 6)
    render("menu_root_top.png", "root", 0)
    render("menu_root_bottom.png", "root", len(root_items) - 1)
    render("menu_servo_sub.png", "servo_calibration", 0)

    tiles = [Image.open(_out(n)) for n in
             ("menu_root_servo.png", "menu_root_top.png",
              "menu_root_bottom.png", "menu_servo_sub.png")]
    sheet = Image.new("RGB", (tiles[0].width * 2 + 12, tiles[0].height * 2 + 12),
                      (24, 24, 24))
    for i, t in enumerate(tiles):
        sheet.paste(t, ((i % 2) * (t.width + 12), (i // 2) * (t.height + 12)))
    sheet.save(_out("menu_sheet.png"))
    print("[preview] done -> %s/menu_{root_servo,root_top,root_bottom,servo_sub,sheet}.png"
          % OUT_DIR, flush=True)
