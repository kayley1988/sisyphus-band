#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""servo_cal_screen.py — 舵机校准 · 屏上触摸版（1.54″ ST7789 240×240）

设计目标：装机现场**不用电脑**。屏上看得见大号角度数字，手摸触摸块就能调，
调一次舵机动一次，确认无误长按存盘。

操作映射（只依赖 UP/DOWN/SET 三个键 + 6 路触摸条；第 4 个触摸模块未装，不依赖 ESC）：
    · 触摸块 1-6  → 选弦 1-6（摸哪块选哪弦）
    · UP / DOWN   → 当前调节点 −/+（按住 0.5s 后连发加速）
    · SET 短按    → 循环切换调节点（菜单页=进入）
    · SET 长按 1s → 存盘（写回 servo_config.csv）

页面：
    主菜单 ─┬─ 压弦校准：零点(品12/品34) + 压深(品1-4)，实时驱动那一片舵机
            ├─ 拨弦校准：6 弦一屏，低点/高点切换
            ├─ 装机角  ：复用官方 show_servo_adjust 页（60~120° 滑条）
            └─ 保存并退出

用法：
    python3 servo_cal_screen.py                # 真机：接管屏幕 + 串口
    python3 servo_cal_screen.py --selftest 8   # 自检：只读触摸 8 秒，不动琴
    python3 servo_cal_screen.py --png /tmp/out # 离线把各页面渲成 PNG（不碰 SPI）

