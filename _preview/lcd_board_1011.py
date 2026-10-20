#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
彩屏渲染器模块（37 号课题 · 1.54″ ST7789 SPI）
================================================
从 oled_renderer.OLEDRenderer 派生：把底层「1-bit OLED」换成「24-bit SPI 彩屏」。

设计原则
  * 继承 OLEDRenderer，**只重写 3 个底层方法**（new_image / clear / show），
    上面 20 个 show_* 页面方法（菜单/进度/和弦/调音器…）全部白嫖复用。
  * 黑白 1-bit 图 → 逐像素转换 RGB565，经 SPI 送屏；
    填充 0（"黑"）映射成深灰、填充 255（"白"）映射成深蓝，
    让沿用旧版式（白底反色）的页面在彩屏上仍然可读、且符合商务深蓝色调。
  * SPI 用 **纯 stdlib（fcntl + ioctl）**，不依赖 spidev、不依赖 blinka 的
    machine SPI（板端两者都缺）。已用 tools/lcd_check.py 实测 /dev/spidev3.0 发数通。
  * DC / RES 用 blinka digitalio（已装）控制。

引脚（泰山派 40PIN，立创官方表已复核，见 48 号总表 §2.5）：
    VCC→17(3.3V) GND→14  SDA/MOSI→19(GPIO4_C3=51)  SCL→23(GPIO4_C2=50)
    CS→24(GPIO4_C6=54，硬件片选走内核驱动)  DC→29(GPIO3_B0=104)
    RES→31(GPIO3_C2=114)  BLK→1/17(3.3V)

用法
    lcd = LCDRenderer(fonts=fonts)        # 不传 oled，自建 SPI 驱动
    lcd.show_menu(...)                   # 与 OLEDRenderer 同接口
