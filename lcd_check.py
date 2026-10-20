#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微雪 1.83" LCD（ST7789P / NV3030B，240x284，4 线 SPI）板端自检
—— 纯 stdlib + libgpiod，零第三方图形库依赖。

用途：接完线第一件事验"屏到底通不通"，不牵扯项目代码。
      依次全屏填 红 → 绿 → 蓝 → 白 → 黑，人眼确认即可。

用法（板端 root）：
    python3 lcd_check.py --list                 # 只看 SPI 节点 / gpiochip，不动屏
    python3 lcd_check.py                        # 用第一个 spidev 节点填色（ST7789）
    python3 lcd_check.py --panel nv3030         # 换 NV3030B 初始化序列
    python3 lcd_check.py --spi /dev/spidev3.0   # 指定节点
    python3 lcd_check.py --dc 114               # 指定 DC 线的 GPIO 编号（默认 GPIO3_C2=114=脚29，随板手册实证）

接线（40PIN，见 48 号总表 §2.5）：
    VCC → 17(3.3V)   GND → 14        DIN(MOSI) → 19
    CLK → 23         CS  → 24        DC → 29        RST → 31(可不接)   BL → 17

⚠️ 判读：
    - 全程全黑          → 背光没通（VCC/BL/GND）或 SPI 没发出数据
    - 整屏均匀白/灰     → 背光通、但没收到有效初始化（SPI 数据没到 / DC 没接对）
    - 颜色错乱/雪花     → 数据通了但初始化序列不匹配（换 --panel 再试）
    - 依次红绿蓝白黑    → ✅ 屏通了
