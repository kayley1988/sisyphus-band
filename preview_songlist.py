#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""选歌屏离线预览：把彩屏选歌界面渲成 PNG，**不碰真实 SPI、不动琴**。

板上用法（root）：
    cd /root/guitarist_app && python3 preview_songlist.py
    adb pull /tmp/songlist_0.png .

PC 用法（Windows Git Bash）：
    python preview_songlist.py          # 自动挑 Windows 中文字体代替 NotoSansCJK

产物：/tmp/songlist_<n>.png（240×240 → 放大 2 倍存图，方便看版式）
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# 源码位置：优先主仓库（PC 开发），其次板上的 /root/guitarist_app/src/guitarist。
# ⚠️ 必须显式判断"这个目录里有没有 song_library.py"——本脚本旁边那份
#    lcd_renderer.py 是早先留下的旧副本，误用它会拿到没有选歌屏的旧渲染器。
REPO = os.environ.get("GUITAR_REPO", "E:/音乐/拨云_开源参考/liguanxi_Automatic_guitar")
_CANDS = [
    os.path.join(REPO, "src", "guitarist"),   # PC 主仓库
    os.path.join(HERE, "..", "src", "guitarist"),
    os.path.join(HERE, "src", "guitarist"),   # 板上 /root/guitarist_app
    HERE,
]
SRC = None
for cand in _CANDS:
    if os.path.isfile(os.path.join(cand, "song_library.py")):
        SRC = os.path.abspath(cand)
        break
if SRC is None:
    print("找不到含 song_library.py 的源码目录，试过: %s" % _CANDS, flush=True)
    sys.exit(2)
sys.path.insert(0, SRC)
os.environ.setdefault("GUITAR_USE_LCD", "1")

import numpy as np
from PIL import Image, ImageFont


def _pick_cjk():
    """W 改用 NotoSansCJK（板端），PC 上退到系统里的中文字体只为看版式。"""
    for p in ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
              "C:/Windows/Fonts/msyh.ttc",
              "C:/Windows/Fonts/simhei.ttf",
              "C:/Windows/Fonts/simsun.ttc"):
        if os.path.exists(p):
            return p
    return None


CJK = _pick_cjk()


def _stub_hardware_if_needed():
    """PC 上没有 blinka（digitalio/board），而 oled_renderer 只用 hardware.i2c_lock。

    真板子上会直接导入成功，这里什么都不做；PC 上注入一个最小桩，
    让"只画图不发 SPI"的版式预览能跑起来。
    """
    try:
        import hardware  # noqa: F401
        return "real"
    except Exception as e:
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


print("[preview] hardware = %s" % _stub_hardware_if_needed(), flush=True)

import lcd_renderer as LR
import song_library as SL

if CJK:
    LR.LCDRenderer._CJK = CJK          # 只影响本次进程，不写回仓库

#: 输出目录：板上走 /tmp（adb pull 方便），PC 上落到脚本旁的 _preview/
OUT_DIR = os.environ.get(
    "PREVIEW_OUT",
    "/tmp" if os.name != "nt" else os.path.join(HERE, "_preview"))
os.makedirs(OUT_DIR, exist_ok=True)


def _out(name):
    return os.path.join(OUT_DIR, name)


class FakeDisp:
    """只收帧不发送 —— 让 LCDRenderer 以为在真屏上画。"""
    w = h = 240

    def __init__(self):
        self.buf = None

    def write_frame(self, b):
        self.buf = bytes(b)


def save_png(buf, path):
    a = np.frombuffer(buf, dtype=">u2").reshape(240, 240)
    r = (((a >> 11) & 0x1F).astype(np.uint16) * 255 // 31).astype(np.uint8)
    g = (((a >> 5) & 0x3F).astype(np.uint16) * 255 // 63).astype(np.uint8)
    b = ((a & 0x1F).astype(np.uint16) * 255 // 31).astype(np.uint8)
    Image.fromarray(np.dstack([r, g, b])).resize((480, 480), Image.NEAREST).save(path)


def main():
    print("[preview] CJK =", CJK, flush=True)
    if not CJK:
        print("[preview] 找不到中文字体，版式预览不可信", flush=True)
        return 1

    fonts = {
        "font": ImageFont.truetype(CJK, 16),
        "font_small": ImageFont.truetype(CJK, 12),
        "font_tiny": ImageFont.truetype(CJK, 10),
    }

    real = SL.scan_songs()
    songs = real["songs"]
    print("[preview] 曲库根 = %s" % real.get("root"), flush=True)
    print("[preview] 可弹 %d 首: %s" % (len(songs), [s["name"] for s in songs]), flush=True)
    print("[preview] 跳过 %d 首: %s"
          % (len(real["skipped"]), [s["name"] for s in real["skipped"]]), flush=True)

    fake = FakeDisp()
    r = LR.LCDRenderer(fonts, oled=fake)

    # ① 真实曲库 + 第二项选中（验证高亮/滚动）
    r.show_song_list(songs, min(1, max(0, len(songs) - 1)))
    save_png(fake.buf, _out("songlist_real.png"))

    # ② 造 12 首假歌名，验证长列表滚动 + 长歌名截断
    fake2 = FakeDisp()
    r2 = LR.LCDRenderer(fonts, oled=fake2)
    demo = ["稻香", "晴天（原版G调吉他谱）", "真的爱你", "夜空中最亮的星",
            "南方姑娘", "鼓楼", "少年锦时", "去大理", "活着", "理想",
            "一生何求", "千千阙歌"]
    r2.show_song_list([{"name": n} for n in demo], 6)
    save_png(fake2.buf, _out("songlist_scroll.png"))

    # ③ 空曲库
    fake3 = FakeDisp()
    r3 = LR.LCDRenderer(fonts, oled=fake3)
    r3.show_song_list([], 0)
    save_png(fake3.buf, _out("songlist_empty.png"))

    print("[preview] done → %s/{songlist_real,songlist_scroll,songlist_empty}.png" % OUT_DIR,
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
