#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LCD 渲染器模块（1.83寸 ST7789B 彩屏，替换 1.3寸 SSD1306 OLED）
================================================================
一期换屏专用。结构对齐现有 oled_renderer.py 的 OLEDRenderer，
但画布从 128×64 单色 改为 240×284 65K色，核心三块：
  · draw_chord_card()   当前和弦：6 弦品位矩阵（哪弦按哪品）
  · draw_progress()     进度条 + 当前小节/拍
  · draw_status_bar()   顶部状态条（歌名 / 电池 / 模式）

依赖（板上 pip 装）：
  · adafruit-circuitpython-st77xx   （驱动 ST7789B）
  · Pillow（已有）

40PIN 接线（见 37_放大屏一期）：
  CS=脚23(GPIO0_A0)  MOSI=脚19(GPIO0_A1)  SCK=脚21(GPIO0_B0)  DC=脚24(GPIO0_A1)
  VCC=脚2(5V)  3V3=脚1/17  GND=脚6/9  背光=脚2(5V) 常亮
"""

from PIL import Image, ImageDraw
import threading

# ──────────── 1.83寸 ST7789B 画布常量 ────────────
WIDTH, HEIGHT = 240, 284        # 竖屏（rotation=90 后的实际显示逻辑尺寸）
# 颜色（16bit 65K）
BG        = (24, 26, 34)       # 深蓝底（商务风）
FG        = (235, 238, 245)    # 浅灰白字
CHORD_RED = (232, 64, 64)      # 和弦按点
OPEN_GRAY = (150, 150, 160)    # 空弦/不按
PROG_BLUE = (64, 140, 232)     # 进度条
PROG_BG   = (50, 55, 70)       # 进度条底
MODE_GRN  = (90, 200, 120)     # her 模式绿点


class LcdRenderer:
    """1.83寸彩屏渲染器。接口对齐 OLEDRenderer，主循环 display_dirty 逻辑可照抄。"""

    def __init__(self, st7789, fonts: dict):
        self.st7789 = st7789
        self._lock = threading.Lock()
        # 彩色屏字体：思源黑体 12~16pt（字号比 OLED 大 3 倍）
        self.font       = fonts.get("font")        # ~16pt 标题
        self.font_small = fonts.get("font_small")  # ~12pt 正文
        self.font_tiny  = fonts.get("font_tiny")   # ~9pt  角标
        self.width, self.height = WIDTH, HEIGHT

    # ──────────── 基座（对齐 OLEDRenderer）────────────
    def clear(self):
        self.st7789.fill(BG)

    def new_image(self):
        return Image.new("RGB", (self.width, self.height), BG)

    def show(self, img):
        """线程安全推帧"""
        try:
            with self._lock:
                self.st7789.image(img)
                self.st7789.show()
        except OSError:
            pass  # SPI 暂时不可用，静默跳过等下一帧

    # ──────────── ① 当前和弦卡：6 弦品位矩阵 ────────────
    def draw_chord_card(self, img, chord_name: str, frets):
        """
        frets: 6 元素列表，从 1 弦到 6 弦，-1=空弦，n=第 n 品。
        例：C 大调 = [0, -1, 2, 1, 0, -1]（1弦E按0=空弦... 实际按标准指法）。
        在屏幕上半部画 6 条竖线(弦) + 品位圆点。
        """
        draw = ImageDraw.Draw(img)
        cx, cy = 12, 60          # 卡片原点
        cw, ch = 216, 150        # 卡片大小
        # 卡片背景
        draw.rounded_rectangle((cx, cy, cx + cw, cy + ch),
                               radius=10, fill=(36, 40, 52), outline=(80, 86, 104))
        # 和弦名
        if self.font:
            draw.text((cx + 14, cy + 10), chord_name, font=self.font, fill=CHORD_RED)

        # 6 根弦（竖线）：x 方向均布
        n_str = 6
        gap = cw // (n_str + 1)
        top, bot = cy + 44, cy + ch - 16
        for i in range(n_str):
            x = cx + gap * (i + 1)
            draw.line((x, top, x, bot), fill=OPEN_GRAY, width=2)
        # 品位横线（1~4 品参考线）
        for f in range(1, 5):
            y = top + f * (bot - top) // 5
            draw.line((cx + 10, y, cx + cw - 10, y), fill=(70, 74, 88), width=1)
        # 按弦圆点（红）
        for i, fret in enumerate(frets):
            if fret is None or fret < 0:
                continue
            x = cx + gap * (i + 1)
            y = top + min(fret, 4) * (bot - top) // 5
            r = 7
            draw.ellipse((x - r, y - r, x + r, y + r), fill=CHORD_RED, outline=(255, 255, 255))
        # 空弦标记（顶部小圈）
        for i, fret in enumerate(frets):
            if fret == 0:
                x = cx + gap * (i + 1)
                draw.ellipse((x - 5, top - 14, x + 5, top - 4), outline=OPEN_GRAY, width=2)
        # 弦号
        if self.font_tiny:
            labels = ["e", "B", "G", "D", "A", "E"]
            for i, lab in enumerate(labels):
                x = cx + gap * (i + 1)
                draw.text((x - 4, bot + 2), lab, font=self.font_tiny, fill=OPEN_GRAY)

    # ──────────── ② 进度条 + 当前小节/拍 ────────────
    def draw_progress(self, img, total_ms: int, cur_ms: int, bar: int, beat: int, bars_total: int):
        """
        total_ms/cur_ms: 整曲与已播时长（用于条宽）；bar/beat/bars_total: 小节位置文本。
        """
        draw = ImageDraw.Draw(img)
        y = 232
        # 条底
        draw.rounded_rectangle((12, y, 228, y + 14), radius=7, fill=PROG_BG)
        # 条前景
        if total_ms > 0:
            frac = max(0.0, min(1.0, cur_ms / total_ms))
            w = int(216 * frac)
            if w > 4:
                draw.rounded_rectangle((12, y, 12 + w, y + 14), radius=7, fill=PROG_BLUE)
        # 小节文本
        if self.font_small:
            text = f"{bar}/{bars_total} · 拍{beat}"
            draw.text((12, y + 22), text, font=self.font_small, fill=FG)

    # ──────────── ③ 顶部状态条 ────────────
    def draw_status_bar(self, img, song: str, mode: str, voltage: float):
        """song=歌名，mode=full/her，voltage=电池电压"""
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle((0, 0, WIDTH, 44), radius=0, fill=(30, 33, 44))
        if self.font_small:
            draw.text((12, 10), song or "—", font=self.font_small, fill=FG)
            draw.text((12, 28), f"{mode}  {voltage:.1f}V", font=self.font_tiny,
                      fill=MODE_GRN if mode == "her" else PROG_BLUE)
        # her 模式绿点（右上角）
        if mode == "her":
            draw.ellipse((216, 16, 228, 28), fill=MODE_GRN)

    # ──────────── 组合一屏：状态条 + 和弦卡 + 进度 ────────────
    def render_play_page(self, song, mode, voltage, total_ms, cur_ms,
                         bar, beat, bars_total, chord_name, frets):
        img = self.new_image()
        self.draw_status_bar(img, song, mode, voltage)
        self.draw_chord_card(img, chord_name, frets)
        self.draw_progress(img, total_ms, cur_ms, bar, beat, bars_total)
        self.show(img)


def make_fonts():
    """生成 3 档中文/数字字体。把 NotoSansCJK / 思源黑体 放 /root/guitarist_app/fonts/。"""
    from PIL import ImageFont
    base = "/root/guitarist_app/fonts/"
    def _load(name, size, fallback_size=12):
        try:
            return ImageFont.truetype(base + name, size)
        except Exception:
            try:
                return ImageFont.truetype(base + "NotoSansCJK-Regular.ttc", fallback_size)
            except Exception:
                return None
    return {
        "font":       _load("SourceHanSans.ttc", 20, 16),
        "font_small": _load("SourceHanSans.ttc", 14, 12),
        "font_tiny":  _load("SourceHanSans.ttc", 11, 9),
    }