"""
import argparse
import ctypes
import fcntl
import glob
import struct
import sys
import time

SPI_IOC_WR_MODE = 0x40016B01
SPI_IOC_WR_BITS_PER_WORD = 0x40016B03
SPI_IOC_WR_MAX_SPEED_HZ = 0x40046B04
SPI_IOC_MESSAGE_1 = 0x40206B00  # _IOW('k', 0, char[32])

WIDTH, HEIGHT = 240, 284


# ─────────────────────────── SPI（纯 stdlib ioctl） ───────────────────────────
class Spi:
    def __init__(self, dev, speed_hz=12_000_000, mode=0):
        self.fd = open(dev, "r+b", buffering=0)
        fcntl.ioctl(self.fd, SPI_IOC_WR_MODE, struct.pack("<B", mode))
        fcntl.ioctl(self.fd, SPI_IOC_WR_BITS_PER_WORD, struct.pack("<B", 8))
        fcntl.ioctl(self.fd, SPI_IOC_WR_MAX_SPEED_HZ, struct.pack("<I", speed_hz))
        self._keep = []

    def write(self, data: bytes):
        buf = ctypes.create_string_buffer(data, len(data))
        self._keep.append(buf)  # 保持引用，防被回收
        xfer = struct.pack(
            "=QQIIHBBI",
            ctypes.addressof(buf),  # tx_buf
            0,                      # rx_buf
            len(data),              # len
            0,                      # speed_hz（0 = 用默认）
            0,                      # delay_usecs
            8,                      # bits_per_word
            0,                      # cs_change
            0,                      # pad
        )
        fcntl.ioctl(self.fd, SPI_IOC_MESSAGE_1, xfer)
        self._keep = self._keep[-4:]

    def close(self):
        try:
            self.fd.close()
        except Exception:
            pass


# ─────────────────────────── GPIO（libgpiod v1 接口） ───────────────────────────
# 板端实测（2026-10-10）：
#   ① RK3566 的 GPIO 按 bank 分片，/dev/gpiochip{N} ↔ GPIO{N}（各 32 线）：
#      gpiochip0=gpio0(0~31) 1=gpio1(32~63) 2=gpio2(64~95) 3=gpio3(96~127) 4=gpio4(128~159)
#   ② 因此 line offset 是"局部"的 —— GPIO3_B0(全局104) = /dev/gpiochip3 的第 8 号线，
#      用全局编号 104 直接 get_line 会报 EINVAL。
#   ③ 板上 gpiod 1.6.3 的 Chip 没有 get_info()，行数属性叫 num_lines。
def gpio_to_chip(num):
    """全局 GPIO 编号 -> (gpiochip 号, 局部 offset)"""
    return num // 32, num % 32


class Gpio:
    def __init__(self, num, consumer="lcd_check"):
        import gpiod

        bank, offset = gpio_to_chip(num)
        self.chip = gpiod.Chip("gpiochip%d" % bank)
        self.line = self.chip.get_line(offset)
        self.line.request(consumer=consumer, type=gpiod.LINE_REQ_DIR_OUT)

    def set(self, v):
        self.line.set_value(1 if v else 0)


# ─────────────────────────── 面板初始化 ───────────────────────────
def init_panel(spi, dc, panel, rst=None):
    """最小可用初始化：能出纯色即可，不做偏移/圆角精调。"""
    dc.set(0)
    if rst is not None:
        rst.set(1)
        time.sleep(0.05)
        rst.set(0)
        time.sleep(0.05)
        rst.set(1)
        time.sleep(0.12)
    else:
        cmd(spi, dc, 0x01)          # SWRESET（软复位，RST 没接时靠它）
        time.sleep(0.15)
    cmd(spi, dc, 0x11)              # SLPOUT
    time.sleep(0.12)

    if panel == "nv3030":
        # NV3030B：常见初始化
        cmd(spi, dc, 0x36, b"\x00")                  # MADCTL
        cmd(spi, dc, 0x3A, b"\x55")                  # COLMOD 16bit
        cmd(spi, dc, 0xB2, bytes([0x0C, 0x0C, 0x00, 0x33, 0x33]))
        cmd(spi, dc, 0xB7, b"\x35")
        cmd(spi, dc, 0xBB, b"\x19")
        cmd(spi, dc, 0xC0, b"\x2C")
        cmd(spi, dc, 0xC2, b"\x01")
        cmd(spi, dc, 0xC3, b"\x12")
        cmd(spi, dc, 0xC4, b"\x20")
        cmd(spi, dc, 0xC6, b"\x0F")
        cmd(spi, dc, 0xD0, bytes([0xA4, 0xA1]))
        cmd(spi, dc, 0x21)                            # INVON
        cmd(spi, dc, 0x13)                            # NORON
        cmd(spi, dc, 0x29)                            # DISPON
    else:
        # ST7789 系列：微雪 1.83" 常见序列
        cmd(spi, dc, 0x36, b"\x00")                   # MADCTL
        cmd(spi, dc, 0x3A, b"\x55")                   # COLMOD 16bit RGB565
        cmd(spi, dc, 0xB2, bytes([0x0C, 0x0C, 0x00, 0x33, 0x33]))
        cmd(spi, dc, 0xB7, b"\x35")
        cmd(spi, dc, 0xBB, b"\x19")
        cmd(spi, dc, 0xC0, b"\x2C")
        cmd(spi, dc, 0xC2, b"\x01")
        cmd(spi, dc, 0xC3, b"\x12")
        cmd(spi, dc, 0xC4, b"\x20")
        cmd(spi, dc, 0xC6, b"\x0F")
        cmd(spi, dc, 0xD0, bytes([0xA4, 0xA1]))
        cmd(spi, dc, 0x21)                            # INVON（IPS 必须）
        cmd(spi, dc, 0x13)
        cmd(spi, dc, 0x29)
    time.sleep(0.05)


def cmd(spi, dc, c, data=None):
    dc.set(0)
    spi.write(bytes([c]))
    if data:
        dc.set(1)
        spi.write(bytes(data))


def fill(spi, dc, color_hex):
    """整屏填色（RGB565）。"""
    hi, lo = (color_hex >> 8) & 0xFF, color_hex & 0xFF
    cmd(spi, dc, 0x2A, struct.pack(">HH", 0, WIDTH - 1))      # CASET
    cmd(spi, dc, 0x2B, struct.pack(">HH", 0, HEIGHT - 1))     # RASET
    cmd(spi, dc, 0x2C)                                         # RAMWR
    dc.set(1)
    chunk = bytes([hi, lo]) * 2048
    remain = WIDTH * HEIGHT * 2
    while remain > 0:
        n = min(len(chunk), remain)
        spi.write(chunk[:n])
        remain -= n


# ─────────────────────────── 信号探针（万用表排线用） ───────────────────────────
def probe(spi, dc, speed_hz=200_000, rounds=3):
    """不点屏。让每根信号线产生可被万用表（DC 20V 挡）读出的特征电平，
    从屏端 8P 座逐根量 —— 信号对 = 线通；信号错/无 = 那根线断或插错位。
    每轮 90 秒、默认跑 3 轮（约 4.5 分钟），给万用表留足时间。"""
    print("""
