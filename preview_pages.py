#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""彩屏「剩余页面归位」离线预览：电源监控 / 确认框 / 调音器 / WiFi / 小结 … 渲成 PNG。

**不碰 SPI、不动琴**。
PC:
    <repo>/.venv/Scripts/python.exe preview_pages.py
板上:
    cd /root/guitarist_app && PREVIEW_OUT=/tmp python3 preview_pages.py
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


def render(name, fn, *args, **kw):
    fake = FakeDisp()
    r = LR.LCDRenderer(fonts, oled=fake)
    fn(r, *args, **kw)
    save_png(fake.buf, _out(name))
    print("  %-26s 帧数=%d" % (name, fake.frames), flush=True)
    return fake.frames


WIFI_INFO = {"ssid": "Guitar-5G", "ip_address": "192.168.1.7", "interface": "wlan0"}
WIFI_NETS = [
    {"ssid": "Guitar-5G", "signal": 88},
    {"ssid": "TP-LINK_2.4G", "signal": 64},
    {"ssid": "CMCC-Home", "signal": 51},
    {"ssid": "Xiaomi_AX3000", "signal": 37},
    {"ssid": "ChinaNet-8f2a", "signal": 22},
    {"ssid": "HUAWEI-B311", "signal": 15},
]
KEYBOARD = [list("ABCDE"), list("FGHIJ"), list("KLMNO"), list("PQRST"), list("UVWXY")]

if __name__ == "__main__":
    # 电源监控（电池图形）：
    #   ① 板上实测的真实读数（该路未接传感器 → 暗电池 + 星号）
    #   ② 1S 锂电正常读数（电池满格）   ③ 低电量（电池条变红见底）
    render("page_power_real.png", lambda r: r.show_power_monitor(0.21, 0.380, 0.113, -34.6))
    render("page_power_ok.png", lambda r: r.show_power_monitor(3.92, 0.850, 0.420, 33.2))
    render("page_power_low.png", lambda r: r.show_power_monitor(3.28, 0.310, 0.150, 41.5))
    render("page_confirm.png", lambda r: r.show_confirm_dialog("晴天", 0))
    render("page_confirm_no.png", lambda r: r.show_confirm_dialog("起风了", 1))
    render("page_voicedl.png", lambda r: r.show_voice_download_confirm("反方向的钟", 7.4))
    # 舵机归位 / 释放力矩 前的通用确认框（以前是 OLED 128×64 小字自绘）
    render("page_yesno_yes.png", lambda r: r.show_yesno_dialog(
        "舵机归位", "所有舵机回收缩位？", 0))
    render("page_yesno_no.png", lambda r: r.show_yesno_dialog(
        "释放力矩", "舵机将松力可手推？", 1))
    render("page_summary.png", lambda r: r.show_session_summary(
        {"title": "演奏小结", "lines": ["共弹 12 个和弦", "平均准确率 92%", "用时 1 分 18 秒"]},
        page_index=0, page_total=2, hint="UP/DOWN 翻页   ESC 退出"))
    render("page_tuner.png", lambda r: r.show_tuner("E2", "6弦", 7.5, False))
    render("page_tuner_ok.png", lambda r: r.show_tuner("A2", "5弦", 0.4, False))
    render("page_wifi_info.png", lambda r: r.show_wifi_info(WIFI_INFO, signal=88))
    render("page_wifi_none.png", lambda r: r.show_wifi_info(None))
    render("page_wifi_scan.png", lambda r: r.show_wifi_scan(WIFI_NETS, 2))
    render("page_scanning.png", lambda r: r.show_scanning(3))
    render("page_input.png", lambda r: r.show_input_ui(
        "Guitar", 1, 2, 0, "ALNUM", layout=KEYBOARD))

    names = ("page_power_real.png", "page_power_ok.png", "page_power_low.png",
             "page_confirm.png", "page_voicedl.png",
             "page_yesno_yes.png", "page_yesno_no.png",
             "page_summary.png", "page_tuner.png",
             "page_wifi_info.png", "page_wifi_scan.png",
             "page_scanning.png", "page_input.png")
    tiles = [Image.open(_out(n)) for n in names]
    cols = 3
    rows = (len(tiles) + cols - 1) // cols
    tw, th = tiles[0].width, tiles[0].height
    sheet = Image.new("RGB", (tw * cols + 12 * (cols + 1), th * rows + 12 * (rows + 1)),
                      (24, 24, 24))
    for i, t in enumerate(tiles):
        sheet.paste(t, (12 + (i % cols) * (tw + 12), 12 + (i // cols) * (th + 12)))
    sheet.save(_out("pages_sheet.png"))
    print("[preview] done -> %s/pages_sheet.png" % OUT_DIR, flush=True)
