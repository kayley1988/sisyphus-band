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

import base64
import io
import math
import struct
import threading
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
        self._seq_core()

    def _seq_core(self):
        """初始化**命令序列**（不含硬件复位）。

        拆出来是为了两处复用：① 开机 `init()`；② `reassert()` 自愈时
        只补发其中"会丢的关键状态"，不必再走一次睡眠/唤醒。
        """
        self._cmd_data(0x11)            # Sleep out
        time.sleep(0.12)
        self._cmd_data(0x36, b"\x00")   # 显示方向（MADCTL）
        self._cmd_data(0x3A, b"\x55")   # RGB565 16bit/px（COLMOD）
        # ★ 0x21 = INVON「显示反转」——不是"泪滴图像"（旧注释写错了）。
        #   1.54″ 这批面板必须开反转才是正常色；丢了它，深色界面会整体
        #   变白、颜色全反（Z 2026-10-11 上报的"屏幕突然变白"就是这个）。
        self._cmd_data(0x21)
        self._cmd_data(0x13)            # 正常显示模式
        self._cmd_data(0x29)            # Display ON
        time.sleep(0.05)

    def reassert(self):
        """**无复位、不碰显示 RAM** 地重发关键状态（防白屏自愈，约 8 字节）。

        用途：面板被干扰复位（RES 瞬断 / 静电）后会回到出厂「反转关闭」态，
        画面变成白底彩纹。MADCTL/COLMOD/INVON/正常模式/显示开 这五条命令
        **都不改变显存内容**，重发后画面保持原样、只是颜色与显示状态回正 ——
        肉眼几乎无感，所以可以每隔几秒无脑补发一次。
        """
        self._cmd_data(0x36, b"\x00")
        self._cmd_data(0x3A, b"\x55")
        self._cmd_data(0x21)
        self._cmd_data(0x13)
        self._cmd_data(0x29)

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
        # ★ 2026-10-11 防白屏自愈（Z：「屏幕突然变成白色」）：
        #   `_last_frame` 留一份最近推屏的 RGB565 字节流 —— 面板万一被复位，
        #   `recover_display()` 重发初始化后可以立刻把这一帧重画回去，
        #   避免"修好了但屏是空白"。
        #   `_spi_lock` 让主循环渲染与 Web 端"修复显示"不会同时往 SPI 灌数据
        #   （两处抢屏必花，见 main.py 里同类注释）。
        self._last_frame = None
        self._spi_lock = threading.Lock()
        self._last_reassert = time.monotonic()
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
        """★ 2026-10-11 改为**空操作**（闪屏机制根治）。

        旧实现是「立刻推一整帧底色到屏」。而 OLED 继承来的老版式页面
        （`show_main_screen` / `show_message` / 各种确认屏）普遍写成
        `self.clear()` → 画图 → `self.show(img)`：于是**每次重绘都先闪一帧
        纯底色**，主界面/提示条每 0.15~0.3s 重绘一次 → 就是 Z 报的"一直闪屏"。

        彩屏自己的页面一律「离屏画满 → 一次 `_flush_p`」，本来就不需要先清屏；
        真的要把整屏刷成底色时，用 `clear_hard()`。
        """
        return

    def clear_hard(self):
        """显式清屏（推一整帧底色）——只在确实需要"黑屏"时调用。"""
        if self.dry_run or self._disp is None:
            return
        buf = self._bg_frame()
        with self._spi_lock:
            self._last_frame = buf
            self._disp.write_frame(buf)

    def _bg_frame(self) -> bytes:
        """一整屏底色的 RGB565 字节流（240×240×2）。"""
        v = self._t565("bg")
        hi, lo = (v >> 8) & 0xFF, v & 0xFF
        return bytes((hi, lo)) * (self._disp.w * self._disp.h)

    # ═════════════ 显示自愈（Z 2026-10-11：白屏"根治办法"）═════════════
    _REASSERT_EVERY_S = 4.0     # 无感补发关键状态的周期（秒）

    def display_selfcheck(self, now: float = None) -> bool:
        """主循环每 tick 调一次：到点就**无复位、无闪烁**地补发关键状态。

        为什么这样就够：面板被复位后能"看起来坏掉"的直接原因就是丢了
        反转/显示状态；这几条命令不动显存，每隔几秒补一次，颜色类异常
        最多 4 秒自动回正，用户无感。真正的整屏乱码（显存也丢了）另由
        `recover_display()` 兜底（按键时重绘 / Web 一键修复）。
        """
        if self.dry_run or getattr(self, "_disp", None) is None:
            return False
        t = time.monotonic() if now is None else now
        if t - self._last_reassert < self._REASSERT_EVERY_S:
            return False
        self._last_reassert = t
        try:
            with self._spi_lock:
                self._disp.reassert()
            return True
        except Exception:
            return False

    def recover_display(self, reason: str = "manual") -> bool:
        """屏幕修复：**硬件复位 + 完整初始化 + 重画上一帧**。

        用于「已经白屏/花屏」的场景（本地或 Web 触发）。因为要重发
        `init()`（含复位脉冲），调用后会有一瞬间的闪，但立刻补上上一帧内容。
        返回是否成功。
        """
        if self.dry_run or getattr(self, "_disp", None) is None:
            return False
        try:
            with self._spi_lock:
                self._disp.init()
                time.sleep(0.05)
                fr = self._last_frame or self._bg_frame()
                self._disp.write_frame(fr)
                self._last_frame = fr
                self._last_reassert = time.monotonic()
            self._log_info("[lcd] 屏幕已重新初始化并重绘（reason=%s）", reason)
            return True
        except Exception as e:
            self._log_info("[lcd] 屏幕恢复失败（reason=%s）: %s", reason, e)
            return False

    @staticmethod
    def _log_info(fmt, *args):
        """懒加载日志：lcd_renderer 要在无 logger 的预览/测试环境里也能 import。"""
        try:
            from logger import get_logger
            get_logger("guitar.lcd").info(fmt, *args)
        except Exception:
            pass

    def show(self, img):
        """1-bit 图 → RGB565 → SPI。img 为 None（clear-only）时直接刷底色。"""
        if self.dry_run or self._disp is None:
            return
        if img is None:
            self.clear_hard()
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
        payload = bytes(out)
        with self._spi_lock:
            self._last_frame = payload
            self._disp.write_frame(payload)

    def show_rgb565_frame(self, frame: bytes):
        """直接送一帧 240×240×2 的 RGB565（做彩色 UI 时用，如和弦图）。"""
        if not self.dry_run:
            with self._spi_lock:
                self._last_frame = bytes(frame)
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
            payload = v.astype(">u2").tobytes()             # big-endian = MSB first
        except Exception:
            px = list(rgb.getdata())
            buf = bytearray(len(px) * 2)
            for i, (r, g, b) in enumerate(px):
                v = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
                buf[i * 2] = (v >> 8) & 0xFF        # 高字节
                buf[i * 2 + 1] = v & 0xFF           # 低字节
            payload = bytes(buf)
        with self._spi_lock:
            self._last_frame = payload      # 供白屏自愈时重画
            self._disp.write_frame(payload)

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

    # ═════════════ 提示条 / 主界面兜底（彩屏版，Z 2026-10-11）═════════════
    # 背景：彩屏此前**没有覆盖** OLED 的 show_message / show_main_screen，
    # 于是这两页走 128×64 老版式 —— 黑底白小字，且因为"clear() + show()"
    # 两连击每帧闪一次。Z 看到的就是这个（"一直闪屏、白色小字、和以前一样"）。
    # 现在按彩屏口径重写：大字、主题配色、**只推一帧**。

    _EMOJI_RANGES = (
        (0x1F000, 0x1FAFF), (0x2600, 0x27BF), (0x2B00, 0x2BFF),
        (0xFE00, 0xFE0F), (0x1F1E6, 0x1F1FF),
    )

    @classmethod
    def _clean_text(cls, s) -> str:
        """去掉常见 emoji/变体选择符：面板字体没有 emoji 字形，留着只会变"方块"。"""
        out = []
        for ch in str(s or ""):
            cp = ord(ch)
            if any(lo <= cp <= hi for lo, hi in cls._EMOJI_RANGES):
                out.append(" ")
            else:
                out.append(ch)
        return "".join(out).strip()

    @staticmethod
    def _wrap(d, text, font, max_w, max_lines=2):
        """按像素宽度折行（中英混排），最多 max_lines 行，末行超长补「…」。"""
        text = str(text or "")
        if not text:
            return []
        lines, cur = [], ""
        for ch in text:
            if ch == "\n":
                lines.append(cur)
                cur = ""
                continue
            if d.textlength(cur + ch, font=font) <= max_w:
                cur += ch
            else:
                lines.append(cur)
                cur = ch
                if len(lines) >= max_lines:
                    break
        if len(lines) < max_lines and cur:
            lines.append(cur)
        joined = "".join(lines)
        if len(joined) < len(text):          # 有截断 → 末行补省略号
            tail = lines[-1] if lines else ""
            lines = lines[:-1] + [tail[:max(0, len(tail) - 1)] + "…"] if lines else lines
        return lines[:max_lines]

    def show_message(self, text_lines, duration=None, hint=""):
        """彩屏提示条（覆盖 OLED 小字版）：居中大字 + 底部提示，只推一帧。"""
        img = self._new_canvas()
        d = ImageDraw.Draw(img)
        W, H = self._disp.w, self._disp.h
        c_txt = self._rgb("_C_WHT")
        c_txt2 = self._rgb("_C_TXT2")
        c_title = self._rgb("_C_YEL")

        raw = [self._clean_text(x) for x in (text_lines or [])]
        lines = [x for x in raw if x][:4] or ["—"]

        area_top, area_bot = 34, 196
        max_w = W - 30
        # 依次试字号，取第一个"宽度放得下 + 总高放得下"的（中文竖排不会挤）
        font = self._ttf(16)
        for size in (30, 26, 23, 20, 17, 15):
            f = self._ttf(size)
            lh = int(size * 1.35)
            if all(d.textlength(t, font=f) <= max_w for t in lines) and \
                    lh * len(lines) <= (area_bot - area_top):
                font = f
                break
        lh = int(font.size * 1.35) if hasattr(font, "size") else 26
        y = area_top + max(0, (area_bot - area_top - lh * len(lines)) // 2)
        for t in lines:
            d.text(((W - d.textlength(t, font=font)) / 2.0, y), t, font=font, fill=c_txt)
            y += lh

        tip = self._clean_text(hint)
        if tip:
            f_h = self._ttf(13)
            d.text(((W - d.textlength(tip, font=f_h)) / 2.0, H - 24), tip,
                   font=f_h, fill=c_txt2)
        d.line([40, 26, W - 40, 26], fill=c_title, width=2)   # 顶部短装饰线
        self._flush_p(img)
        if duration:
            time.sleep(duration)

    _AI_STATE_TEXT = {
        "IDLE": "待机", "LISTENING": "聆听中", "RECORDING": "聆听中",
        "THINKING": "思考中", "SPEAKING": "说话中", "LOADING": "加载中",
        "UNAVAILABLE": "语音不可用",
    }

    def show_main_screen(self, main_info: dict, marquee_offset: int = 0):
        """彩屏主界面兜底（覆盖 OLED 小字版）：顶栏传感器 + 中部语音状态。

        只在「她的演奏」未装配 / 非彩屏和弦面板路径时用到，但**必须**是
        彩屏版式 —— 否则又回到"黑底小白字每帧闪"的老样子。
        所有字段都按 `main_info.get(...)` 取，缺字段不崩。
        """
        info = main_info or {}
        img = self._new_canvas()
        d = ImageDraw.Draw(img)
        W, H = self._disp.w, self._disp.h
        c_txt = self._rgb("_C_WHT")
        c_txt2 = self._rgb("_C_TXT2")
        c_title = self._rgb("_C_YEL")
        c_dim = self._rgb("_C_DIM")

        # ── 顶栏：电压 / 温度 / WiFi ──
        f_top = self._ttf(15)
        try:
            bat = "%.1fV" % float(info.get("battery_v") or 0)
            tmp = "%.1f°C" % float(info.get("temperature") or 0)
        except Exception:
            bat, tmp = "--", "--"
        sig = str(info.get("wifi_signal") or "--")
        d.text((10, 10), bat, font=f_top, fill=c_txt2)
        d.text(((W - d.textlength(tmp, font=f_top)) / 2.0, 10), tmp, font=f_top, fill=c_txt2)
        d.text((W - 10 - d.textlength(sig, font=f_top), 10), sig, font=f_top, fill=c_txt2)
        d.line([0, 34, W, 34], fill=c_dim, width=1)

        # ── 中部：语音状态大字 + 一句用户话 / 回话 ──
        state_raw = str(info.get("ai_state_raw") or "IDLE")
        big = self._AI_STATE_TEXT.get(state_raw, "待机")
        if state_raw == "UNAVAILABLE":
            err = self._clean_text(info.get("ai_error")) or "语音功能不可用"
            big = err
        f_big = self._ttf(30)
        big = self._fit(d, big, f_big, W - 30)
        d.text(((W - d.textlength(big, font=f_big)) / 2.0, 62), big,
               font=f_big, fill=c_txt)

        y = 112
        msg_bottom = H - 72          # 底部留给 和弦/提示/IP，消息不许压过来
        f_msg = self._ttf(16)
        for tag, key, col in (("我说", "ai_user_text", c_txt2),
                              ("她说", "ai_reply_text", c_title)):
            txt = self._clean_text(info.get(key))
            if not txt:
                continue
            budget = max(0, (msg_bottom - y) // 22)   # 剩余高度还能放几行
            if budget <= 0:
                break
            lines = self._wrap(d, txt, f_msg, W - 60, max_lines=min(2, budget))
            for i, ln in enumerate(lines):
                prefix = "%s " % tag if i == 0 else "    "
                d.text((14, y), prefix + ln, font=f_msg, fill=col)
                y += 22
            y += 2

        # ── 底部：和弦 / 提示 / IP ──
        chord = self._clean_text(info.get("her_chord"))
        hint = self._clean_text(info.get("her_hint"))
        ip = str(info.get("device_ip") or "")
        f_bot = self._ttf(14)
        if chord:
            d.text((12, H - 62), self._fit(d, chord, f_bot, W - 24), font=f_bot, fill=c_txt)
        if hint:
            d.text((12, H - 40), self._fit(d, hint, f_bot, W - 24), font=f_bot, fill=c_title)
        foot = ip if ip else "长按 SET 进设置"
        d.text((12, H - 20), self._fit(d, foot, self._ttf(12), W - 24),
               font=self._ttf(12), fill=c_dim)
        self._flush_p(img)

    def show_menu(self, items, selected_index, title=None, hint=""):
        """彩屏设置菜单（240×240 大字版）—— 覆盖 OLED 的 128×64 版式。

        ★ 2026-10-11（Z 反馈"设置界面字小挤左上角 + 疯狂闪屏"）：
          此前彩屏没有覆盖 show_menu，直接走 OLED 版式 —— 坐标写死 0~127、
          字体 12px，铺到 240×240 上就是「字极小、全挤左上角」；
          且 OLED 版首行 `self.clear()` 在本类里是**立刻推一整帧底色**，
          随后再推内容帧，于是每次重绘都有"底色一闪"，连续重绘即成"疯狂闪屏"。

        本实现：直接在离屏画布上画满「底色 + 标题 + 列表 + 提示」，
        **全程只 `_flush_p` 推一帧**（不碰 clear），天然无闪。
        版式与 show_song_list 保持一致：标题栏 + 5 行滚动列表 + 底部按键提示。
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
        f_row = self._ttf(19)
        f_sel = self._ttf(21)

        rows = list(items or [])
        n = len(rows)

        # ── 标题栏 ──
        d.text((10, 8), title or "设置", font=f_title, fill=c_yel)
        if n:
            cnt = "%d/%d" % (max(1, min(int(selected_index) + 1, n)), n)
            d.text((W - 10 - d.textlength(cnt, font=f_cnt), 14), cnt,
                   font=f_cnt, fill=c_txt2)
        d.line([0, 40, W, 40], fill=c_dim, width=2)

        ROWS, ROW_H, TOP = 5, 32, 46
        TXT_X, TXT_MAXW = 30, 186        # 右侧留 ~22px 给"还有更多"小三角

        if not n:
            d.text((10, 100), "（本页无项目）", font=f_row, fill=c_txt2)
        else:
            sel = max(0, min(int(selected_index), n - 1))
            start = max(0, min(sel - ROWS // 2, n - ROWS))
            for r in range(ROWS):
                i = start + r
                if i >= n:
                    break
                y = TOP + r * ROW_H
                raw = rows[i]
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
        tip = hint or "上/下 选   SET 进   ESC 退"
        f_hint = self._ttf(13)
        if d.textlength(tip, font=f_hint) > W - 12:
            f_hint = self._ttf(11)
        d.text(((W - d.textlength(tip, font=f_hint)) // 2, 218), tip,
               font=f_hint, fill=c_txt2)
        self._flush_p(img)

    # ════════ 剩余 OLED 页面 → 彩屏版（Z 2026-10-11 批量归位）════════
    # 背景：彩屏此前只覆盖了部分页面，其余仍走 OLED 的 128×64 版式
    # （坐标写死 0~127 + 12px 小字 + clear()/show() 两连击每帧闪）。
    # Z 每点进一个就报一次"黑底小白字"。这里把剩余页面一次性补齐，
    # 统一口径：离屏画满 + 只 _flush_p 推一帧 + 主题大字。

    def _page_title(self, d, W, title, right=""):
        """彩屏页面通用标题栏：左侧标题（金）+ 右侧小字 + 分隔线。"""
        c_yel = self._rgb("_C_YEL")
        c_txt2 = self._rgb("_C_TXT2")
        c_dim = self._rgb("_C_DIM")
        d.text((12, 8), self._fit(d, self._clean_text(title), self._ttf(20), W - 90),
               font=self._ttf(20), fill=c_yel)
        if right:
            f_r = self._ttf(14)
            d.text((W - 12 - d.textlength(str(right), font=f_r), 14), str(right),
                   font=f_r, fill=c_txt2)
        d.line([0, 40, W, 40], fill=c_dim, width=2)

    def _page_hint(self, d, W, H, text, fill=None):
        """彩屏页面底部居中小提示。"""
        c_txt2 = fill if fill is not None else self._rgb("_C_TXT2")
        t = self._clean_text(text)
        f = self._ttf(13)
        if d.textlength(t, font=f) > W - 12:
            f = self._ttf(11)
        d.text(((W - d.textlength(t, font=f)) / 2.0, H - 24), t, font=f, fill=c_txt2)

    # ── 电池图形的量程刻度（Z 2026-10-11：这页要"画几个电池的图形"）──
    #   改这几个常数就能调各条电池的"空/满"位置，画法不用动。
    _BATT_PACK_MIN, _BATT_PACK_MAX = 1.5, 4.5    # 1S 锂电可信区间（与固件判定一致）
    _BATT_V_EMPTY, _BATT_V_FULL = 3.0, 4.2       # 电压条：3.0V=空、4.2V=满
    _BATT_I_FULL = 3.0                           # 电流条：MAX471 模块 ±3A → 3A 满格
    _BATT_T_MIN, _BATT_T_FULL = 0.0, 60.0        # 温度条：0~60°C 映射满格

    @staticmethod
    def _batt_pct(v, lo, hi):
        """数值 → 0.0~1.0 的电量比例（超范围夹到 0/1；无效值给 0）。"""
        try:
            f = float(v)
        except (TypeError, ValueError):
            return 0.0
        if f != f or hi <= lo:          # NaN / 退化量程
            return 0.0
        return max(0.0, min(1.0, (f - lo) / (hi - lo)))

    @staticmethod
    def _batt_body(d, x0, y0, x1, y1, pct, col, empty, radius=3, nub=8, cap=5):
        """画一枚电池：外壳 + 正极凸起 + 按 pct 填充电量条。

        nub > 0 → 竖向电池（凸起在顶、电量从下往上长）；
        nub < 0 → 横向电池（凸起在右、电量从左往右长）。
        内腔先描一圈暗框，这样 0% 时也一眼看得出是"空电池"。
        """
        d.rounded_rectangle([x0, y0, x1, y1], radius=radius, outline=col, width=2)
        if nub > 0:
            w = max(6, int((x1 - x0) * 0.42))
            nx = (x0 + x1 - w) // 2
            d.rectangle([nx, y0 - cap, nx + w, y0], fill=col)
        elif nub < 0:
            h = max(6, int((y1 - y0) * 0.42))
            ny = (y0 + y1 - h) // 2
            d.rectangle([x1, ny, x1 + cap, ny + h], fill=col)
        ix0, iy0, ix1, iy1 = x0 + 3, y0 + 3, x1 - 3, y1 - 3
        p = max(0.0, min(1.0, float(pct)))
        if ix1 > ix0 and iy1 > iy0:
            if nub > 0:
                fh = int((iy1 - iy0) * p)
                if fh > 0:
                    d.rectangle([ix0, iy1 - fh, ix1, iy1], fill=col)
            else:
                fw = int((ix1 - ix0) * p)
                if fw > 0:
                    d.rectangle([ix0, iy0, ix0 + fw, iy1], fill=col)
        d.rectangle([ix0, iy0, ix1, iy1], outline=empty, width=1)

    def show_power_monitor(self, voltage, chord_curr, strum_curr, temp):
        """彩屏电源监控（覆盖 OLED 小字版）：**电池图形** + 越界读数异常标注。

        ★ 数据是真读、不是 mock：由 `power_monitor.PowerMonitor` 经串口从
          ESP32 的 ADC 实时读取（V3 二进制帧 50ms 推一次），每次采样都在小幅波动。
          本板 board_id=3（XIAOZHI 小智板）电流走外接 MAX471 模块。
        ★ Z 2026-10-11：这页改成"画几个电池的图形"——上面一枚大横电池画电池电压
          （电量百分比直接印在电池里），下面三格小电池条画 和弦/拨弦电流、板温；
          每条的"空/满"刻度见 `_BATT_*` 常数。
        ★ 若某路传感器没接（电池分压 / NTC 未焊），读到的就是"悬空≈0"的电压，
          换算后得到 0.2V / -34°C 这类不合理值 —— 这里对明显越界的读数画
          「暗色空电池 + 星号」，免得把垃圾读数当成真实电量。
        """
        img = self._new_canvas()
        d = ImageDraw.Draw(img)
        W, H = self._disp.w, self._disp.h
        c_wht = self._rgb("_C_WHT")
        c_txt2 = self._t("txt2")
        c_dim = self._t("dim")
        c_red = self._rgb("_C_HI")
        c_v = self._t("accent")      # 电池电压：绿（低于 16% → 红）
        c_cc = self._t("press")      # 和弦电流：青
        c_sc = self._t("strum")      # 拨弦电流：橙
        c_t = self._t("title")       # 板温：黄

        self._page_title(d, W, "电源管理与监控")

        def _f(v):
            if v is None:
                return None
            try:
                f = float(v)
            except (TypeError, ValueError):
                return None
            return None if f != f else f          # NaN 当无效

        def _fmt(f, spec):
            return "--" if f is None else format(f, spec)

        fv, fcc, fsc, ft = _f(voltage), _f(chord_curr), _f(strum_curr), _f(temp)

        # 越界 = 该路可能未接传感器 / 未标定（电压按 1S 锂电可信区间判定）
        v_bad = fv is not None and not (self._BATT_PACK_MIN <= fv <= self._BATT_PACK_MAX)
        cc_bad = fcc is not None and not (0.0 <= fcc <= self._BATT_I_FULL * 1.5)
        sc_bad = fsc is not None and not (0.0 <= fsc <= self._BATT_I_FULL * 1.5)
        t_bad = ft is not None and not (self._BATT_T_MIN - 10.0 <= ft <= 95.0)

        v_pct = self._batt_pct(fv, self._BATT_V_EMPTY, self._BATT_V_FULL)
        cc_pct = self._batt_pct(fcc, 0.0, self._BATT_I_FULL)
        sc_pct = self._batt_pct(fsc, 0.0, self._BATT_I_FULL)
        t_pct = self._batt_pct(ft, self._BATT_T_MIN, self._BATT_T_FULL)

        # ── ① 电池电压：一枚大横电池，电量百分比直接印在电池里 ──
        v_col = c_dim if v_bad else (c_red if v_pct < 0.16 else c_v)
        d.text((14, 46), "电池电压", font=self._ttf(16), fill=c_txt2)
        self._batt_body(d, 14, 70, 150, 118, v_pct, v_col, c_dim,
                        radius=5, nub=-1, cap=10)
        if v_bad:
            inside = "异常"
        elif fv is None:
            inside = "--"
        else:
            inside = "%d%%" % round(v_pct * 100)
        isize = 28
        f_in = self._ttf(isize)
        while isize > 16 and d.textlength(inside, font=f_in) > 112:
            isize -= 2
            f_in = self._ttf(isize)
        d.text(((14 + 150 - d.textlength(inside, font=f_in)) / 2.0,
                94 - isize * 0.62), inside, font=f_in,
               fill=(c_dim if v_bad else c_wht))
        v_txt = _fmt(fv, ".2f") + ("*" if v_bad else "")
        vsize = 26
        f_v = self._ttf(vsize)
        while vsize > 15 and d.textlength(v_txt, font=f_v) > 52:
            vsize -= 2
            f_v = self._ttf(vsize)
        d.text((162, 78), v_txt, font=f_v, fill=(c_dim if v_bad else c_v))
        d.text((162 + d.textlength(v_txt, font=f_v) + 3, 88), "V",
               font=self._ttf(14), fill=c_txt2)

        # ── ② 和弦电流 / 拨弦电流 / 板温：三格小电池条 ──
        rows = [
            ("和弦电流", _fmt(fcc, ".3f"), "A", c_cc, cc_bad, cc_pct),
            ("拨弦电流", _fmt(fsc, ".3f"), "A", c_sc, sc_bad, sc_pct),
            ("板温", _fmt(ft, ".1f"), "°C", c_t, t_bad, t_pct),
        ]
        f_lab = self._ttf(14)
        for k, (lab, val, unit, col, bad, pct) in enumerate(rows):
            ry = 124 + k * 30
            col2 = c_dim if bad else col
            d.text((14, ry + 3), lab, font=f_lab, fill=c_txt2)
            self._batt_body(d, 76, ry + 7, 166, ry + 27, pct, col2, c_dim,
                            radius=3, nub=-1, cap=6)
            shown = val + ("*" if bad else "") + unit
            rsize = 15                      # 数值右对齐；太宽（如 -34.6*°C）逐级缩
            f_r = self._ttf(rsize)
            while rsize > 11 and d.textlength(shown, font=f_r) > 58:
                rsize -= 1
                f_r = self._ttf(rsize)
            d.text((236 - d.textlength(shown, font=f_r), ry + 4), shown,
                   font=f_r, fill=col2)

        if v_bad or t_bad or cc_bad or sc_bad:
            self._page_hint(d, W, H, "* 读数异常 → 该路未接传感器/未标定", fill=c_dim)
        else:
            self._page_hint(d, W, H, "ESC 返回")
        self._flush_p(img)

    def show_confirm_dialog(self, song_name, selected_option):
        """彩屏演奏确认弹窗（覆盖 OLED 小字版）。"""
        img = self._new_canvas()
        d = ImageDraw.Draw(img)
        W, H = self._disp.w, self._disp.h
        c_wht = self._rgb("_C_WHT")
        c_txt2 = self._rgb("_C_TXT2")
        c_hi = self._rgb("_C_HI")
        c_dim = self._rgb("_C_DIM")

        self._page_title(d, W, "演奏确认")
        d.text((16, 54), "是否演奏这首？", font=self._ttf(18), fill=c_txt2)
        f_name = self._ttf(28)
        name = self._clean_text(song_name) or "未知歌曲"
        d.text((16, 82), self._fit(d, name, f_name, W - 32), font=f_name, fill=c_wht)

        options = ["是（开始演奏）", "否（返回）"]
        f_o = self._ttf(20)
        try:
            sel = int(selected_option)
        except (TypeError, ValueError):
            sel = 0
        for i, opt in enumerate(options):
            y = 132 + i * 44
            if i == sel:
                d.rectangle([12, y, W - 12, y + 38], fill=c_hi)
                d.text((26, y + 6), opt, font=f_o, fill=c_wht)
            else:
                d.rectangle([12, y, W - 12, y + 38], outline=c_dim, width=2)
                d.text((26, y + 6), opt, font=f_o, fill=c_txt2)
        self._page_hint(d, W, H - 6, "UP/DOWN 选   SET 确认   ESC 取消")
        self._flush_p(img)

    def show_voice_download_confirm(self, song_name: str, countdown: float):
        """彩屏「该歌曲未下载」确认框（覆盖 OLED 小字版）。"""
        img = self._new_canvas()
        d = ImageDraw.Draw(img)
        W, H = self._disp.w, self._disp.h
        c_wht = self._rgb("_C_WHT")
        c_txt2 = self._rgb("_C_TXT2")
        c_hi = self._rgb("_C_HI")
        c_yel = self._rgb("_C_YEL")
        c_dim = self._rgb("_C_DIM")

        self._page_title(d, W, "未下载的歌曲")
        d.text((16, 54), "该歌曲尚未下载：", font=self._ttf(17), fill=c_txt2)
        f_name = self._ttf(26)
        name = self._clean_text(song_name) or "未知歌曲"
        d.text((16, 80), self._fit(d, name, f_name, W - 32), font=f_name, fill=c_wht)

        f_o = self._ttf(20)
        d.text((20, 134), "UP   确认下载", font=f_o, fill=c_hi)
        d.text((20, 170), "DOWN 取消", font=f_o, fill=c_txt2)

        sec = max(0, int(countdown))
        ctd = "%d 秒后自动取消" % sec
        f_c = self._ttf(14)
        d.text(((W - d.textlength(ctd, font=f_c)) / 2.0, H - 44), ctd, font=f_c, fill=c_yel)
        d.line([30, H - 50, W - 30, H - 50], fill=c_dim, width=1)
        self._flush_p(img)

    def show_session_summary(self, page, page_index: int = 0,
                             page_total: int = 1, hint: str = ""):
        """彩屏演奏小结（覆盖 OLED 小字版）：标题 + 大字正文 + 右上页码。"""
        img = self._new_canvas()
        d = ImageDraw.Draw(img)
        W, H = self._disp.w, self._disp.h
        c_txt = self._rgb("_C_WHT")
        c_txt2 = self._rgb("_C_TXT2")

        page = page or {}
        title = self._clean_text(page.get("title")) or "演奏小结"
        lines = [self._clean_text(x) for x in (page.get("lines") or [])]
        lines = [x for x in lines if x]

        right = ""
        if page_total > 1:
            right = "%d/%d" % (page_index + 1, page_total)
        self._page_title(d, W, title, right)

        f = self._ttf(20)
        max_lines = 4 if not hint else 3
        y = 56
        for ln in lines[:max_lines]:
            d.text((16, y), self._fit(d, ln, f, W - 32), font=f, fill=c_txt)
            y += 40
        if not lines:
            d.text((16, 100), "（暂无内容）", font=f, fill=c_txt2)
        if hint:
            self._page_hint(d, W, H, hint)
        self._flush_p(img)

    def show_tuner(self, display_text, target_string, cents, locked):
        """彩屏调音器（覆盖 OLED 小字版）：大字音名 + 加宽音分条。"""
        img = self._new_canvas()
        d = ImageDraw.Draw(img)
        W, H = self._disp.w, self._disp.h
        c_txt = self._rgb("_C_WHT")
        c_txt2 = self._rgb("_C_TXT2")
        c_yel = self._rgb("_C_YEL")
        c_hi = self._rgb("_C_HI")
        c_txtg = self._rgb("_C_TXT")

        self._page_title(d, W, "吉他调音器", target_string or "")
        f_big = self._ttf(46)
        txt = self._clean_text(display_text) or "--"
        d.text(((W - d.textlength(txt, font=f_big)) / 2.0, 58), txt, font=f_big, fill=c_yel)

        bar_x, bar_w, bar_y, bar_h = 20, W - 40, 140, 20
        center_x = bar_x + bar_w // 2
        d.rectangle([bar_x, bar_y, bar_x + bar_w, bar_y + bar_h], outline=c_txt2, width=2)
        d.line([center_x, bar_y - 4, center_x, bar_y + bar_h + 4], fill=c_txt, width=2)
        try:
            cents_v = float(cents)
        except (TypeError, ValueError):
            cents_v = 0.0
        clamped = max(-50.0, min(50.0, cents_v))
        pointer_x = int(center_x + clamped * (bar_w / 2.0 - 6) / 50.0)
        pointer_x = max(bar_x + 4, min(bar_x + bar_w - 4, pointer_x))
        d.rectangle([pointer_x - 4, bar_y, pointer_x + 4, bar_y + bar_h], fill=c_hi)

        if locked:
            status, sc = "已锁定", c_txt
        elif abs(cents_v) < 3:
            status, sc = "✓ 准确", c_txtg
        elif cents_v > 3:
            status, sc = "偏高 ↑", c_hi
        elif cents_v < -3:
            status, sc = "偏低 ↓", c_hi
        else:
            status, sc = "搜索中…", c_txt2
        f_s = self._ttf(20)
        d.text(((W - d.textlength(status, font=f_s)) / 2.0, 182), status, font=f_s, fill=sc)
        self._page_hint(d, W, H, "ESC 退出")
        self._flush_p(img)

    def show_wifi_info(self, wifi_info, signal=None, hint=""):
        """彩屏 WiFi 信息页（覆盖 OLED 小字版）。"""
        img = self._new_canvas()
        d = ImageDraw.Draw(img)
        W, H = self._disp.w, self._disp.h
        c_txt = self._rgb("_C_WHT")
        c_txt2 = self._rgb("_C_TXT2")

        self._page_title(d, W, "WiFi 信息", hint or "")
        if wifi_info:
            f_l = self._ttf(15)
            f_v = self._ttf(21)
            rows = [
                ("已连接", str(wifi_info.get("ssid", "") or "-")),
                ("IP 地址", str(wifi_info.get("ip_address", "N/A") or "N/A")),
                ("接口", str(wifi_info.get("interface", "") or "-")),
                ("信号强度", ("%s%%" % signal) if signal else "-"),
            ]
            y = 48
            for lab, val in rows:
                d.text((16, y), lab, font=f_l, fill=c_txt2)
                d.text((16, y + 17), self._fit(d, val, f_v, W - 32), font=f_v, fill=c_txt)
                y += 42
        else:
            f_big = self._ttf(26)
            t = "WiFi 未连接"
            d.text(((W - d.textlength(t, font=f_big)) / 2.0, 108), t, font=f_big, fill=c_txt2)
        self._page_hint(d, W, H, "ESC 返回")
        self._flush_p(img)

    def show_wifi_scan(self, networks, selected_index, hint=""):
        """彩屏 WiFi 扫描列表（覆盖 OLED 小字版）。"""
        img = self._new_canvas()
        d = ImageDraw.Draw(img)
        W, H = self._disp.w, self._disp.h
        c_wht = self._rgb("_C_WHT")
        c_txt2 = self._rgb("_C_TXT2")
        c_hi = self._rgb("_C_HI")
        c_dim = self._rgb("_C_DIM")

        nets = list(networks or [])
        self._page_title(d, W, "选择 WiFi 网络", hint or "")

        if not nets:
            d.text((16, 100), "未找到网络", font=self._ttf(22), fill=c_txt2)
            self._page_hint(d, W, H, "ESC 返回")
            self._flush_p(img)
            return

        f_row = self._ttf(17)
        f_sig = self._ttf(15)
        ROWS, ROW_H, TOP = 5, 34, 48
        try:
            sel = int(selected_index)
        except (TypeError, ValueError):
            sel = 0
        sel = max(0, min(sel, len(nets) - 1))
        start = max(0, min(sel - ROWS // 2, len(nets) - ROWS))
        for r in range(ROWS):
            i = start + r
            if i >= len(nets):
                break
            net = nets[i] or {}
            y = TOP + r * ROW_H
            ssid = str(net.get("ssid", "") or "?")
            sig = "%s%%" % net.get("signal", 0)
            if i == sel:
                d.rectangle([6, y, W - 6, y + ROW_H - 4], fill=c_hi)
                col = c_wht
            else:
                col = c_txt2
            d.text((14, y + 4), self._fit(d, ssid, f_row, W - 100), font=f_row, fill=col)
            d.text((W - 16 - d.textlength(sig, font=f_sig), y + 6), sig, font=f_sig, fill=col)
        # 「还有更多」并入底部提示（右侧信号列要占满，不另画三角免得压字）
        more = ""
        if start > 0:
            more += "▲ "
        if start + ROWS < len(nets):
            more += "▼ "
        d.line([0, H - 30, W, H - 30], fill=c_dim, width=1)
        self._page_hint(d, W, H, (more + "UP/DOWN 选   SET 连接   ESC 返回").strip())
        self._flush_p(img)

    def show_scanning(self, frame, hint=""):
        """彩屏「扫描中」动画页（覆盖 OLED 小字版）。"""
        img = self._new_canvas()
        d = ImageDraw.Draw(img)
        W, H = self._disp.w, self._disp.h
        c_txt = self._rgb("_C_WHT")
        c_txt2 = self._rgb("_C_TXT2")

        f_big = self._ttf(28)
        try:
            frm = int(frame)
        except (TypeError, ValueError):
            frm = 0
        dots = "." * ((frm % 4) + 1)
        t = "扫描 WiFi 中" + dots
        d.text(((W - d.textlength(t, font=f_big)) / 2.0, 104), t, font=f_big, fill=c_txt)
        # 底部进度点（不依赖字体字形，自己画）
        n, r = 5, 6
        gap = 26
        x0 = (W - (n - 1) * gap) // 2
        active = frm % n
        for k in range(n):
            cx = x0 + k * gap
            col = c_txt if k == active else c_txt2
            d.ellipse([cx - r, 170 - r, cx + r, 170 + r], fill=col)
        self._page_hint(d, W, H, hint or "")
        self._flush_p(img)

    def show_input_ui(self, input_string, cursor_row, cursor_col,
                      keyboard_offset, input_mode, hint="", layout=None):
        """彩屏输入界面（覆盖 OLED 小字版）：输入框 + 加大虚拟键盘。"""
        img = self._new_canvas()
        d = ImageDraw.Draw(img)
        W, H = self._disp.w, self._disp.h
        c_wht = self._rgb("_C_WHT")
        c_txt2 = self._rgb("_C_TXT2")
        c_hi = self._rgb("_C_HI")
        c_dim = self._rgb("_C_DIM")

        # ── 顶部输入框 ──
        d.rectangle([8, 8, W - 8, 48], outline=c_hi, width=2)
        max_chars = 12 if layout is not None else 15
        keep = max(1, max_chars - 3)
        s = str(input_string or "")
        if len(s) > max_chars:
            s = "…" + s[-keep:]
        f_in = self._ttf(20)
        d.text((16, 15), self._fit(d, s + "_", f_in, W - 32), font=f_in, fill=c_wht)
        tip = self._clean_text(hint)
        if tip:
            f_t = self._ttf(12)
            d.text((W - 14 - d.textlength(tip, font=f_t), 20), tip, font=f_t, fill=c_txt2)

        # ── 虚拟键盘（最多 3 行可见）──
        if layout:
            f_key = self._ttf(20)
            ROW_H, TOP = 48, 58
            col_w = (W - 16) // 5
            nrows = len(layout)
            try:
                koff = int(keyboard_offset)
            except (TypeError, ValueError):
                koff = 0
            visible = max(0, min(3, nrows - koff))
            for vi in range(visible):
                lr = koff + vi
                ry = TOP + vi * ROW_H
                for col in range(5):
                    if col >= len(layout[lr]):
                        break
                    ch = layout[lr][col]
                    disp = "←" if ch == "<" else str(ch)
                    cx = 8 + col * col_w
                    cell = [cx + 2, ry, cx + col_w - 6, ry + ROW_H - 10]
                    sel = (lr == cursor_row and col == cursor_col)
                    if sel:
                        d.rectangle(cell, fill=c_hi)
                    else:
                        d.rectangle(cell, outline=c_dim, width=1)
                    tw = d.textlength(disp, font=f_key)
                    d.text((cell[0] + (cell[2] - cell[0] - tw) / 2.0, cell[1] + 6),
                           disp, font=f_key, fill=(c_wht if sel else c_txt2))
            if nrows > 3:
                f_pg = self._ttf(12)
                pg = "%d/%d" % (koff + visible, nrows)
                d.text((W - 14 - d.textlength(pg, font=f_pg), H - 24), pg,
                       font=f_pg, fill=c_txt2)
        self._flush_p(img)

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
                          hint1=None, hint2=None, title="舵机调整",
                          subtitle=None, signed=False, default_label=None,
                          tabs=None, tab_index=0):
        """舵机装机角度调整专用屏（240×240 全屏）。

        为「只有 UP / SET / DOWN 三个触摸键」的场景而做（琴上第 4 个触摸模块
        还没装，所以这个界面**不依赖 ESC**）：

          · 顶部**两个页签**（和弦 / 拨弦）—— 一眼看出现在调的是哪一组；
          · 中间**大号角度数字** —— 站在琴边一眼看清现在调到多少度；
          · 下方带 10° 刻度的滑条，**白色竖线标出默认位**，
            调完一眼能看出偏离默认多远、该往哪边回；
          · 最底部写清三个键各自干什么（保存 / 取消都不需要第 4 个键）。

        signed=True 时按"带符号偏移量"显示（拨弦就是这种：+6° / -12°）。
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
        c_panel = self._t("panel")
        c_dim = self._t("dim")

        angle = float(angle)
        lo, hi = float(min_v), float(max_v)
        p = 0.0 if hi <= lo else max(0.0, min(100.0, (angle - lo) / (hi - lo) * 100.0))

        # ① 标题 + 页签（没有页签时退回一行副标题）
        f1 = self._ttf(18)
        d.text(((W - d.textlength(title, font=f1)) // 2, 6), title,
               font=f1, fill=c_title)
        if tabs:
            ft = self._ttf(13)
            tws = [d.textlength(t, font=ft) for t in tabs]
            pad_x, gap = 14, 8
            total = sum(w + pad_x * 2 for w in tws) + gap * (len(tabs) - 1)
            x = (W - total) / 2.0
            ty, th = 30, 22
            for i, t in enumerate(tabs):
                w = tws[i] + pad_x * 2
                sel = (i == int(tab_index))
                d.rounded_rectangle([x, ty, x + w, ty + th], radius=11,
                                    fill=(c_fill if sel else c_panel))
                d.text((x + pad_x, ty + 3), t, font=ft,
                       fill=(c_dim if sel else c_txt2))
                x += w + gap
        else:
            f2 = self._ttf(13)
            sub = subtitle or "和弦收缩位（齿条收回）"
            d.text(((W - d.textlength(sub, font=f2)) // 2, 34), sub,
                   font=f2, fill=c_txt2)

        # ② 大号角度（自适应降号，放得下为止）
        iv = int(round(angle))
        if signed:
            vtxt = "0°" if iv == 0 else ("%+d°" % iv)
        else:
            vtxt = "%d°" % iv
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
            dtxt = default_label if default_label else ("默认%d°" % int(default_v))
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

    # ══════ 开机启动图（全屏 240×240 · 琴名「金鱼宝宝-to C」+ 金鱼图标）══════
    # ★ 2026-10-11（用户要求）：
    #   ① 「全屏居中」——彩屏此前共用 OLEDRenderer 的 128×64 版式，那段代码把
    #      标题/进度条按 128 宽写死（居中在 x≈64），搬到 240×240 上就只剩左上角
    #      一小块。这里给彩屏单独写一版 240×240 全屏版式。
    #   ② 「琴名 + 金鱼」——上方琴名「金鱼宝宝-to C」，下方金鱼图标。
    #   ③ 金鱼是**矢量绘制**（三次贝塞尔 + 多边形），不依赖任何外部图片文件：
    #      板端 PIL 直接画 → 不用随包推素材，也不怕素材文件丢失。
    #   ④ 画在 2× 离屏画布上，最后 LANCZOS 缩回 240×240 —— 白拿抗锯齿，
    #      矢量边缘不会有难看的阶梯。
    #   调用契约与 OLEDRenderer.show_boot_screen 完全一致（main.py 无感切换）。
    #   ⚠ 要改名字只改下面一行 _BOOT_NAME 即可。
    #     2026-10-11 改名：JY-演奏 → 金鱼宝宝-to C（Z 提供，整机昵称）。
    _BOOT_NAME = "金鱼宝宝-to C"
    #   2026-10-11（Z 要求）删除底部页脚「自动吉他演奏系统 · v4.3」→ 置空即不画；
    #   想恢复只需把文案填回引号里（渲染处已做非空判断）。
    _BOOT_FOOTER = ""
    _SS = 2                                    # 超采样倍数（2 → 480×480 画布）
    _BOOT_BG_CACHE = {}                        # (w,h) → 渐变底 + 柔光（只算一次）

    # 文字 / 进度条
    _TXT_HI = (255, 247, 232)
    _TXT_ST = (150, 208, 236)
    _TXT_DIM = (96, 126, 162)
    _BAR_BG = (24, 40, 68)
    _BAR_FG = (255, 176, 60)

    # ── 金鱼位图（2026-10-11 换装：矢量金鱼 → 油画「金鱼吐泡泡」抠图）──
    # 来源：Z 提供的油画《金鱼缸》，抠出右上主鱼（HSV 橙色分割 + 最大连通域 + 羽化）。
    # PNG8 260×224（2x 超采样尺度，屏上显示约 130×112）；base64 内嵌，不依赖外部文件。
    _BOOT_MOUTH = (0.088, 0.452)               # 鱼嘴在位图内的相对位置（泡泡起点）
    _BOOT_FISH_B64 = (
        "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"
    )

    @classmethod
    def _boot_fish(cls):
        """解码并缓存金鱼位图（RGBA）。"""
        cache = cls.__dict__.get("_BOOT_FISH_CACHE")
        if cache is None:
            raw = base64.b64decode(cls._BOOT_FISH_B64)
            cache = Image.open(io.BytesIO(raw)).convert("RGBA")
            cls._BOOT_FISH_CACHE = cache
        return cache

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
        """彩屏全屏开机图：琴名「金鱼宝宝-to C」+ 金鱼图标 + 进度条（240×240 居中）。

        参数与 OLEDRenderer.show_boot_screen 同名同义；``show_ready`` 仅为兼容
        保留（本版式始终显示「进度条 + 状态文字」，不切「系统就绪」页）。
        """
        ss = self._SS
        W, H = self._disp.w * ss, self._disp.h * ss
        ph = (boot_frame % 30) / 30.0          # 0~1 相位（~5FPS → 约 6 秒一轮）
        cx, cy = 120.0 * ss, 110.0 * ss            # 金鱼视觉中心（2x 尺度）
        img = self._boot_base(W, H).copy().convert("RGBA")

        # ── ① 金鱼位图（油画「金鱼吐泡泡」抠图）+ 轻微上下浮动（呼吸感）──
        fish = self._boot_fish()
        fw, fh = fish.size
        fx = int(cx - fw / 2)
        fy = int(cy - fh / 2 + math.sin(ph * 2 * math.pi) * 6.0)
        img.alpha_composite(fish, (fx, fy))

        # ── ② 气泡：从鱼嘴往左上方升，渐大渐隐，循环往复 ──
        ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        od = ImageDraw.Draw(ov)
        w_thin = max(1, int(0.9 * ss))
        mx = fx + fw * self._BOOT_MOUTH[0]
        my = fy + fh * self._BOOT_MOUTH[1]
        for spd, r0, p0, drift in ((0.55, 5.0, 0.00, -14.0),
                                   (0.75, 3.8, 0.42, -28.0),
                                   (0.45, 3.0, 0.74, -6.0)):
            t = (ph * spd + p0) % 1.0
            a = int(205 * (1.0 - t) ** 0.8)
            if a <= 6:
                continue
            bp_x = mx + drift * t + math.sin(t * 6.283 + p0 * 7.0) * 4.0
            bp_y = my - t * 74.0
            rp = r0 * ss * (0.75 + 0.55 * t)
            od.ellipse([bp_x - rp, bp_y - rp, bp_x + rp, bp_y + rp],
                       fill=(150, 214, 240, a // 3), outline=(196, 232, 252, a),
                       width=w_thin)
        img = Image.alpha_composite(img, ov)
        d = ImageDraw.Draw(img)

        # ── ④ 琴名「金鱼宝宝-to C」──
        #   名字变长（5 汉字 + "-to C"）后 32 号已接近满宽 → 加一道自适应：
        #   实测宽度超过可用区就按比例缩字号，保证任何名字都居中留边、不溢出。
        f_name = self._ttf(int(32 * ss))
        nw = d.textlength(self._BOOT_NAME, font=f_name)
        max_w = W - 24 * ss                    # 左右各留 12px 安全边距
        if nw > max_w:
            f_name = self._ttf(max(12, int(32 * ss * max_w / nw)))
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

        # ── ⑦ 页脚（_BOOT_FOOTER 为空则不画；2026-10-11 Z 要求删除该行）──
        if self._BOOT_FOOTER:
            f_ft = self._ttf(int(10 * ss))
            d.text(((W - d.textlength(self._BOOT_FOOTER, font=f_ft)) / 2.0, 210 * ss),
                   self._BOOT_FOOTER, font=f_ft, fill=self._TXT_DIM)

        # ── ⑧ 缩放回 240×240（LANCZOS 抗锯齿）→ 送屏 ──
        out = img.convert("RGB").resize((self._disp.w, self._disp.h), Image.LANCZOS)
        self._flush_p(out)