━━━ 信号探针（循环 %d 轮，共约 %.0f 秒）━━━━━━━━━━━━━━━━━━━━
万用表调 DC 20V 挡，黑表笔钉在屏端 GND，红表笔逐根量屏端 8P 座。
量之前别动线 —— 表笔就停在要量的那根上，看不同阶段的读数。
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━""" % (rounds, rounds * 90.0), flush=True)

    for rnd in range(1, rounds + 1):
        print("\n══════ 第 %d/%d 轮 ══════" % (rnd, rounds), flush=True)

        print("  ▸ A 段：DC(脚29) 慢翻转 30s → 量【屏端 DC】：应见 0↔3.3V 来回跳", flush=True)
        t0 = time.time()
        v = 0
        while time.time() - t0 < 30:
            v ^= 1
            dc.set(v)
            print("      DC=%d" % v, flush=True)
            time.sleep(1.5)
        dc.set(0)

        print("  ▸ B 段：连发 0xFF 30s → 量【屏端 DIN / CLK / CS / VCC】：", flush=True)
        print("        DIN≈3.3V   CLK≈1.5V(半高)   CS≈0V   VCC≈3.3V   BL≈3.3V", flush=True)
        _slow_write(spi, b"\xff" * 256, speed_hz, 30)

        print("  ▸ C 段：连发 0x00 30s → 量【屏端 DIN】：应≈0V   （CLK 仍≈半高）", flush=True)
        _slow_write(spi, b"\x00" * 256, speed_hz, 30)

    print("""
━━━ 探测结束 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
普通万用表 DC 挡能可靠读出的：DIN（30s 慢翻）、DC（1.5s 翻）、CS（30s 慢）、VCC。
判定：
  VCC 不是常 3.3V            → 屏逻辑电没通 → 全黑（首要嫌疑）
  DC 不随 A 段跳 0↔3.3V       → DC 没落到脚 29
  CS 在 B/C 段不拉低(≈0V)     → CS 没落到脚 24
  DIN 在 B/C 段 0↔3.3V 变     → DIN 线通

