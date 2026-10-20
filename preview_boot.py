#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""开机启动图离线预览：把「金鱼宝宝-to C + 金鱼」全屏启动图渲成 PNG，**不碰真实 SPI、不动琴**。

板上用法（root）：
    cd /root/guitarist_app && python3 preview_boot.py
    adb pull /tmp/boot_*.png .

PC 用法（Windows Git Bash）：
    <repo>/.venv/Scripts/python.exe preview_boot.py

产物：/tmp/boot_<n>.png（240×240 → 放大 2 倍存图，方便看版式）
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# 源码位置：优先主仓库（PC 开发），其次板上的 /root/guitarist_app/src/guitarist。
# ⚠️ 必须显式判断"这个目录里有没有 song_library.py"——本脚本旁边那份
#    lcd_renderer.py 是早先留下的旧副本，误用它会拿到没有新启动图的旧渲染器。
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
    """板端用 NotoSansCJK，PC 上退到系统里的中文字体只为看版式。"""
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


print("[preview] hardware = %s" % _stub_hardware_if_needed(), flush=True)

import lcd_renderer as LR  # noqa: E402

if CJK:
    LR.LCDRenderer._CJK = CJK          # 只影响本次进程，不写回仓库

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


def save_png(buf, path, scale=2):
    a = np.frombuffer(buf, dtype=">u2").reshape(240, 240)
    r = (((a >> 11) & 0x1F).astype(np.uint16) * 255 // 31).astype(np.uint8)
    g = (((a >> 5) & 0x3F).astype(np.uint16) * 255 // 63).astype(np.uint8)
    b = ((a & 0x1F).astype(np.uint16) * 255 // 31).astype(np.uint8)
    im = Image.fromarray(np.dstack([r, g, b]))
    if scale != 1:
        im = im.resize((240 * scale, 240 * scale), Image.NEAREST)
    im.save(path)


def render(prog, status, frame, detail="", name="boot.png"):
    fake = FakeDisp()
    r = LR.LCDRenderer(fonts, oled=fake)
    r.show_boot_screen(progress=prog, status_text=status, boot_frame=frame,
                       loading_detail=detail)
    save_png(fake.buf, _out(name))
    return fake.buf


fonts = None
if __name__ == "__main__" or True:
    print("[preview] CJK =", CJK, flush=True)
    if not CJK:
        print("[preview] 找不到中文字体，版式预览不可信", flush=True)
        sys.exit(1)
    fonts = {
        "font": ImageFont.truetype(CJK, 16),
        "font_small": ImageFont.truetype(CJK, 12),
        "font_tiny": ImageFont.truetype(CJK, 10),
    }

    # ① 开机动到第 2 步（进度 45%）
    render(0.45, "加载AI模型...", 9, name="boot_step.png")
    # ② 进度满 + 「启动中，请稍候...」（开机动画之后那段真实初始化的等待屏）
    render(1.0, "启动中，请稍候...", 16, name="boot_wait.png")
    # ③ 尾摆的另一相位（看动画摆动幅度）
    render(1.0, "启动中，请稍候...", 31, name="boot_wait2.png")
    # ④ 带 AI 子阶段小字
    render(0.72, "启动语音引擎...", 22, detail="检查 API Key...",
           name="boot_detail.png")
    # ⑤ 拼一张 2×2 接触表，方便一眼看全
    tiles = [Image.open(_out(n)) for n in
             ("boot_step.png", "boot_wait.png", "boot_wait2.png", "boot_detail.png")]
    sheet = Image.new("RGB", (tiles[0].width * 2 + 12, tiles[0].height * 2 + 12),
                      (24, 24, 24))
    for i, t in enumerate(tiles):
        sheet.paste(t, ((i % 2) * (t.width + 12), (i // 2) * (t.height + 12)))
    sheet.save(_out("boot_sheet.png"))

    print("[preview] done → %s/{boot_step,boot_wait,boot_wait2,boot_detail,boot_sheet}.png"
          % OUT_DIR, flush=True)