"""

import math
import struct
import time
from typing import Optional

try:
    import fcntl          # Linux 专属。板上一定有；PC 上只做版式预览时缺席
except ImportError:       # pragma: no cover - 仅 PC 预览路径走到这里
    fcntl = None          # _Spi 的方法只在真建 St7789 时才调 fcntl

from PIL import Image, ImageDraw

try:
    import board
    from digitalio import DigitalInOut, Direction
except Exception:  # pragma: no cover - blinka 缺席时仍允许 dry-run 测试
    board = None
    DigitalInOut = None
    Direction = None

from oled_renderer import OLEDRenderer


# ───────────────────────── SPI（纯 stdlib ioctl）────────────────────────
class _Spi:
    """对 /dev/spidev* 的最薄封装（只发数据，不读——ST7789 写屏用不到 MISO）。"""

    _WR_MODE = 0x40016B01
    _WR_BITS = 0x40016B03
    _WR_MAXSPEED = 0x40046B04
    _MSG = 0x40206B00

    def __init__(self, dev="/dev/spidev3.0", speed=12_000_000, mode=0):
        import ctypes
        self._ct = ctypes
        self._fd = open(dev, "r+b", buffering=0)
        fcntl.ioctl(self._fd, self._WR_MODE, struct.pack("<B", mode & 0x3))
        fcntl.ioctl(self._fd, self._WR_BITS, struct.pack("<B", 8))
        fcntl.ioctl(self._fd, self._WR_MAXSPEED, struct.pack("<I", speed))

    def write(self, data: bytes):
        """把 data 作为 SPI 脉冲发出去（分块，绕开内核单批「Message too long」上限）。"""
        ct = self._ct
        CH = 4096
        for i in range(0, len(data), CH):
            chunk = data[i:i + CH]
            buf = ct.create_string_buffer(chunk, len(chunk))
            msg = struct.pack(
                "=QQIIHBBI",
                ct.addressof(buf), 0, len(chunk), 0, 0, 8, 0, 0,
            )
            fcntl.ioctl(self._fd, self._MSG, msg)


class _Gpio:
    """DC / RES 的薄封装；blinka 缺席时退化为空操作（dry-run）。"""

    def __init__(self, gpio_obj, active=True):
        self._l = gpio_obj
        self.active = active

    def set(self, v: int):
        if self._l is not None:
            self._l.value = bool(v)

    def release(self):
        try:
            self._l and self._l.deactivate()
        except Exception:
            pass


def _make_gpio(name: str, global_num: int):
    """按 blinka 板属性名取 GPIO；失败则按全局编号走 sysfs（板端 6.1 内核支持）。"""
    if board is not None:
        try:
            pin = getattr(board, name)
            g = DigitalInOut(pin)
            g.direction = Direction.OUTPUT
            g.value = 1
            return g
        except Exception:
            pass
    # sysfs 兜底：export → direction=out → value=1
    import os
    base = "/sys/class/gpio"
    try:
        with open(base + "/export", "w") as f:
            f.write(str(global_num))
        with open(base + "/gpio%d/direction" % global_num, "w") as f:
            f.write("out")
        with open(base + "/gpio%d/value" % global_num, "w") as f:
            f.write("1")
        class _Sysfs:
            _n = global_num

            def __init__(self):
                self._dir = base + "/gpio%d" % self._n

            def __setattr__(self, k, v):
                if k == "value":
                    open(self._dir + "/value", "w").write("1" if v else "0")
                else:
                    object.__setattr__(self, k, v)

            def deactivate(self):
                try:
                    open(base + "/gpio%d/unexport" % self._n, "w").close()
                except Exception:
                    pass
        return _Sysfs()
    except Exception:
        return None


# ───────────────────────── ST7789 驱动 ─────────────────────────
class St7789:
    """极简 ST7789：够驱动 1.54″ 240×240 彩屏（初始化 + 全屏/矩形写像素）。"""

    def __init__(self, w=240, h=240, spi_dev="/dev/spidev3.0", speed=12_000_000,
                 dc_num=104, rst_num=114):
        self.w, self.h = w, h
        self.spi = _Spi(spi_dev, speed)
        self.dc = _Gpio(_make_gpio("GPIO3_B0", dc_num))
        self.rst = _Gpio(_make_gpio("GPIO3_C2", rst_num))

    # ── 底层命令 / 数据 ──
    def _cmd(self, c: int):
        self.dc.set(0)
        self.spi.write(bytes([c]))

    def _data(self, b: bytes):
        self.dc.set(1)
        self.spi.write(b)

    def _cmd_data(self, c: int, d: bytes = None):
        self._cmd(c)
        if d is not None:
            self._data(d)

    # ── 硬件复位 + 初始化序列（1.54″ ST7789 通用版）──
    def init(self):
        r = self.rst
        if r is not None:
            r.set(1)
            time.sleep(0.05)
            r.set(0)
            time.sleep(0.05)
            r.set(1)
            time.sleep(0.15)
        self._cmd_data(0x11)            # Sleep out
        time.sleep(0.12)
        self._cmd_data(0x36, b"\x00")   # 显示方向
        self._cmd_data(0x3A, b"\x55")   # RGB565 16bit/px
        self._cmd_data(0x21)            # 异象（泪滴）图像开启
        self._cmd_data(0x13)            # 正常显示模式
        self._cmd_data(0x29)            # Display ON
        time.sleep(0.05)

    # ── 写一帧 RGB565 字节流到全屏 ──
    def write_frame(self, rgb565: bytes):
        self._cmd_data(0x2A, struct.pack(">HH", 0, self.w - 1))   # CASET
        self._cmd_data(0x2B, struct.pack(">HH", 0, self.h - 1))   # RASET
        self._cmd(0x2C)                                            # RAMWR
        self.dc.set(1)
        self.spi.write(rgb565)

    def fill_rgb565(self, rgb: int):
        hi, lo = (rgb >> 8) & 0xFF, rgb & 0xFF
        buf = bytes((hi, lo)) * (self.w * self.h)
        self.write_frame(buf)


# ───────────────────────── 渲染器（继承 OLEDRenderer）────────────────────────
class LCDRenderer(OLEDRenderer):
    """彩屏版渲染器：底层 SPI ST7789，顶层页面复用 OLEDRenderer。

    * 1-bit 黑白图 → 逐像素映射 RGB565（填充 0→深灰、填充 255→深蓝），
      使沿用旧版式（白底反色）的页面在彩屏上可读且贴近商务深蓝色调。
    * 颜色常量集中在模块底部，要换主题只改这两行。
    """

    def __init__(self, fonts: dict, w: int = 240, h: int = 240,
                 spi_dev: str = "/dev/spidev3.0", speed: int = 12_000_000,
                 dc_num: int = 104, rst_num: int = 114, oled=None, dry_run: bool = False,
                 perform_window_s: float = 7.0):
        # 不走 OLEDRenderer.__init__（它要 oled 实例）；手动建字段
        self.font = fonts["font"]
        self.font_small = fonts["font_small"]
        self.font_tiny = fonts["font_tiny"]
        self.width, self.height = w, h
        self.dry_run = dry_run
        self._perform_window_ms = max(1000.0, float(perform_window_s) * 1000.0)
        self._head_x = 26                     # 播放头竖线的 x（演奏主界面）
        self._rgb565 = bytearray(w * h * 2)
        if oled is None:
            self._disp = St7789(w, h, spi_dev, speed, dc_num, rst_num)
            if not dry_run:
                self._disp.init()
                time.sleep(0.05)
                self._disp.fill_rgb565(self._t565("bg"))   # 底色随界面模式
        else:
            self._disp = oled                          # 注入式（测试用）

    # ── 重写的 3 个底层方法 ──
    def new_image(self):
        """旧接口返回 1-bit 图；上层 draw 完仍用它，show() 再转彩。"""
        return Image.new("1", (self.width, self.height))

    def clear(self):
        if not self.dry_run:
            self._disp.fill_rgb565(self._t565("bg"))

    def show(self, img):
        """1-bit 图 → RGB565 → SPI。img 为 None（clear-only）时直接刷底色。"""
        if self.dry_run or self._disp is None:
            return
        if img is None:
            self._disp.fill_rgb565(self._t565("bg"))
            return
        if img.mode == "1":
            px = img.tobytes()          # 每行 ceil(w/8) 字节，1bpp，逐位扫描
            rows = [px[r * ((self.width + 7) // 8):(r + 1) * ((self.width + 7) // 8)]
                    for r in range(self.height)]
        else:                            # 已是 RGB/RGB565 的透传
            px = None
            rows = None
        out = self._rgb565
        if px is not None:
            for y in range(self.height):
                row = rows[y]
                base = y * self.width * 2
                for x in range(self.width):
                    bit = 1 if (row[x // 8] >> (7 - x % 8)) & 1 else 0
                    # ★ 2026-10-11 修正（用户上报「桃红色背景 + 左上角小字」）：
                    #   原值 0x08415 / 0x0800F 解出来的 RGB 是 (132,65,173) 紫 与
                    #   (132,0,123) 桃红 —— 等于「紫字画在桃红底上」，对比度极低，
                    #   老版式页面（进度条 / 提示 / 菜单 / 确认框）全被染成桃红。
                    #   老版式沿用 OLED 的「白字黑底」约定（fill(255)=点亮）：
                    #   亮部(1) → 纯白（文字/高亮块），暗部(0) → 纯黑（背景）。
                    rgb = 0xFFFF if bit else 0x0000
                    out[base + x * 2:base + x * 2 + 2] = bytes(((rgb >> 8) & 0xFF, rgb & 0xFF))
        self._disp.write_frame(bytes(out))

    def show_rgb565_frame(self, frame: bytes):
        """直接送一帧 240×240×2 的 RGB565（做彩色 UI 时用，如和弦图）。"""
        if not self.dry_run:
            self._disp.write_frame(frame)

    # ── 彩色 UI（240×240，绕过 1-bit 继承层直接画）──
    # 色板（RGB565 数值）→ 转成 RGB 元组供 PIL 直接画，最后一次 convert("RGB565")。
    @staticmethod
    def _rgb565_to_rgb(v):
        r = (v >> 11) & 0x1F
        g = (v >> 5) & 0x3F
        b = v & 0x1F
        return (r * 255 // 31, g * 255 // 63, b * 255 // 31)

    _C_BG = 0x0000          # 纯黑底（Z 要求）
    _C_PANEL = 0x1028A       # 面板深蓝
    _C_HI = 0xF800          # 亮红（当前）
    _C_TXT = 0x07E0          # 亮绿（下一个 / 进度）
    _C_DIM = 0x102A          # 深灰（未选中方块，跟纯黑底拉开对比）
    _C_WHT = 0xFFFF          # 白
    # ── 演奏主界面配色（Z 2026-10-10：音浪 / 和弦 / 压弦）──
    _C_CYAN = 0x07FF        # 青 —— 压弦音浪柱
    _C_ORG = 0xFD20         # 橙 —— 拨弦音浪柱 / 和弦标签
    _C_YEL = 0xFFE0         # 亮黄 —— 歌名（醒目）
    _C_GRID = 0x18C3        # 暗灰蓝 —— 中线 / 网格 / 未按弦
    _C_TXT2 = 0xCE79        # 浅灰 —— 辅助文字

    def _rgb(self, name):
        return self._rgb565_to_rgb(getattr(type(self), name))

    # ── 界面模式（theme.py）：渲染层只认「逻辑色名」，不认具体色值 ──
    #   2026-10-11（Z：想要"好几个界面模式，比如赛博朋克 / 海浪上下浮动"）：
    #   把配色从类常量抽到 theme.py 的注册表；这里保留一份「万一 theme 不可用
    #   时的旧色回退」，保证降级后画面和以前一模一样。
    _THEME_FALLBACK = {
        "bg": "_C_BG", "panel": "_C_PANEL", "title": "_C_YEL", "txt": "_C_TXT",
        "dim": "_C_DIM", "grid": "_C_GRID", "txt2": "_C_TXT2",
        "press": "_C_CYAN", "strum": "_C_ORG", "playhead": "_C_WHT",
        "accent": "_C_TXT", "prog_bg": "_C_GRID",
    }

    def _theme(self):
        """当前界面模式 dict（theme.py 是单一真相源）；不可用 → None。"""
        try:
            import theme as _th
            return _th.get_theme()
        except Exception:
            return None

    def theme_key(self):
        try:
            import theme as _th
            return _th.current_key()
        except Exception:
            return "wave"

    def _t565(self, name):
        """逻辑色名 → RGB565 int。优先当前模式，退回旧类常量。"""
        try:
            th = self._theme()
            if th and name in th:
                return int(th[name])
        except Exception:
            pass
        attr = self._THEME_FALLBACK.get(name)
        if attr:
            return getattr(type(self), attr, 0x0000)
        return 0x0000

    def _t(self, name):
        """逻辑色名 → (r, g, b)。"""
        return self._rgb565_to_rgb(self._t565(name))

    @staticmethod
    def _mix(c1, c2, t):
        """两色线性混合（t=0 → c1，t=1 → c2）；用来在纯色板上造纵深，不引入渐变。"""
        return tuple(int(a * (1.0 - t) + b * t) for a, b in zip(c1, c2))

    # ── 带缓存的中文字体加载（truetype 每次 new 很慢，缓存住）──
    _CJK = "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"

    def _ttf(self, size):
        c = getattr(self, "_ttfc", None)
        if c is None:
            c = self._ttfc = {}
        size = int(size)
        if size in c:
            return c[size]
        from PIL import ImageFont
        try:
            f = ImageFont.truetype(self._CJK, size)
        except Exception:
            f = None
        c[size] = f or self.font_small
        return c[size]

    def _flush_p(self, img):
        """RGB 图 → RGB565 字节流 → SPI（PIL 无 RGB565 模式，手动拼 565）。

        每个像素 2 字节：高字节在前（MSB first，与 ST7789 RAMWR 的字节序一致）。
        ★ 滚动动画要 ~5FPS，逐像素 Python 循环太慢 → 优先走 numpy 向量化；
        板端无 numpy 时回退纯 Python（功能不变，只是慢）。
        """
        if self.dry_run:
            return
        rgb = img.convert("RGB")
        # RGB565 位宽：R5 / G6 / B5 → 移位必须是 >>3 / >>2 / >>3。
        # ★ 2026-10-10 修正：蓝通道原写成 >>5，把 8bit 蓝直接砍到 3bit（满蓝只剩
        #   7/31），白色被画成淡黄、浅灰发黄。选歌屏的大面积浅灰字把这个问题暴露了。
        try:
            import numpy as _np
            a = _np.asarray(rgb, dtype=_np.uint16)          # (H, W, 3)
            v = ((a[:, :, 0] >> 3) << 11) | ((a[:, :, 1] >> 2) << 5) | (a[:, :, 2] >> 3)
            self._disp.write_frame(v.astype(">u2").tobytes())   # big-endian = MSB first
            return
        except Exception:
            pass
        px = list(rgb.getdata())
        buf = bytearray(len(px) * 2)
        for i, (r, g, b) in enumerate(px):
            v = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
            buf[i * 2] = (v >> 8) & 0xFF        # 高字节
            buf[i * 2 + 1] = v & 0xFF           # 低字节
        self._disp.write_frame(bytes(buf))

    def _new_canvas(self):
        W, H = self._disp.w, self._disp.h
        bg = self._t("bg")          # 底色随界面模式走（默认黑，与旧版一致）
        img = Image.new("RGB", (W, H), bg)
        return img

    def show_chord_panel(self, active_index, next_index=None,
                         chord_names=("C", "G", "Am", "F", "Dm", "Em"),
                         label="当前 · 下一个"):
        """彩屏和弦面板（3×2 布局）：

        - 上区：大字区（当前和弦名 + 下一个和弦名）
        - 下区：两排方块  C G Am / F Dm Em，当前亮红、下一个亮绿、其余灰
        `active_index`  = 当前该按的方块（0..5）
        `next_index`    = 下一个和弦（0..5），None 则不画
        """
        img = self._new_canvas()
        d = ImageDraw.Draw(img)
        W, H = self._disp.w, self._disp.h
        c_hi = self._rgb("_C_HI")
        c_gd = self._rgb("_C_TXT")
        c_dim = self._rgb("_C_DIM")
        c_wht = self._rgb("_C_WHT")

        from PIL import ImageFont
        def _f(size):
            try:
                return ImageFont.truetype("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc", size)
            except Exception:
                return self.font
        f_big = _f(26)
        f_tag = _f(10)

        # ── 上半屏（y 0~80，约 1/3）：左右两格，当前 | 下一个 ──
        half_w = W // 2
        top_h = 80
        # 中间竖线（分隔当前/下一个）
        d.line([half_w, 8, half_w, top_h - 4], fill=c_dim, width=1)
        # 当前（左格，半格内水平居中）
        cur = chord_names[active_index] if 0 <= active_index < 6 else "—"
        cur_tag_w = d.textlength("当前", font=f_tag)
        d.text(((half_w - cur_tag_w) // 2, 8), "当前", font=f_tag, fill=c_gd)
        cur_w = d.textlength(cur, font=f_big)
        d.text(((half_w - cur_w) // 2, 24), cur, font=f_big, fill=c_wht)
        # 下一个（右格，半格内水平居中）
        nxt = chord_names[next_index] if next_index is not None and 0 <= next_index < 6 else "—"
        nxt_tag_w = d.textlength("下一个", font=f_tag)
        d.text((half_w + (half_w - nxt_tag_w) // 2, 8), "下一个", font=f_tag, fill=c_gd)
        nxt_w = d.textlength(nxt, font=f_big)
        d.text((half_w + (half_w - nxt_w) // 2, 24), nxt, font=f_big, fill=c_gd)
        # 横线（分隔上下半屏）
        d.line([8, top_h, W - 8, top_h], fill=c_dim, width=1)

        # ── 下半屏（y 80~240，约 2/3）：3×2 大方格 ──
        area_top = top_h + 8
        area_h = H - area_top - 4          # 约 152
        col_w = W // 3
        cell_w = col_w - 8
        row_gap = 10
        cell_h = (area_h - row_gap) // 2   # 两排均分，更大
        row_y1 = area_top
        row_y2 = row_y1 + cell_h + row_gap

        f_cell = _f(18)                    # 方块内字随格放大

        for i in range(6):
            row = i // 3
            col = i % 3
            x = col * col_w + 4
            y = row_y1 if row == 0 else row_y2
            fill = c_hi if i == active_index else (c_gd if i == next_index else c_dim)
            d.rectangle([x, y, x + cell_w, y + cell_h], fill=fill)
            if i == active_index:
                d.line([x, y, x + cell_w, y], fill=c_wht, width=2)
            nm = chord_names[i]
            nw = d.textlength(nm, font=f_cell)
            d.text((x + (cell_w - nw) // 2, y + cell_h // 2 - 9), nm,
                   font=f_cell, fill=c_wht)

        d.text((8, 214), label, font=self.font_tiny, fill=c_gd)
        self._flush_p(img)

    # ═════════════ 选歌屏（Z 2026-10-10 需求）═════════════
    @staticmethod
    def _fit(d, text, font, max_w):
        """按**像素宽度**截断并补「…」。

        中文按字符数截会算错（一个汉字 ≈ 一个 20px 字号），所以必须先量宽度。
        """
        if d.textlength(text, font=font) <= max_w:
            return text
        for cut in range(len(text) - 1, 0, -1):
            cand = text[:cut] + "…"
            if d.textlength(cand, font=font) <= max_w:
                return cand
        return "…"

    def show_song_list(self, songs, selected_index=0, title="选 歌"):
        """彩屏选歌屏（240×240 大字版）。

        版式::

            ┌────────────────────────────┐
            │ 选 歌              2/3     │ y 0~40   标题亮黄 / 计数浅灰
            │────────────────────────────│
            │ ▶ 晴天                     │ y 46~206 5 行 × 32px
            │   稻香                     │          选中=亮红实心块+白字
            │   后来                     │          未选=浅灰
            │────────────────────────────│
            │   上/下 选   SET 弹  ESC 退 │ y 218
            └────────────────────────────┘

        ``songs`` 直接吃 ``song_library.scan_songs()["songs"]``
        （``[{"name",...}]``），也兼容纯字符串列表。
        列表为空时给一句明确的话，而不是留一块黑屏让人猜。
        """
        img = self._new_canvas()
        d = ImageDraw.Draw(img)
        W = self._disp.w
        c_yel = self._rgb("_C_YEL")
        c_hi = self._rgb("_C_HI")
        c_wht = self._rgb("_C_WHT")
        c_txt2 = self._rgb("_C_TXT2")
        c_dim = self._rgb("_C_DIM")

        f_title = self._ttf(20)
        f_cnt = self._ttf(14)
        f_row = self._ttf(20)
        f_sel = self._ttf(22)

        n = len(songs or [])

        # ── 标题栏 ──
        d.text((10, 8), title, font=f_title, fill=c_yel)
        if n:
            cnt = "%d/%d" % (max(1, min(selected_index + 1, n)), n)
            d.text((W - 10 - d.textlength(cnt, font=f_cnt), 14), cnt,
                   font=f_cnt, fill=c_txt2)
        d.line([0, 40, W, 40], fill=c_dim, width=2)

        ROWS, ROW_H, TOP = 5, 32, 46
        TXT_X, TXT_MAXW = 30, 186        # 右侧留 ~22px 给"还有更多"小三角

        if not n:
            d.text((10, 84), "曲库里还没有", font=f_row, fill=c_wht)
            d.text((10, 114), "能弹的歌", font=f_row, fill=c_wht)
            d.text((10, 156), "把歌放进来再回来", font=self._ttf(13), fill=c_txt2)
        else:
            sel = max(0, min(int(selected_index), n - 1))
            start = max(0, min(sel - ROWS // 2, n - ROWS))
            for r in range(ROWS):
                i = start + r
                if i >= n:
                    break
                y = TOP + r * ROW_H
                raw = songs[i]
                name = raw if isinstance(raw, str) else raw.get("name", "?")
                if i == sel:
                    d.rectangle([6, y, W - 6, y + ROW_H - 4], fill=c_hi)
                    d.text((12, y + 3), ">", font=f_sel, fill=c_wht)
                    d.text((TXT_X, y + 3), self._fit(d, name, f_sel, TXT_MAXW),
                           font=f_sel, fill=c_wht)
                else:
                    d.text((TXT_X, y + 6), self._fit(d, name, f_row, TXT_MAXW),
                           font=f_row, fill=c_txt2)
            # 列表外还有更多 → 右侧上下小三角（不依赖字体是否有箭头字形）
            if start > 0:
                d.polygon([(W - 15, 52), (W - 23, 62), (W - 7, 62)], fill=c_txt2)
            if start + ROWS < n:
                d.polygon([(W - 15, 200), (W - 23, 190), (W - 7, 190)], fill=c_txt2)

        # ── 底部按键提示 ──
        hint = "上/下 选    SET 弹    ESC 退"
        f_hint = self._ttf(13)
        if d.textlength(hint, font=f_hint) > W - 12:
            f_hint = self._ttf(11)
        d.text(((W - d.textlength(hint, font=f_hint)) // 2, 218), hint,
               font=f_hint, fill=c_txt2)
        self._flush_p(img)

    def show_progress_panel(self, song_name, progress):
        """彩屏进度条：歌名 + 0~100% 横向条 + 百分比大字。progress: 0.0~100.0"""
        img = self._new_canvas()
        d = ImageDraw.Draw(img)
        W = self._disp.w
        c_panel = self._t("prog_bg")
        c_gd = self._t("accent")
        c_wht = self._t("playhead")
        c_title = self._t("title")
        p = max(0.0, min(100.0, float(progress)))

        d.text((8, 10), song_name[:14], font=self.font_small, fill=c_title)
        bar_x, bar_y, bar_w, bar_h = 16, 100, W - 32, 20
        d.rectangle([bar_x, bar_y, bar_x + bar_w, bar_y + bar_h], fill=c_panel)
        fill_w = int(bar_w * p / 100)
        if fill_w > 0:
            d.rectangle([bar_x, bar_y, bar_x + fill_w, bar_y + bar_h], fill=c_gd)
        pct = f"{int(p)}%"
        d.text(((W - d.textlength(pct, font=self.font)) // 2, 150), pct,
               font=self.font, fill=c_wht)
        d.text((8, 214), "演奏进度", font=self.font_tiny, fill=c_gd)
        self._flush_p(img)

    def show_volume_slider(self, title, percent, value_text=None, hint=None):
        """彩屏全屏版滑动条（覆盖 OLED 的 128×64 版式）。

        用于设置类交互（麦克风/扬声器音量、装机角度微调）——240×240 上
        用「标题 + 大号数值 + 横向滑条 + 底部操作提示」，配色跟随当前界面模式，
        与演奏主界面同一套色，切换模式时设置屏也跟着变。

        value_text: 直接显示的数值文本（如 "92°"）；None 则显示百分比。
        hint: 底部操作提示；None 用默认音量提示。
        """
        img = self._new_canvas()
        d = ImageDraw.Draw(img)
        W, H = self._disp.w, self._disp.h
        c_title = self._t("title")
        c_val = self._t("playhead")
        c_bar = self._t("prog_bg")
        c_fill = self._t("press")
        c_txt2 = self._t("txt2")

        p = max(0.0, min(100.0, float(percent)))

        # ① 标题（过长自动降号）
        f_title = self._ttf(19)
        if d.textlength(title, font=f_title) > W - 16:
            f_title = self._ttf(15)
        d.text(((W - d.textlength(title, font=f_title)) // 2, 16), title,
               font=f_title, fill=c_title)

        # ② 大号数值
        vtxt = value_text if value_text is not None else ("%d%%" % int(p))
        f_val = self._ttf(52)
        for fs in (52, 44, 36, 28):
            f_val = self._ttf(fs)
            if d.textlength(vtxt, font=f_val) <= W - 24:
                break
        vw = d.textlength(vtxt, font=f_val)
        d.text(((W - vw) // 2, 74), vtxt, font=f_val, fill=c_val)

        # ③ 横向滑条 + 滑块
        bar_x, bar_w = 30, W - 60
        bar_y, bar_h = 156, 18
        bx1, by1, bx2, by2 = bar_x, bar_y, bar_x + bar_w, bar_y + bar_h
        d.rounded_rectangle([bx1, by1, bx2, by2], radius=9, fill=c_bar)
        fw = int(bar_w * p / 100.0)
        if fw > 0:
            d.rounded_rectangle([bx1, by1, bx1 + fw, by2], radius=9, fill=c_fill)
        kx = min(max(bx1, bx1 + fw), bx2)
        d.ellipse([kx - 7, bar_y - 5, kx + 7, bar_y + bar_h + 5], fill=c_val)

        # ④ 底部操作提示
        ht = hint if hint is not None else "SET:确认  ESC:返回"
        f_h = self._ttf(14)
        if d.textlength(ht, font=f_h) > W - 12:
            f_h = self._ttf(12)
        d.text(((W - d.textlength(ht, font=f_h)) // 2, 208), ht,
               font=f_h, fill=c_txt2)
        self._flush_p(img)

    # ═══════ 舵机装机角度调整专用屏（Z 2026-10-11：用那 3 个触摸键调）═══════
    def show_servo_adjust(self, angle, min_v=60.0, max_v=120.0, default_v=90.0,
                          hint1=None, hint2=None, title="舵机调整"):
        """舵机装机角度调整专用屏（240×240 全屏）。

        为「只有 UP / SET / DOWN 三个触摸键」的场景而做（琴上第 4 个触摸模块
        还没装，所以这个界面**不依赖 ESC**）：

          · 中间**大号角度数字** —— 站在琴边一眼看清现在调到多少度；
          · 下方带 10° 刻度的滑条，**白色竖线标出 90° 默认位**，
            调完一眼能看出偏离默认多远、该往哪边回；
          · 最底部写清三个键各自干什么（保存 / 取消都不需要第 4 个键）。
        """
        img = self._new_canvas()
        d = ImageDraw.Draw(img)
        W = self._disp.w
        c_title = self._t("title")
        c_val = self._t("playhead")
        c_bar = self._t("prog_bg")
        c_fill = self._t("press")
        c_txt2 = self._t("txt2")
        c_grid = self._t("grid")

        angle = float(angle)
        lo, hi = float(min_v), float(max_v)
        p = 0.0 if hi <= lo else max(0.0, min(100.0, (angle - lo) / (hi - lo) * 100.0))

        # ① 标题 + 副标题
        f1 = self._ttf(20)
        d.text(((W - d.textlength(title, font=f1)) // 2, 8), title,
               font=f1, fill=c_title)
        f2 = self._ttf(13)
        sub = "和弦收缩位（齿条收回）"
        d.text(((W - d.textlength(sub, font=f2)) // 2, 34), sub,
               font=f2, fill=c_txt2)

        # ② 大号角度（自适应降号，放得下为止）
        vtxt = "%d°" % int(round(angle))
        f_val = self._ttf(60)
        for fs in (60, 52, 46, 38, 30):
            f_val = self._ttf(fs)
            if d.textlength(vtxt, font=f_val) <= W - 24:
                break
        d.text(((W - d.textlength(vtxt, font=f_val)) // 2, 54), vtxt,
               font=f_val, fill=c_val)

        # ③ 范围标注（滑条两端）
        fd = self._ttf(12)
        d.text((26, 118), "%d°" % int(lo), font=fd, fill=c_txt2)
        r = "%d°" % int(hi)
        d.text((W - 26 - d.textlength(r, font=fd), 118), r, font=fd, fill=c_txt2)

        # ④ 滑条 + 10° 刻度 + 90° 默认位白线
        bar_x, bar_w = 26, W - 52
        bar_y, bar_h = 164, 16
        d.rounded_rectangle([bar_x, bar_y, bar_x + bar_w, bar_y + bar_h],
                            radius=8, fill=c_bar)
        fw = int(bar_w * p / 100.0)
        if fw > 0:
            d.rounded_rectangle([bar_x, bar_y, bar_x + fw, bar_y + bar_h],
                                radius=8, fill=c_fill)
        if hi > lo:
            v = lo
            while v <= hi + 0.01:
                tx = bar_x + bar_w * (v - lo) / (hi - lo)
                d.line([tx, bar_y + bar_h + 2, tx, bar_y + bar_h + 6],
                       fill=c_grid, width=1)
                v += 10.0
            dx = bar_x + bar_w * (default_v - lo) / (hi - lo)
            d.line([dx, bar_y - 10, dx, bar_y + bar_h + 8], fill=c_val, width=2)
            fdt = self._ttf(11)
            dtxt = "默认%d°" % int(default_v)
            d.text((dx - d.textlength(dtxt, font=fdt) / 2.0, bar_y - 26),
                   dtxt, font=fdt, fill=c_val)
        kx = min(max(bar_x, bar_x + fw), bar_x + bar_w)
        d.ellipse([kx - 8, bar_y - 6, kx + 8, bar_y + bar_h + 6], fill=c_val)

        # ⑤ 三个触摸键的说明（不需要 ESC）
        h1 = hint1 or "UP / DOWN   调整角度"
        h2 = hint2 or "SET 保存并归位 · 长按 SET 取消"
        fh = self._ttf(14)
        if d.textlength(h1, font=fh) > W - 16:
            fh = self._ttf(12)
        d.text(((W - d.textlength(h1, font=fh)) // 2, 196), h1, font=fh, fill=c_txt2)
        fh2 = self._ttf(12)
        if d.textlength(h2, font=fh2) > W - 16:
            fh2 = self._ttf(11)
        d.text(((W - d.textlength(h2, font=fh2)) // 2, 218), h2, font=fh2, fill=c_txt2)

        self._flush_p(img)

    # ═════════════ 演奏主界面（Z 2026-10-10 需求）═════════════
    def show_perform_panel(self, song_name, progress, events,
                           chord_now="", frets_now=None, head_x=26):
        """自动演奏主界面 —— 上：歌名；中：音浪（从右到左）；下：压弦+和弦。

        `events`：调用方已把时间轴映射成屏幕 x 的列表，每项 dict：
            {"x":     float 0..240  该音符横坐标（右→左滚动由调用方算）
             "level": 0.0~1.0       音浪强度 → 柱高（以中线上下对称展开）
             "strum": bool          是否拨弦（柱色：拨弦=橙 / 压弦=青）
             "chord": str|None      该处若和弦变化 → 标和弦名}

        时间映射口径（调用方）：``x = head_x + (t_note - t_now)/WINDOW*(W-head_x)``,
        于是 head_x 左边是"刚过去"、右边是"即将到来"；head_x 处画白色播放头线。

        `frets_now`：6 弦当前按压值（0=松，1~4=品位），画在底部压弦符号区。
        """
        img = self._new_canvas()
        d = ImageDraw.Draw(img)
        W, H = self._disp.w, self._disp.h

        c_wht = self._t("playhead")
        c_yel = self._t("title")
        c_cyan = self._t("press")
        c_org = self._t("strum")
        c_grd = self._t("grid")
        c_gd = self._t("accent")
        c_t2 = self._t("txt2")
        c_fg = self._t("chord_fg")      # 压在实心标签/色块上的字色（多半是黑）

        f_title = self._ttf(32)
        f_tag = self._ttf(13)
        f_num = self._ttf(15)
        f_chord = self._ttf(22)
        f_mini = self._ttf(10)

        # ── ① 顶部：歌名（醒目）────────────────────────────
        title = song_name or "—"
        tw = d.textlength(title, font=f_title)
        while tw > W - 20 and len(title) > 1:
            title = title[:-1]
            tw = d.textlength(title, font=f_title)
        d.text(((W - tw) / 2.0, 5), title, font=f_title, fill=c_yel)
        d.line([14, 45, W - 14, 45], fill=c_grd, width=1)

        # ── ② 和弦标签带（y 52~82，随音浪一起滚；相邻重叠时只画先到的）──
        last_right = -999.0
        for ev in events:
            name = ev.get("chord")
            if not name:
                continue
            x = ev["x"]
            if x < -34 or x > W + 34:
                continue
            bw = d.textlength(name, font=f_tag) + 12
            bx = x - bw / 2.0
            if bx < last_right + 4:          # 和上一个标签叠在一起 → 跳过
                continue
            col = c_org if ev.get("strum") else c_cyan
            d.rounded_rectangle([bx, 52, bx + bw, 76], radius=5, fill=col)
            d.text((bx + 6, 57), name, font=f_tag, fill=c_fg)
            d.polygon([(x - 4, 76), (x + 4, 76), (x, 82)], fill=col)
            last_right = bx + bw

        # ── ③ 波形区（y 90~178，上下对称）──────────────────
        wav_top, wav_bot = 90, 178
        cy = (wav_top + wav_bot) // 2                 # 134
        max_half = (wav_bot - wav_top) // 2 - 4       # 40
        d.line([0, cy, W, cy], fill=c_grd, width=1)   # 中线

        th = self._theme() or {}
        wtype = th.get("wave")
        if wtype == "sine":
            # 「海浪」：连续正弦带上下浮动（Z 要的那种），音符强度调制波高
            self._draw_ocean_wave(d, W, events, cy, max_half, th)
        elif wtype == "bars_wave":
            # 「音浪 + 海浪」合体：离散柱骑在行进的正弦浪上（Z 2026-10-11）
            self._draw_bars_wave(d, W, events, cy, max_half, th,
                                 c_cyan, c_org, c_wht, c_grd)
        else:
            # 「音浪」：离散对称柱（原版）
            bar_w = 7
            for ev in events:
                x = ev["x"]
                if x < -bar_w or x > W + bar_w:
                    continue
                lvl = max(0.08, min(1.0, float(ev.get("level", 0.3))))
                h = int(max_half * lvl)
                col = c_org if ev.get("strum") else c_cyan
                d.rectangle([x - bar_w / 2.0, cy - h, x + bar_w / 2.0 - 1, cy + h], fill=col)
                d.rectangle([x - bar_w / 2.0, cy - h, x + bar_w / 2.0 - 1, cy - h + 2],
                            fill=c_wht)

        # 播放头竖线（左边 = 现在，右边 = 即将到来）
        d.line([head_x, wav_top, head_x, wav_bot], fill=c_wht, width=2)

        # ── ④ 底部：压弦符号（6 弦）+ 当前和弦 ──────────────
        d.line([14, 180, W - 14, 180], fill=c_grd, width=1)
        cell_w = 26
        fv = list(frets_now) if frets_now else [0] * 6
        for i in range(6):
            x0 = 8 + i * cell_w
            cx0, cx1 = x0 + 3, x0 + cell_w - 3
            d.text((x0 + cell_w / 2.0 - 3, 186), str(i + 1), font=f_mini, fill=c_t2)
            val = fv[i] if i < len(fv) else 0
            if val and val > 0:
                d.rounded_rectangle([cx0, 199, cx1, 221], radius=4, fill=c_cyan)
                s = str(val)
                sw = d.textlength(s, font=f_num)
                d.text((cx0 + (cx1 - cx0 - sw) / 2.0, 200), s, font=f_num, fill=c_fg)
            else:
                d.rounded_rectangle([cx0, 199, cx1, 221], radius=4, outline=c_grd, width=1)

        cname = chord_now or "—"
        cw = d.textlength(cname, font=f_chord)
        d.text((202 - cw / 2.0, 194), cname, font=f_chord, fill=c_wht)
        d.text((202 - d.textlength("和弦", font=f_mini) / 2.0, 220), "和弦",
               font=f_mini, fill=c_t2)

        # ── ⑤ 进度条 ──────────────────────────────────────
        p = max(0.0, min(100.0, float(progress)))
        bar_x, bar_y, bar_w2, bar_h = 8, 228, 132, 7
        d.rectangle([bar_x, bar_y, bar_x + bar_w2, bar_y + bar_h], fill=c_grd)
        fw = int(bar_w2 * p / 100.0)
        if fw > 0:
            d.rectangle([bar_x, bar_y, bar_x + fw, bar_y + bar_h], fill=c_gd)
        d.text((bar_x + bar_w2 + 6, 222), "%d%%" % int(p), font=f_mini, fill=c_gd)

        self._flush_p(img)

    def _draw_ocean_wave(self, d, W, events, cy, max_half, th):
        """「海浪」波形（ocean 模式）：两层连续正弦带上下浮动。

        设计要点
        --------
        * **相位跟墙钟走**（``time.time()``），不跟播放进度走 —— 界面只 5~6Hz
          上屏时，浪依然在"流"，不会一格一格跳；
        * 音符事件先泼洒成一条**强度包络** ``lv[x]``（三角核 + 两次 3 点平滑），
          波高 = 基础正弦 × 包络 ⇒ 弹得密/重的段落浪更高。信息没丢，只是换了形态；
        * **两层**：后层暗、慢半拍、波幅更大，前层亮 —— 纯色块叠出纵深，
          不用渐变（彩屏 565 + PIL 逐像素，渐变既慢又容易出带状）；
        * 音符另外画成**浪上的小珠**（压弦青 / 拨弦橙），保住"哪根是哪根"的读法。
        """
        t = time.time()
        lam = 46.0                              # 波长（px）
        k = 2.0 * math.pi / lam
        base_amp = max_half * 0.55

        # ── 音符强度包络 ──
        lv = [0.0] * W
        for ev in events:
            x = int(round(ev.get("x", -999)))
            if x < -24 or x > W + 24:
                continue
            a = max(0.0, min(1.0, float(ev.get("level", 0.3))))
            for dx in range(-10, 11):
                xx = x + dx
                if 0 <= xx < W:
                    lv[xx] = max(lv[xx], a * (1.0 - abs(dx) / 11.0))
        for _ in range(2):                      # 平滑 → 浪更"柔"
            lv = [(lv[max(0, i - 1)] + lv[i] + lv[min(W - 1, i + 1)]) / 3.0
                  for i in range(W)]

        c_line = self._rgb565_to_rgb(int(th.get("wave_line", th.get("accent", 0))))
        c_line2 = self._rgb565_to_rgb(int(th.get("wave_line2", th.get("grid", 0))))
        c_fill = self._rgb565_to_rgb(int(th.get("wave_fill", th.get("dim", 0))))
        c_bg = self._rgb565_to_rgb(int(th.get("bg", 0)))
        c_cyan = self._rgb565_to_rgb(int(th.get("press", 0)))
        c_org = self._rgb565_to_rgb(int(th.get("strum", 0)))

        def _y(x, phase, amp_scale, gain):
            xi = min(max(int(x), 0), W - 1)
            a = base_amp * amp_scale * (0.45 + 0.55 * gain * lv[xi])
            return cy - a * math.sin(k * x + phase)

        def _curve(phase, amp_scale, gain):
            return [(x, _y(x, phase, amp_scale, gain)) for x in range(0, W + 1, 3)]

        # ── 后层：暗、慢半拍、浪更大（先填，作为底）──
        back = _curve(t * 1.15 + 1.1, 1.25, 0.9)
        d.polygon(back + [(W, cy + max_half), (0, cy + max_half)],
                  fill=self._mix(c_fill, c_bg, 0.45))
        d.line(back, fill=c_line2, width=2, joint="curve")

        # ── 前层：亮、主浪（盖在上面的实心浪体）──
        ph_f = t * 1.15
        front = _curve(ph_f, 1.0, 1.0)
        d.polygon(front + [(W, cy + max_half), (0, cy + max_half)], fill=c_fill)
        d.line(front, fill=c_line, width=2, joint="curve")

        # ── 浪上的音符小珠（压弦青 / 拨弦橙）──
        for ev in events:
            x = ev.get("x", -999)
            if x < 3 or x > W - 3:
                continue
            y = _y(x, ph_f, 1.0, 1.0)
            col = c_org if ev.get("strum") else c_cyan
            d.ellipse([x - 2, y - 2, x + 2, y + 2], fill=col)

    def _draw_bars_wave(self, d, W, events, cy, max_half, th,
                        c_cyan, c_org, c_wht, c_grd):
        """「音浪 + 海浪」合体波形（Z 2026-10-11：音浪要和海浪合体）。

        为什么这么画
        ------------
        * **保留音浪的读法**：一根根对称柱，压弦青 / 拨弦橙，柱顶留亮头 ——
          和原版「音浪」一模一样，一眼仍能看出"哪根是压弦、哪根是拨弦"；
        * **加入海浪的动感**：整片柱子的**上下位置**（柱心）骑在一条行进的正弦
          波上 ⇒ 柱子整体像浮在海面一样起落 —— 这就是"合体"；
        * **相位跟墙钟**（``time.time()``）而非播放进度：界面 5~6Hz 上屏时浪
          依然在连续流动，不会一格一格跳；
        * 柱子背后那条柔和浪线是"水面"，柱心正好落在它上面（视觉上柱子吃水）。
        """
        t = time.time()
        lam = 78.0                       # 浪的波长（px）——比 ocean 宽，起伏更舒缓
        k = 2.0 * math.pi / lam
        ph = t * 1.25                    # 行进相位
        swell = max_half * 0.46          # 柱心随浪起伏的幅度
        bar_max = max_half * 0.66        # 单柱最大半高（留出起伏空间）
        y_top, y_bot = cy - max_half, cy + max_half

        def _cy(x):
            """给定横坐标处的浪面高度（柱心把这条线当"水面"骑）。"""
            return cy - swell * math.sin(k * x + ph)

        c_bg = self._rgb565_to_rgb(int(th.get("bg", 0)))
        c_line = self._rgb565_to_rgb(int(th.get("wave_line", th.get("accent", 0))))
        c_water = self._mix(c_line, c_bg, 0.5)      # 水面：半暗，不抢柱子

        # ① 水面浪线（先画，作为柱子的底）
        pts = [(x, _cy(x)) for x in range(0, W + 1, 4)]
        d.line(pts, fill=c_water, width=2, joint="curve")

        # ② 音浪柱（柱心骑在浪线上；高度照旧由力度决定）
        bar_w = 7
        for ev in events:
            x = ev.get("x", -999)
            if x < -bar_w or x > W + bar_w:
                continue
            lvl = max(0.08, min(1.0, float(ev.get("level", 0.3))))
            h = bar_max * lvl
            c = _cy(min(max(x, 0), W - 1))
            top = max(y_top, c - h)
            bot = min(y_bot, c + h)
            if bot - top < 3:
                continue                                # 贴边裁得太薄就跳过
            col = c_org if ev.get("strum") else c_cyan
            d.rectangle([x - bar_w / 2.0, top, x + bar_w / 2.0 - 1, bot], fill=col)
            d.rectangle([x - bar_w / 2.0, top, x + bar_w / 2.0 - 1, top + 2],
                        fill=c_wht)                     # 柱顶亮头（原版手感）

    # ═════════════ 演奏态接入（覆盖基类的进度条）═════════════
    def _perform_tl(self):
        """取 state 里的整曲时间轴（演奏主界面的唯一数据源）；没有返回 None。

        `perform_timeline` 由**每一条播放入口**在建播放线程时写入 state：
          · web_ui/api.py 的 /api/songs/play —— 管理页点「▶ 演奏」
          · screen_functions.play_song      —— 板载选歌屏按 SET 开弹
           · auto_play_show.py               —— 命令行独立演奏脚本
        同一进程 → 直接共享（见 main.py 里 start_web_server 的调用）。
        """
        try:
            from state import get_state
            tl = get_state("perform_timeline")
        except Exception:
            tl = None
        if tl is None or not getattr(tl, "tl", None):
            return None
        return tl

    def _draw_perform_frame(self, tl, progress):
        """用时间轴 + 进度画一帧 240×240 演奏主界面。成功返回 True。"""
        try:
            total = max(1.0, float(tl.total_ms))
            p = max(0.0, min(100.0, float(progress)))
            t_ms = p / 100.0 * total
            head_x = self._head_x
            events = tl.events_at(t_ms, self._perform_window_ms,
                                  head_x=head_x, scr_w=self._disp.w)
            cur = tl.current_at(t_ms)
            self.show_perform_panel(
                tl.song, p, events,
                chord_now=(cur["name"] if cur and cur.get("name") else "—"),
                frets_now=(cur["frets"] if cur else [0] * 6),
                head_x=head_x,
            )
            return True
        except Exception:
            return False      # 任何异常 → 调用方降级，不影响演奏

    def show_progress(self, song_name, progress):
        """彩屏演奏主界面（覆盖基类 OLED 128×64 老版式）。

        ★ 2026-10-11 修复（用户上报「不是音浪和大字，而是桃红底 + 左上角小字，
          还一闪一闪」）：板载选歌屏按 SET 开弹走的是
          ``screen_functions.play_song → _renderer.show_progress(...)``，
          而基类 ``OLEDRenderer.show_progress`` 是**按 128 宽写死的 1-bit 版式**：

            ① 在 240×240 上只占左上角一小块（歌名/百分比都是小字）；
            ② 1-bit 兜底色一旦不对就整屏染色（见 ``show()`` 的配色修正）；
            ③ 它还 ``clear()`` + ``show()`` **连写两帧全屏** —— 深蓝底一帧、
               内容一帧交替上屏，肉眼就是"一闪一闪"。

          这里改成直接画 240×240 全屏「演奏主界面」（音浪 + 大字歌名 + 和弦
          标签 + 6 弦压弦 + 进度条），**单帧上屏、不闪烁**。
          没有时间轴（找不到曲谱）时退彩屏自带的彩色进度条。
        """
        tl = self._perform_tl()
        if tl is not None and self._draw_perform_frame(tl, progress):
            return
        # 兜底：没曲谱/时间轴 → 彩屏彩色进度条（单帧，不闪）
        self.show_progress_panel(song_name, progress)

    def show_calibration_progress(self, title, progress, status_text):
        """校准/测试/演奏共用的进度界面。

        ★ 演奏时（state 里有 perform_timeline）自动切到「演奏主界面」，
          让**管理页点「▶ 演奏」**和**命令行脚本**看到的是同一个画面；
          校准/测试（没有时间轴）仍走基类的老式进度条。
        """
        tl = self._perform_tl()
        if tl is not None and self._draw_perform_frame(tl, progress):
            return
        super().show_calibration_progress(title, progress, status_text)

    # ══════ 开机启动图（全屏 240×240 · 琴名「JY-演奏」+ 金鱼图标）══════
    # ★ 2026-10-11（用户要求）：
    #   ① 「全屏居中」——彩屏此前共用 OLEDRenderer 的 128×64 版式，那段代码把
    #      标题/进度条按 128 宽写死（居中在 x≈64），搬到 240×240 上就只剩左上角
    #      一小块。这里给彩屏单独写一版 240×240 全屏版式。
    #   ② 「琴名 + 金鱼」——上方琴名 JY-演奏，下方金鱼图标。
    #   ③ 金鱼是**矢量绘制**（三次贝塞尔 + 多边形），不依赖任何外部图片文件：
    #      板端 PIL 直接画 → 不用随包推素材，也不怕素材文件丢失。
    #   ④ 画在 2× 离屏画布上，最后 LANCZOS 缩回 240×240 —— 白拿抗锯齿，
    #      矢量边缘不会有难看的阶梯。
    #   调用契约与 OLEDRenderer.show_boot_screen 完全一致（main.py 无感切换）。
    #   ⚠ 要改名字只改下面一行 _BOOT_NAME 即可（再同步改 _BOOT_FOOTER 里的版本号）。
    _BOOT_NAME = "JY-演奏"
    _BOOT_FOOTER = "自动吉他演奏系统 · v4.3"
    _SS = 2                                    # 超采样倍数（2 → 480×480 画布）
    _BOOT_BG_CACHE = {}                        # (w,h) → 渐变底 + 柔光（只算一次）

    # 金鱼配色（RGB / RGBA）——鳍给足不透明度，半透明太狠会在深底上发灰
    _F_BODY = (255, 128, 32)                   # 鱼身主色（橙）
    _F_BELLY = (255, 212, 142)                 # 腹部亮色
    _F_RIM = (255, 232, 186)                   # 背部高光
    _F_LINE = (172, 56, 0)                     # 轮廓线
    _F_FIN = (255, 152, 36, 235)               # 尾鳍
    _F_FIN2 = (255, 170, 58, 225)              # 背/臀/腹鳍
    _F_PEC = (255, 176, 74)                    # 胸鳍（画在鱼身上，须不透明）
    _F_VEIN = (206, 96, 12, 120)               # 鳍脉
    _F_EYE = (255, 255, 255)
    _F_PUP = (20, 26, 46)
    # 文字 / 进度条
    _TXT_HI = (255, 247, 232)
    _TXT_ST = (150, 208, 236)
    _TXT_DIM = (96, 126, 162)
    _BAR_BG = (24, 40, 68)
    _BAR_FG = (255, 176, 60)

    # ── 曲线工具（金鱼的有机轮廓全靠贝塞尔拼）──
    @staticmethod
    def _bez(p0, p1, p2, p3, n=18):
        """三次贝塞尔采样 n 段，返回 n+1 个点。"""
        pts = []
        for i in range(n + 1):
            t = i / n
            m = 1.0 - t
            a, b, c, e = m * m * m, 3 * m * m * t, 3 * m * t * t, t * t * t
            pts.append((a * p0[0] + b * p1[0] + c * p2[0] + e * p3[0],
                        a * p0[1] + b * p1[1] + c * p2[1] + e * p3[1]))
        return pts

    @staticmethod
    def _chain(*segs):
        """把若干段贝塞尔接成一条折线（自动跳过每段重复的首点）。"""
        pts = []
        for i, seg in enumerate(segs):
            pts.extend(seg if i == 0 else seg[1:])
        return pts

    @classmethod
    def _fish_parts(cls):
        """金鱼各部件的轮廓点列。

        局部坐标系：原点 = 金鱼视觉中心；头朝右（+x），尾朝左（-x）。
        范围约 x ∈ [-76, 72]、y ∈ [-54, 54]（含背鳍尖与下尾鳍）。

        绘制顺序（后画的盖住先画的）：尾鳍/背鳍/臀鳍/腹鳍（半透明，垫在身后）
        → 鱼身 → 腹部 → 背高光 → 轮廓 → 鳃弧 → 胸鳍 → 眼睛 → 嘴。
        """
        B, C = cls._bez, cls._chain
        T = (72.0, 0.0)                                            # 吻端
        body = C(
            B(T, (60.0, -30.0), (20.0, -42.0), (-4.0, -14.0)),      # 上缘：头 → 尾柄
            B((-4.0, 14.0), (20.0, 42.0), (60.0, 30.0), T),         # 下缘：尾柄 → 头
        )
        # 腹部：鱼身整体向中心收缩后下移 → 得到一块贴合下缘的亮色区（无需裁剪）
        bx = sum(p[0] for p in body) / len(body)
        by = sum(p[1] for p in body) / len(body)
        belly = [(bx + (x - bx) * 0.70, by + 8.0 + (y - by) * 0.58) for (x, y) in body]
        # 尾鳍：两片宽叶在尾柄处交叠成扇，尖端分叉（金鱼双尾的辨识点）
        tail_up = C(
            B((-4.0, -8.0), (-34.0, -30.0), (-60.0, -46.0), (-76.0, -40.0)),    # 外缘
            B((-76.0, -40.0), (-84.0, -30.0), (-80.0, -18.0), (-70.0, -16.0)),  # 尖端
            B((-70.0, -16.0), (-46.0, -10.0), (-22.0, -2.0), (-6.0, 3.0)),      # 内缘过中线
        )
        tail_dn = [(x, -y) for (x, y) in tail_up]                  # 下尾鳍 = 上半镜像
        dorsal = C(                                                # 背鳍（后缘落进鱼身，会被盖住）
            B((36.0, -28.0), (28.0, -52.0), (6.0, -58.0), (-4.0, -16.0)),
            [(-4.0, -16.0), (36.0, -28.0)],
        )
        anal = C(                                                  # 臀鳍
            B((18.0, 26.0), (14.0, 44.0), (4.0, 50.0), (-2.0, 20.0)),
            [(-2.0, 20.0), (18.0, 26.0)],
        )
        pelvic = C(                                                # 腹鳍
            B((44.0, 24.0), (40.0, 42.0), (30.0, 46.0), (26.0, 24.0)),
            [(26.0, 24.0), (44.0, 24.0)],
        )
        pec = C(                                                   # 胸鳍（小、贴头下方）
            B((54.0, 8.0), (50.0, 20.0), (38.0, 27.0), (33.0, 22.0)),
            B((33.0, 22.0), (40.0, 16.0), (48.0, 10.0), (54.0, 6.0)),
        )
        gill = B((48.0, -18.0), (36.0, -6.0), (36.0, 4.0), (44.0, 16.0))       # 鳃弧
        rim = B((66.0, 2.0), (54.0, -24.0), (18.0, -34.0), (-2.0, -12.0))      # 背部高光
        mouth = B((62.0, 12.0), (69.0, 12.0), (73.0, 8.0), (73.0, 3.0))        # 嘴
        return {
            "body": body, "belly": belly, "tail_up": tail_up, "tail_dn": tail_dn,
            "dorsal": dorsal, "anal": anal, "pelvic": pelvic, "pec": pec,
            "gill": gill, "rim": rim, "mouth": mouth,
        }

    @classmethod
    def _boot_base(cls, W, H):
        """开机图底：深蓝竖向渐变 + 金鱼背后的柔光。缓存复用（每帧 copy 即可）。"""
        img = cls._BOOT_BG_CACHE.get((W, H))
        if img is not None:
            return img
        img = Image.new("RGB", (W, H), (5, 10, 22))
        d = ImageDraw.Draw(img)
        for y in range(H):                                   # 逐行渐变（只算一次）
            t = y / max(1, H - 1)
            d.line([(0, y), (W, y)],
                   fill=(int(5 + 12 * t), int(11 + 22 * t), int(25 + 40 * t)))
        glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))       # 同心椭圆叠出径向柔光
        gd = ImageDraw.Draw(glow)
        gx, gy, rmax = W // 2, int(H * 0.44), int(W * 0.58)
        for i in range(24):
            k = i / 24.0
            rr = rmax * (1.0 - k)
            gd.ellipse([gx - rr, gy - rr * 0.80, gx + rr, gy + rr * 0.80],
                       fill=(26, 92, 138, int(2 + 12 * k * k)))
        img = Image.alpha_composite(img.convert("RGBA"), glow).convert("RGB")
        cls._BOOT_BG_CACHE[(W, H)] = img
        return img

    def show_boot_screen(self, progress: float, status_text: str = "",
                         wake_word: str = "", boot_frame: int = 0,
                         loading_detail: str = "", show_ready: bool = True):
        """彩屏全屏开机图：琴名「JY-演奏」+ 金鱼图标 + 进度条（240×240 居中）。

        参数与 OLEDRenderer.show_boot_screen 同名同义；``show_ready`` 仅为兼容
        保留（本版式始终显示「进度条 + 状态文字」，不切「系统就绪」页）。
        """
        ss = self._SS
        W, H = self._disp.w * ss, self._disp.h * ss
        ph = (boot_frame % 30) / 30.0          # 0~1 相位（~5FPS → 约 6 秒一轮）
        parts = self._fish_parts()

        # ── 局部坐标 → 屏幕坐标（金鱼局部原点 = 视觉中心）──
        s = 0.90 * ss
        cx, cy = 120.0 * ss, 108.0 * ss
        off = (4.0, 4.0)                       # 把整体视觉重心挪到画面正中

        def P(pts):
            return [((x + off[0]) * s + cx, (y + off[1]) * s + cy) for (x, y) in pts]

        def sway(pts, amp=7.0, pivot=(-2.0, 0.0)):
            """尾 / 鳍绕尾柄枢轴轻微摆动（局部坐标下先旋转、再映射到屏幕）。"""
            rad = math.radians(amp * math.sin(ph * 2 * math.pi))
            ca, sa = math.cos(rad), math.sin(rad)
            px, py = pivot
            out = []
            for x, y in pts:
                dx, dy = x - px, y - py
                out.append((px + dx * ca - dy * sa, py + dx * sa + dy * ca))
            return out

        img = self._boot_base(W, H).copy()

        # ── ① 半透明层：尾鳍 / 背鳍 / 臀鳍 / 腹鳍 / 气泡（先画，鱼身随后盖上去）──
        ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        od = ImageDraw.Draw(ov)
        w_thin = max(1, int(0.9 * ss))

        for lobe, by0 in ((parts["tail_up"], -4.0), (parts["tail_dn"], 4.0)):
            sp = sway(lobe, amp=8.0)
            pts = P(sp)
            od.polygon(pts, fill=self._F_FIN)
            od.line(pts + [pts[0]], fill=self._F_VEIN, width=max(1, int(1.1 * ss)),
                    joint="curve")
            base = P(sway([(-6.0, by0)]))[0]                    # 鳍脉：尾柄 → 外缘
            for k in (0.30, 0.56):
                od.line([base, pts[int(k * len(pts))]], fill=self._F_VEIN, width=w_thin)

        for fin in (parts["dorsal"], parts["anal"], parts["pelvic"]):
            fp = P(sway(fin, amp=3.5))
            od.polygon(fp, fill=self._F_FIN2)
            od.line(fp, fill=self._F_VEIN, width=w_thin)

        # 气泡：贴着鱼头右前方往上升，循环往复（左尾右头 → 右侧留白最干净）
        for bx, spd, r0, p0 in ((186.0, 0.55, 4.0, 0.00),
                                (200.0, 0.72, 3.0, 0.42),
                                (212.0, 0.42, 2.6, 0.75)):
            t = (ph * spd + p0) % 1.0
            a = int(120 * (1.0 - t) ** 0.8)
            if a <= 6:
                continue
            bx_p, by_p, rp = bx * ss, (152.0 - t * 128.0) * ss, r0 * ss
            od.ellipse([bx_p - rp, by_p - rp, bx_p + rp, by_p + rp],
                       fill=(150, 214, 240, a // 3), outline=(186, 230, 252, a),
                       width=w_thin)

        img = Image.alpha_composite(img.convert("RGBA"), ov)

        # ── ② 鱼身（不透明，盖住各鳍的根部）──
        d = ImageDraw.Draw(img)
        d.polygon(P(parts["body"]), fill=self._F_BODY)
        d.polygon(P(parts["belly"]), fill=self._F_BELLY)
        d.line(P(parts["rim"]), fill=self._F_RIM, width=max(2, int(2.2 * ss)), joint="curve")
        body_pts = parts["body"]
        d.line(P(body_pts + [body_pts[0]]), fill=self._F_LINE,
               width=max(1, int(1.4 * ss)), joint="curve")
        d.line(P(parts["gill"]), fill=self._F_LINE,
               width=max(1, int(1.2 * ss)), joint="curve")
        # 胸鳍（覆在鱼身上，必须不透明，否则 alpha 直通会显脏）
        d.polygon(P(parts["pec"]), fill=self._F_PEC)
        d.line(P(parts["pec"] + [parts["pec"][0]]), fill=self._F_LINE,
               width=w_thin, joint="curve")
        d.line(P(parts["mouth"]), fill=self._F_LINE,
               width=max(1, int(1.1 * ss)), joint="curve")

        # ── ③ 眼睛（大眼是金鱼的辨识点）──
        ex, ey = P([(54.0, -9.0)])[0]
        r = 7.4 * s
        d.ellipse([ex - r, ey - r, ex + r, ey + r], fill=self._F_EYE,
                  outline=self._F_LINE, width=w_thin)
        pr = r * 0.52
        d.ellipse([ex - pr, ey - pr, ex + pr, ey + pr], fill=self._F_PUP)
        gr = r * 0.26
        gx2, gy2 = ex - r * 0.30, ey - r * 0.34
        d.ellipse([gx2 - gr, gy2 - gr, gx2 + gr, gy2 + gr], fill=(255, 255, 255))

        # ── ④ 琴名「JY-演奏」──
        f_name = self._ttf(int(32 * ss))
        nw = d.textlength(self._BOOT_NAME, font=f_name)
        d.text(((W - nw) / 2.0, 14 * ss), self._BOOT_NAME, font=f_name, fill=self._TXT_HI)

        # ── ⑤ 状态行：旋转环 + 文字（整体居中）──
        f_st = self._ttf(int(13 * ss))
        st = (status_text or "启动中")[:16]
        tw = d.textlength(st, font=f_st)
        ring, gap = 15.0 * ss, 7.0 * ss
        sx = (W - (ring + gap + tw)) / 2.0
        sy = 165.0 * ss
        rcx, rcy = sx + ring / 2.0, sy + 9.0 * ss
        rr = ring / 2.0 - 1.6 * ss
        a0 = (boot_frame * 26) % 360
        d.arc([rcx - rr, rcy - rr, rcx + rr, rcy + rr], a0, a0 + 250,
              fill=self._TXT_ST, width=max(1, int(1.6 * ss)))
        d.text((sx + ring + gap, sy), st, font=f_st, fill=self._TXT_ST)

        if loading_detail:                                     # AI 加载子阶段（小字）
            f_dt = self._ttf(int(10 * ss))
            dt = loading_detail[:22]
            d.text(((W - d.textlength(dt, font=f_dt)) / 2.0, 179 * ss), dt,
                   font=f_dt, fill=self._TXT_DIM)

        # ── ⑥ 进度条 ──
        bx0, bx1 = 48 * ss, 192 * ss
        by0, by1 = 192 * ss, 201 * ss
        rad = int(4.5 * ss)
        d.rounded_rectangle([bx0, by0, bx1, by1], radius=rad, fill=self._BAR_BG)
        p = max(0.0, min(1.0, float(progress)))
        if p < 1.0:
            fw = (bx1 - bx0) * p
            if fw >= 3:
                d.rounded_rectangle([bx0, by0, bx0 + fw, by1], radius=rad,
                                    fill=self._BAR_FG)
        else:
            # 进度已满但程序仍在初始化 → 一小段高亮来回扫，
            # 明确告诉用户「还在忙」，而不是让他以为卡死了。
            iw = 42 * ss
            tt = (boot_frame % 40) / 40.0
            pos = 1.0 - abs(tt * 2.0 - 1.0)                    # 0→1→0 三角波
            x = bx0 + ((bx1 - bx0) - iw) * pos
            d.rounded_rectangle([x, by0, x + iw, by1], radius=rad, fill=self._BAR_FG)

        # ── ⑦ 页脚 ──
        f_ft = self._ttf(int(10 * ss))
        d.text(((W - d.textlength(self._BOOT_FOOTER, font=f_ft)) / 2.0, 210 * ss),
               self._BOOT_FOOTER, font=f_ft, fill=self._TXT_DIM)

        # ── ⑧ 缩放回 240×240（LANCZOS 抗锯齿）→ 送屏 ──
        out = img.convert("RGB").resize((self._disp.w, self._disp.h), Image.LANCZOS)
        self._flush_p(out)