⚠️ CLK 是 ~200kHz 方波，普通万用表 DC 挡【可能根本读不出】→ 别只凭"CLK=0"下结论！
   两个可靠办法（任一）：
   ① 表的「Hz / 频率」挡量屏端 CLK：应读到非 0 频率
   ② DIN/CLK 对调复测：把【板端】19 与 23 两根线互换，再跑本探针 ——
      此时屏端 CLK 改收 MOSI（慢翻转）：随 30s 节奏 0↔3.3V 跳 = CLK 线好；
      恒 0.00V = CLK 线【断/坏】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━""", flush=True)


def _slow_write(spi, buf, speed_hz, seconds):
    """以极低速连续发送 buf，让万用表能读出平均电平。"""
    import os
    SPI_IOC_WR_MAX_SPEED_HZ = 0x40046B04
    fcntl.ioctl(spi.fd, SPI_IOC_WR_MAX_SPEED_HZ, struct.pack("<I", speed_hz))
    keep = ctypes.create_string_buffer(buf, len(buf))
    xfer = struct.pack("=QQIIHBBI", ctypes.addressof(keep), 0,
                       len(buf), 0, 0, 8, 0, 0)
    t0 = time.time()
    n = 0
    while time.time() - t0 < seconds:
        fcntl.ioctl(spi.fd, SPI_IOC_MESSAGE_1, xfer)
        n += 1
    fcntl.ioctl(spi.fd, SPI_IOC_WR_MAX_SPEED_HZ, struct.pack("<I", 12_000_000))
    print("  （已连发 %d 轮）" % n, flush=True)


# ─────────────────── 找 DC：逐个候选 GPIO 慢闪（表笔停在屏端 DC） ───────────────────
# 手册照片第 29 行读作 GPIO3_C2(114)，但换上仍全黑 → 不再依赖任何引脚表，
# 用「慢速翻转 + 万用表」直接问出与 DC 线真正连通的那个 GPIO。
DC_CANDIDATES = [
    (114, "GPIO3_C2"), (104, "GPIO3_B0"), (115, "GPIO3_C3"), (116, "GPIO3_C4"),
    (103, "GPIO3_A7"), (102, "GPIO3_A6"), (111, "GPIO3_B7"), (110, "GPIO3_B6"),
    (109, "GPIO3_B5"), (107, "GPIO3_B3"), (106, "GPIO3_B2"), (105, "GPIO3_B1"),
    (100, "GPIO3_A4"), (99, "GPIO3_A3"), (98, "GPIO3_A2"), (97, "GPIO3_A1"),
    (39, "GPIO1_A7"), (36, "GPIO1_A4"), (48, "GPIO1_C0"), (47, "GPIO1_B7"),
    (6, "GPIO0_B6"), (5, "GPIO0_B5"),
]


def hunt_dc(secs=6.0):
    """逐个候选 GPIO 以 1Hz 翻转，表笔停在【屏端 DC】：
    哪个候选能让屏端 DC 在 0↔3.3V 之间跳 = DC 线真正连到的那个 GPIO。"""
    import gpiod

    total = len(DC_CANDIDATES) * secs
    print("""
━━━ DC 定位（%d 个候选，约 %.0f 秒）━━━━━━━━━━━━━━━━━
万用表 DC 20V 挡：黑表笔钉【屏端 GND】，红表笔停在【屏端 DC】别动。
每换一个候选脚本会打印名字 —— 谁让 DC 一秒一跳，谁就是答案。
（只驱动 GPIO 脚，不会碰到 5V/3.3V 电源脚，安全）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━""" % (len(DC_CANDIDATES), total), flush=True)

    for num, name in DC_CANDIDATES:
        try:
            bank, off = gpio_to_chip(num)
            chip = gpiod.Chip("gpiochip%d" % bank)
            line = chip.get_line(off)
            line.request(consumer="dchunt", type=gpiod.LINE_REQ_DIR_OUT)
        except Exception as e:
            print("✗ %s (GPIO%d) 打不开：%s" % (name, num, e), flush=True)
            continue

        print("\n▶▶ 正在驱动 %s (GPIO%d) …… 盯表！" % (name, num), flush=True)
        try:
            t0 = time.time()
            v = 0
            while time.time() - t0 < secs:
                v ^= 1
                line.set_value(v)
                time.sleep(1.0)
            line.set_value(0)
        finally:
            try:
                line.release()
            except Exception:
                pass

    print("""
━━━ 结束 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
① 某个候选让【屏端 DC】0↔3.3V 跳 → 把它的名字告诉我，我改参数重跑填色
② 全程 DC 一动不动 → DC 线大概率落在电源/GND 针上（被钉死）或断了
   → 断电，把 DC 线重插到【脚 29】（以 DIN=脚19 为参照数）
