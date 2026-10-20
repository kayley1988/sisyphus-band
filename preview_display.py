#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""彩屏「提示条 / 主界面兜底」离线预览：渲成 PNG，**不碰 SPI、不动琴**。

PC:
    <repo>/.venv/Scripts/python.exe preview_display.py
板上:
    cd /root/guitarist_app && PREVIEW_OUT=/tmp python3 preview_display.py
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
    if os.path.isfile(os.path.join(cand, "lcd_renderer.py")):
        SRC = os.path.abspath(cand)
        break
if SRC is None:
    print("找不到含 lcd_renderer.py 的源码目录: %s" % _CANDS, flush=True)
    sys.exit(2)
sys.path.insert(0, SRC)
os.environ.setdefault("GUITAR_USE_LCD", "1")


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
    import threading
    import types
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

import numpy as np                                      # noqa: E402
from PIL import Image, ImageFont                        # noqa: E402

import lcd_renderer as LR                               # noqa: E402

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
        self.frames = 0

    def write_frame(self, b):
        self.buf = bytes(b)
        self.frames += 1

    def fill_rgb565(self, rgb):
        pass

    def init(self):
        pass

    def reassert(self):
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


def render(name, fn, *args):
    fake = FakeDisp()
    r = LR.LCDRenderer(fonts, oled=fake)
    fn(r, *args)
    save_png(fake.buf, _out(name))
    print("  %-24s 帧数=%d" % (name, fake.frames), flush=True)
    return fake.frames


MAIN_SPEAKING = {
    "battery_v": 12.1, "temperature": 33.2, "wifi_signal": "80%",
    "ai_state_raw": "SPEAKING", "ai_user_text": "给我弹一首晴天吧",
    "ai_reply_text": "好的，马上开始弹奏《晴天》给你听",
    "device_ip": "192.168.1.7", "her_chord": "C 和弦", "her_hint": "可以拨了",
}
MAIN_IDLE = {
    "battery_v": 11.8, "temperature": 30.5, "wifi_signal": "62%",
    "ai_state_raw": "IDLE", "device_ip": "", "ap_mode": True,
    "hotspot_ssid": "Guitar-Config", "her_chord": "", "her_hint": "",
}

if __name__ == "__main__":
    n1 = render("disp_msg_toggle.png", lambda r: r.show_message(
        ["已切换：人机配合", "长按 SET 设置"]))
    n2 = render("disp_msg_connect.png", lambda r: r.show_message(["正在连接WiFi..."]))
    n3 = render("disp_main_speaking.png", lambda r: r.show_main_screen(MAIN_SPEAKING))
    n4 = render("disp_main_idle.png", lambda r: r.show_main_screen(MAIN_IDLE))
    print("[preview] 每个页面推屏帧数（都应为 1）: %s" % [n1, n2, n3, n4], flush=True)

    names = ("disp_msg_toggle.png", "disp_msg_connect.png",
             "disp_main_speaking.png", "disp_main_idle.png")
    tiles = [Image.open(_out(n)) for n in names]
    sheet = Image.new("RGB", (tiles[0].width * 2 + 12, tiles[0].height * 2 + 12),
                      (24, 24, 24))
    for i, t in enumerate(tiles):
        sheet.paste(t, ((i % 2) * (t.width + 12), (i // 2) * (t.height + 12)))
    sheet.save(_out("disp_sheet.png"))
    print("[preview] done -> %s/disp_{msg_toggle,msg_connect,main_speaking,main_idle,sheet}.png"
          % OUT_DIR, flush=True)