屏与串口都被主程序独占 → 真机运行前请用 servo_cal.sh（自动停/恢复 guitar.service）。
"""

import os
import sys
import time
import argparse

from PIL import ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "src", "guitarist")
# ⚠ 顺序要紧：/root/guitarist_app/ 根目录下还有一个**旧的 lcd_renderer.py 副本**
#   （552 行，没有彩色层 _t/_t565）。src 必须排在 HERE 之前，否则 import 到旧副本，
#   表现为 "LCDRenderer object has no attribute '_t'"。
sys.path.insert(0, HERE)
if os.path.isdir(SRC):
    sys.path.insert(0, SRC)

# ───────────────────────── 触摸硬件 ─────────────────────────

# 6 路触摸条 → 弦 1-6（Blinka 名 / 40PIN 脚号 / Linux 编号）
TOUCH_STRIP = [
    ("GPIO1_A4", 7, 36),
    ("GPIO3_C4", 12, 116),
    ("GPIO3_A5", 18, 101),
    ("GPIO3_A6", 22, 102),
    ("GPIO3_A7", 26, 103),
    ("GPIO3_C5", 32, 117),
]
# 4 个触摸按键（全高有效；ESC 的模块未到货，脚16 悬空=永不触发，安全）
KEYS = [
    ("up", "GPIO3_A1", 11, 97),
    ("down", "GPIO3_A2", 13, 98),
    ("set", "GPIO3_A3", 15, 99),
    ("esc", "GPIO3_A4", 16, 100),
]
LONG_PRESS_S = 1.0          # SET 长按=存盘
REPEAT_DELAY_S = 0.5        # UP/DOWN 按住多久开始连发
REPEAT_RATE_S = 0.12        # 连发间隔


class _SysfsPin:
    """Blinka 缺席时的兜底：直接用 sysfs 读 GPIO。"""

    def __init__(self, linux_num):
        self.num = linux_num
        self.path = "/sys/class/gpio/gpio%d" % linux_num
        if not os.path.isdir(self.path):
            try:
                with open("/sys/class/gpio/export", "w") as f:
                    f.write(str(linux_num))
                time.sleep(0.05)
            except Exception:
                pass
        try:
            with open(os.path.join(self.path, "direction"), "w") as f:
                f.write("in")
        except Exception:
            pass
        self._v = os.path.join(self.path, "value")

    def value(self):
        try:
            with open(self._v) as f:
                return f.read().strip() == "1"
        except Exception:
            return False


class TouchIO:
    """触摸输入：6 路条 + 4 个键。

    分工（板上实测后的唯一正确口径）：
      · 4 个按键：`hardware.py` 在导入时就已经按键申请并配好方向，且它导出了全项目
        唯一的口径 `hardware.is_pressed(name)`（TTP223 全高有效）。我**不再抢同一批引脚**
        ——重复申请会抛 Errno 16 Device or resource busy，按键直接失效。
      · 6 路触摸条：`hardware.touch_strip` 恒为 None（谁用谁申请），所以由我这里独占申请。
    """

    def __init__(self, need=2):
        self.need = need
        self.pins = {}          # 只放触摸条 6 路
        self.state = {}
        self.hits = {}
        self.t0 = {}
        self.fired_long = {}
        self.last_repeat = {}
        self.backend = "none"
        self._hw = None
        self._init_strip()
        self._init_hw_keys()

    def _init_strip(self):
        try:
            import board
            import digitalio
            for i, (bname, _pin, _ln) in enumerate(TOUCH_STRIP):
                d = digitalio.DigitalInOut(getattr(board, bname))
                d.direction = digitalio.Direction.INPUT
                self.pins["strip%d" % (i + 1)] = d
            self.backend = "blinka"
        except Exception as e:
            sys.stderr.write("blinka 不可用(%s)，退回 sysfs\n" % e)
            self.pins = {}
            for i, (_b, _p, ln) in enumerate(TOUCH_STRIP):
                self.pins["strip%d" % (i + 1)] = _SysfsPin(ln)
            self.backend = "sysfs"
        for k in self.pins:
            self.state[k] = False
            self.hits[k] = 0
            self.t0[k] = 0.0
            self.fired_long[k] = False
            self.last_repeat[k] = 0.0

    def _init_hw_keys(self):
        try:
            import hardware
            self._hw = hardware
            for key in ("up", "down", "set", "esc"):
                self.state[key] = False
                self.hits[key] = 0
                self.t0[key] = 0.0
                self.fired_long[key] = False
                self.last_repeat[key] = 0.0
        except Exception as e:
            sys.stderr.write("hardware 按键不可用(%s)\n" % e)
            self._hw = None

    def _raw(self, name):
        if name in self.pins:
            p = self.pins[name]
            try:
                return bool(p.value) if self.backend == "blinka" else p.value()
            except Exception:
                return False
        if self._hw is not None:
            try:
                return bool(self._hw.is_pressed(name))
            except Exception:
                return False
        return False

    def poll(self):
        """返回本次新产生的事件列表（上升沿 + 长按 + 连发），如 ['strip3','up','set_long']。"""
        ev = []
        now = time.monotonic()
        for name in self.state:
            r = self._raw(name)
            if r != self.state[name]:
                self.hits[name] += 1
                if self.hits[name] >= self.need:
                    self.state[name] = r
                    self.hits[name] = 0
                    if r:
                        ev.append(name)
                        self.t0[name] = now
                        self.fired_long[name] = False
                        self.last_repeat[name] = now
            else:
                self.hits[name] = 0
                if self.state[name]:
                    held = now - self.t0[name]
                    if name == "set" and not self.fired_long[name] and held >= LONG_PRESS_S:
                        self.fired_long[name] = True
                        ev.append("set_long")
                    if name in ("up", "down") and held >= REPEAT_DELAY_S \
                            and now - self.last_repeat[name] >= REPEAT_RATE_S:
                        self.last_repeat[name] = now
                        ev.append(name)
        return ev

    def pressed(self, name):
        return self.state.get(name, False)

    def close(self):
        for p in self.pins.values():
            try:
                p.deactivate()
            except Exception:
                pass


# ───────────────────────── 渲染 ─────────────────────────

def init_fonts():
    """与 hardware.init_fonts 同款（不 import hardware，避免它的导入副作用）。"""
    from PIL import ImageFont
    path = "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"
    try:
        return {"font": ImageFont.truetype(path, 14),
                "font_small": ImageFont.truetype(path, 12),
                "font_tiny": ImageFont.truetype(path, 10)}
    except Exception:
        f = ImageFont.load_default()
        return {"font": f, "font_small": f, "font_tiny": f}


class FakeDisp:
    """离线渲染用假屏（不碰 SPI）。"""

    def __init__(self, w=240, h=240):
        self.w, self.h = w, h


# 调节点：压弦页 6 个点循环
PRESS_POINTS = [
    ("零点", "品12组", "null", 0),
    ("零点", "品34组", "null", 1),
    ("压深", "品1", "press", 0),
    ("压深", "品2", "press", 1),
    ("压深", "品3", "press", 2),
    ("压深", "品4", "press", 3),
]


class CalUI:
    def __init__(self, renderer, cfg, io, driver):
        self.r = renderer
        self.cfg = cfg
        self.io = io
        self.drv = driver
        self.page = "menu"
        self.menu_idx = 0
        self.string = 0          # 0..5
        self.point = 0           # 压弦页 6 个调节点
        self.strum_end = 0       # 拨弦 0=低点 1=高点
        self.dirty = False
        self.toast = ""
        self.toast_until = 0.0

    # ── 值读写 ──
    def point_info(self):
        return PRESS_POINTS[self.point]

    def get_value(self):
        kind, _lab, k, i = self.point_info()
        if k == "null":
            return self.cfg["null_offset"][2 * self.string + i]
        return self.cfg["press_offsets"][self.string][i]

    def set_value(self, v):
        kind, _lab, k, i = self.point_info()
        lim = 45.0 if k == "null" else 90.0
        v = max(-lim, min(lim, round(v * 10) / 10.0))
        if k == "null":
            self.cfg["null_offset"][2 * self.string + i] = v
        else:
            self.cfg["press_offsets"][self.string][i] = v
        self.dirty = True

    def apply(self):
        """把当前值立刻作用到舵机（看得见才算校得准）。"""
        kind, _lab, k, i = self.point_info()
        try:
            if k == "null":
                ang = self.cfg["home_chord_angle"] + self.cfg["null_offset"][2 * self.string + i]
                self.drv.move(self.cfg["chord_map"][2 * self.string + i], ang)
            else:
                a_lo, a_hi = self.drv.angles_for_press(self.cfg, self.string, i + 1)
                tag = self.cfg["chord_map"][2 * self.string + (0 if i < 2 else 1)]
                self.drv.move(tag, a_lo if i < 2 else a_hi)
        except Exception as e:
            self.flash("驱动失败 %s" % e)

    def apply_all_home(self):
        for i, tag in enumerate(self.cfg["chord_map"]):
            try:
                self.drv.move(tag, self.cfg["home_chord_angle"] + self.cfg["null_offset"][i])
                time.sleep(0.012)
            except Exception:
                pass

    def apply_home_preview(self):
        self.apply_all_home()

    def flash(self, text, secs=1.2):
        self.toast = text
        self.toast_until = time.monotonic() + secs

    # ── 事件处理 ──
    def handle(self, ev):
        for e in ev:
            if self.page == "menu":
                self._menu(e)
            elif self.page == "press":
                self._press(e)
            elif self.page == "strum":
                self._strum(e)
            elif self.page == "home":
                self._home(e)

    def _menu(self, e):
        if e == "up":
            self.menu_idx = (self.menu_idx - 1) % 4
        elif e == "down":
            self.menu_idx = (self.menu_idx + 1) % 4
        elif e.startswith("strip"):
            n = int(e[5:]) - 1
            if n < 4:
                self.menu_idx = n
                self._enter()
        elif e == "set":
            self._enter()
        elif e == "set_long":
            self.save()
        elif e == "esc":
            pass

    def _enter(self):
        if self.menu_idx == 0:
            self.page = "press"
            self.apply()                      # 进页先动一下，确认是哪一片
        elif self.menu_idx == 1:
            self.page = "strum"
        elif self.menu_idx == 2:
            self.page = "home"
            self.apply_home_preview()
        else:
            self.save()
            self.page = "menu"

    def _back(self):
        self.page = "menu"

    def _press(self, e):
        if e.startswith("strip"):
            self.string = int(e[5:]) - 1
            self.apply()
        elif e == "up":
            self.set_value(self.get_value() - 0.5)
            self.apply()
        elif e == "down":
            self.set_value(self.get_value() + 0.5)
            self.apply()
        elif e == "set":
            self.point = (self.point + 1) % len(PRESS_POINTS)
            self.apply()
        elif e == "set_long":
            self.save()
        elif e == "esc":
            self._back()

    def _strum(self, e):
        if e.startswith("strip"):
            self.string = int(e[5:]) - 1
            self.apply()
        elif e == "up":
            self.cfg["strum_angles"][self.string][self.strum_end] = max(
                0, self.cfg["strum_angles"][self.string][self.strum_end] - 1)
            self.dirty = True
            self.apply()
        elif e == "down":
            self.cfg["strum_angles"][self.string][self.strum_end] = min(
                180, self.cfg["strum_angles"][self.string][self.strum_end] + 1)
            self.dirty = True
            self.apply()
        elif e == "set":
            self.strum_end = 1 - self.strum_end
            self.apply()
        elif e == "set_long":
            self.save()
        elif e == "esc":
            self._back()

    def _home(self, e):
        if e == "up":
            self.cfg["home_chord_angle"] = max(60.0, self.cfg["home_chord_angle"] - 1)
            self.dirty = True
            self.apply_home_preview()
        elif e == "down":
            self.cfg["home_chord_angle"] = min(120.0, self.cfg["home_chord_angle"] + 1)
            self.dirty = True
            self.apply_home_preview()
        elif e == "set":
            self.save()
            self._back()
        elif e == "set_long":
            self.save()
        elif e == "esc":
            self._back()

    def save(self):
        try:
            n = self.drv.cfg_put(self.cfg)
            self.dirty = False
            self.flash("已保存 · %d 项写入 csv" % n, 1.6)
        except Exception as e:
            self.flash("保存失败 %s" % e, 2.0)

    # ── 绘制 ──
    def draw(self):
        if self.page == "menu":
            self._draw_menu()
        elif self.page == "press":
            self._draw_press()
        elif self.page == "strum":
            self._draw_strum()
        elif self.page == "home":
            self._draw_home()

    def _toast_line(self, img, d, y=214):
        if self.toast and time.monotonic() < self.toast_until:
            f = self.r._ttf(12)
            d.text(((240 - d.textlength(self.toast, font=f)) // 2, y), self.toast,
                   font=f, fill=self.r._t("title"))
            return True
        return False

    def _hint(self, d, text, y, size=11, color="txt2"):
        f = self.r._ttf(size)
        while size > 9 and d.textlength(text, font=f) > 232:
            size -= 1
            f = self.r._ttf(size)
        d.text(((240 - d.textlength(text, font=f)) // 2, y), text,
               font=f, fill=self.r._t(color))

    def _draw_menu(self):
        img = self.r._new_canvas()
        d = ImageDraw.Draw(img)
        c_title, c_txt2, c_panel, c_fill, c_dim = (
            self.r._t("title"), self.r._t("txt2"), self.r._t("panel"),
            self.r._t("press"), self.r._t("dim"))
        f1 = self.r._ttf(18)
        d.text(((240 - d.textlength("舵机校准", font=f1)) // 2, 4), "舵机校准",
               font=f1, fill=c_title)

        items = ["压弦校准", "拨弦校准", "装机角", "保存并退出"]
        f2 = self.r._ttf(14)
        y = 34
        for i, it in enumerate(items):
            sel = (i == self.menu_idx)
            d.rounded_rectangle([16, y, 224, y + 26], radius=7,
                                fill=(c_fill if sel else c_panel))
            d.text((28, y + 5), "%d %s" % (i + 1, it), font=f2,
                   fill=(c_dim if sel else c_txt2))
            y += 31
        f3 = self.r._ttf(11)
        home = self.cfg["home_chord_angle"]
        tip = "装机角 %d°" % int(round(home)) + ("   ● 有未保存改动" if self.dirty else "")
        d.text(((240 - d.textlength(tip, font=f3)) // 2, 162), tip, font=f3, fill=c_txt2)
        if not self._toast_line(img, d, 178):
            self._hint(d, "触摸块1-4 直选 · UP/DN 移动 · SET 进入", 180, 11)
            self._hint(d, "长按 SET 存盘", 196, 11)
        self.r._flush_p(img)

    def _draw_press(self):
        img = self.r._new_canvas()
        d = ImageDraw.Draw(img)
        c_title, c_txt2, c_panel, c_fill, c_dim, c_grid = (
            self.r._t("title"), self.r._t("txt2"), self.r._t("panel"),
            self.r._t("press"), self.r._t("dim"), self.r._t("grid"))
        c_val = self.r._t("playhead")

        f1 = self.r._ttf(17)
        d.text(((240 - d.textlength("压弦校准", font=f1)) // 2, 3), "压弦校准",
               font=f1, fill=c_title)
        f2 = self.r._ttf(12)
        sub = "弦%d · %s%s" % (self.string + 1, self.point_info()[0], self.point_info()[1])
        d.text(((240 - d.textlength(sub, font=f2)) // 2, 25), sub, font=f2, fill=c_txt2)

        # 大号当前值
        v = self.get_value()
        kind = self.point_info()[2]
        vtxt = ("%+.1f°" % v) if kind == "null" else ("%.1f°" % v)
        fv = self.r._ttf(52)
        for fs in (52, 44, 36, 30):
            fv = self.r._ttf(fs)
            if d.textlength(vtxt, font=fv) <= 216:
                break
        d.text(((240 - d.textlength(vtxt, font=fv)) // 2, 42), vtxt, font=fv, fill=c_val)

        # 6 个调节点 chips（3 列 2 行）
        fc = self.r._ttf(11)
        for i, (k1, k2, _a, _b) in enumerate(PRESS_POINTS):
            r0, c0 = divmod(i, 3)
            x = 12 + c0 * 73
            y = 104 + r0 * 25
            sel = (i == self.point)
            d.rounded_rectangle([x, y, x + 69, y + 21], radius=6,
                                fill=(c_fill if sel else c_panel))
            lab = k1 + k2.replace("组", "").replace("品", "")
            d.text((x + 4, y + 4), lab, font=fc, fill=(c_dim if sel else c_txt2))

        # 触摸块 1-6 = 选弦
        fk = self.r._ttf(12)
        for i in range(6):
            x = 12 + i * 37
            y = 156
            sel = (i == self.string)
            d.rounded_rectangle([x, y, x + 33, y + 22], radius=6,
                                fill=(c_fill if sel else c_panel))
            d.text((x + 12, y + 4), str(i + 1), font=fk, fill=(c_dim if sel else c_txt2))
        d.text((12, 141), "触摸块→选弦", font=self.r._ttf(10), fill=c_grid)

        if not self._toast_line(img, d, 184):
            self._hint(d, "UP/DN %s ±0.5° · SET 换调节点" %
                       ("减/增" if kind == "null" else "减/增"), 186, 11)
            self._hint(d, "长按 SET 存盘 · 返回: 菜单-ESC(未装)", 202, 10)
        self.r._flush_p(img)

    def _draw_strum(self):
        img = self.r._new_canvas()
        d = ImageDraw.Draw(img)
        c_title, c_txt2, c_panel, c_fill, c_dim = (
            self.r._t("title"), self.r._t("txt2"), self.r._t("panel"),
            self.r._t("press"), self.r._t("dim"))
        f1 = self.r._ttf(17)
        d.text(((240 - d.textlength("拨弦校准", font=f1)) // 2, 3), "拨弦校准",
               font=f1, fill=c_title)
        f2 = self.r._ttf(12)
        d.text((12, 26), "触摸块选弦 · SET 切低/高", font=f2, fill=c_txt2)

        f3 = self.r._ttf(13)
        y = 50
        for i in range(6):
            lo, hi = self.cfg["strum_angles"][i]
            sel = (i == self.string)
            d.rounded_rectangle([12, y, 228, y + 22], radius=6,
                                fill=(c_fill if sel else c_panel))
            txt = "%d弦" % (i + 1)
            d.text((18, y + 3), txt, font=f3, fill=(c_dim if sel else c_txt2))
            v1 = "%d" % int(round(lo))
            v2 = "%d" % int(round(hi))
            mark1 = "◀" if (sel and self.strum_end == 0) else " "
            mark2 = "◀" if (sel and self.strum_end == 1) else " "
            d.text((92, y + 3), "%s%s" % (mark1, v1), font=f3,
                   fill=(c_dim if sel else c_txt2))
            d.text((166, y + 3), "%s%s" % (mark2, v2), font=f3,
                   fill=(c_dim if sel else c_txt2))
            y += 25

        if not self._toast_line(img, d, 212):
            self._hint(d, "UP/DN 调 ±1° · SET 切低/高点 · 长按SET 存盘", 214, 10)
        self.r._flush_p(img)

    def _draw_home(self):
        ang = self.cfg["home_chord_angle"]
        self.r.show_servo_adjust(
            ang, 60.0, 120.0, 90.0,
            hint1="UP / DOWN   装机角 ±1°",
            hint2="SET 存盘并返回 · 长按 SET 存盘",
            title="装机角", tabs=["和弦 12 片"], tab_index=0)

    def _draw_saved(self):
        img = self.r._new_canvas()
        d = ImageDraw.Draw(img)
        f = self.r._ttf(26)
        t1 = "已保存"
        d.text(((240 - d.textlength(t1, font=f)) // 2, 92), t1, font=f,
               fill=self.r._t("title"))
        f2 = self.r._ttf(12)
        t2 = self.toast or ""
        d.text(((240 - d.textlength(t2, font=f2)) // 2, 132), t2, font=f2,
               fill=self.r._t("txt2"))
        self.r._flush_p(img)

    # ── 主循环 ──
    def run(self, selftest=0.0):
        last = time.monotonic()
        self.draw()
        t_boot = time.monotonic()
        try:
            while True:
                ev = self.io.poll()
                if selftest:
                    if ev:
                        print("[selftest] 事件: %s  (page=%s string=%d point=%d)"
                              % (ev, self.page, self.string + 1, self.point), flush=True)
                    if time.monotonic() - t_boot > selftest:
                        print("[selftest] 结束，触摸后端=%s" % self.io.backend, flush=True)
                        return
                if ev:
                    self.handle(ev)
                    self.draw()
                    last = time.monotonic()
                else:
                    if self.toast and time.monotonic() > self.toast_until:
                        self.toast = ""
                        self.draw()
                    elif time.monotonic() - last > 30 and not selftest:
                        return          # 30 秒无操作自动退出（回到主程序）
                time.sleep(0.02)
        except KeyboardInterrupt:
            return


# ───────────────────────── 驱动适配 ─────────────────────────

def make_driver():
    """复用 servo_panel 的串口驱动与 csv 读写（同一套公式，屏/网页结果一致）。"""
    import servo_panel as sp
    return sp


# ───────────────────────── 入口 ─────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--png", metavar="DIR", help="把各页面渲成 PNG（不碰 SPI）")
    ap.add_argument("--selftest", type=float, default=0.0, metavar="SECS",
                    help="只读触摸 N 秒做自检，不动琴")
    ap.add_argument("--no-drive", action="store_true", help="不发串口帧（纯看屏）")
    args = ap.parse_args()

    from lcd_renderer import LCDRenderer
    if not hasattr(LCDRenderer, "_t"):
        raise SystemExit("导入了错误的 lcd_renderer（缺彩色层 _t）：%s"
                         % sys.modules["lcd_renderer"].__file__)
    fonts = init_fonts()

    drv = make_driver()
    cfg = drv.cfg_get()

    if args.png:
        os.makedirs(args.png, exist_ok=True)
        r = LCDRenderer(fonts, oled=FakeDisp(), dry_run=True)
        n = [0]

        def _save(img):
            n[0] += 1
            img.save(os.path.join(args.png, "cal_%02d.png" % n[0]))
        r._flush_p = _save
        ui = CalUI(r, cfg, _NullIO(), drv)
        # 逐页渲染
        ui.page = "menu"
        ui.draw()
        ui.page = "press"
        ui.point = 0
        ui.string = 2
        ui.draw()
        ui.point = 3
        ui.draw()
        ui.page = "strum"
        ui.strum_end = 1
        ui.draw()
        ui.page = "home"
        ui.draw()
        print("已输出 %d 张到 %s" % (n[0], args.png), flush=True)
        return

    io = TouchIO()
    print("触摸后端: %s" % io.backend, flush=True)

    if args.selftest:
        r = LCDRenderer(fonts) if not args.no_drive else LCDRenderer(fonts, oled=FakeDisp(), dry_run=True)
        ui = CalUI(r, cfg, io, drv)
        ui.run(selftest=args.selftest)
        io.close()
        return

    r = LCDRenderer(fonts)
    ui = CalUI(r, cfg, io, drv)
    try:
        ui.run()
    finally:
        try:
            ui.apply_all_home()          # 退出前把 12 片收回，别一直压着弦
        except Exception:
            pass
        io.close()


class _NullIO:
    def poll(self):
        return []

    def pressed(self, _n):
        return False

    def close(self):
        pass


if __name__ == "__main__":
    main()