③ 想彻底定案（断电，蜂鸣挡）：一表笔钉【屏端 DC】，另一表笔扫 40 针，
   响在哪针 = DC 线实际落点
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━""", flush=True)


# ─────────────────────────── main ───────────────────────────
COLORS = [("红", 0xF800), ("绿", 0x07E0), ("蓝", 0x001F), ("白", 0xFFFF), ("黑", 0x0000)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="只列 SPI 节点与 gpiochip")
    ap.add_argument("--spi", default=None, help="spidev 节点，默认取第一个")
    ap.add_argument("--panel", default="st7789", choices=["st7789", "nv3030"])
    ap.add_argument("--dc", type=int, default=114, help="DC 线 GPIO 编号，默认 114(GPIO3_C2=脚29，随板手册实证)")
    ap.add_argument("--rst", type=int, default=None, help="RST 线 GPIO 编号，默认不接")
    ap.add_argument("--speed", type=int, default=12_000_000)
    ap.add_argument("--spi-mode", type=int, default=0, choices=[0, 1, 2, 3],
                    help="SPI 模式，默认 0；某些屏要 mode 3 才有反应")
    ap.add_argument("--repeat", type=int, default=1, help="填色轮数，>1 时循环显示便于观察")
    ap.add_argument("--probe", action="store_true",
                    help="信号探针：万用表逐根量线，不点屏（接线排错用）")
    ap.add_argument("--probe-rounds", type=int, default=3, help="探针循环轮数，每轮 90 秒")
    ap.add_argument("--hunt-dc", action="store_true",
                    help="DC 定位：逐个候选 GPIO 1Hz 慢闪，表笔停屏端 DC 找出真身")
    ap.add_argument("--hunt-secs", type=float, default=6.0, help="每个候选驱动秒数")
    args = ap.parse_args()

    nodes = sorted(glob.glob("/dev/spidev*"))
    print("SPI 节点: %s" % (", ".join(nodes) if nodes else "（无！内核没起 SPI 控制器）"), flush=True)
    print("gpiochip: %s" % ", ".join(sorted(glob.glob("/dev/gpiochip*"))), flush=True)
    if args.list or not nodes:
        if not nodes:
            print("\n⚠️ 没有 /dev/spidev* —— 这是设备树/内核层的问题，跟接线无关：", flush=True)
            print("   ① 看 `dmesg | grep -i -E 'spi|rockchip-spi'` 有没有控制器注册", flush=True)
            print("   ② 查 /boot 下是否有 SPI3 的 overlay 没启用（lckfb Ubuntu 镜像常见）", flush=True)
        return

    dev = args.spi or nodes[0]
    print("使用 %s  panel=%s  dc=GPIO%d  speed=%d  mode=%d"
          % (dev, args.panel, args.dc, args.speed, args.spi_mode), flush=True)

    spi = Spi(dev, args.speed, args.spi_mode)
    dc = Gpio(args.dc)
    rst = Gpio(args.rst) if args.rst is not None else None

    if args.hunt_dc:
        spi.close()
        try:
            dc.line.release()      # 先放开默认 DC，避免候选与它抢
        except Exception:
            pass
        hunt_dc(args.hunt_secs)
        return

    if args.probe:
        probe(spi, dc, 200_000, args.probe_rounds)
        spi.close()
        return

    init_panel(spi, dc, args.panel, rst)
    print("初始化完成，开始填色（盯屏）……", flush=True)
    for r in range(max(1, args.repeat)):
        if args.repeat > 1:
            print("— 第 %d/%d 轮 —" % (r + 1, args.repeat), flush=True)
        for name, val in COLORS:
            print("  → %s" % name, flush=True)
            fill(spi, dc, val)
            time.sleep(1.6)

    print("\n若 5 色依次出现 = ✅ 屏通路 OK", flush=True)
    print("若全黑/全白/花色 → 见脚本头部「判读」段的三种情形", flush=True)
    spi.close()


if __name__ == "__main__":
    main()
