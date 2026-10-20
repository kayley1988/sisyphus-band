#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""板端主程序(ui_main.c)与左手键(input.c)静态核对

本机没有 C 编译器, 所以用"能做的"验证兜底。这里检的都是【错了就上板必挂】
的东西 —— 接线数字、和弦顺序、以及两个已修 bug 的回归防护:

  1) 默认接线核对: ui_main.c 里的 GPIO 号, 与档3 实机实证值逐个比对;
     并且反过来验算"GPIO 名 -> 全局号"的换算(GPIO3_B0 = 104 这类),
     防止手抄出错 —— 这类错误上板表现为"屏不亮"或"按键没反应", 最难查。
  2) 和弦槽位顺序: 必须等于档3 实机映射 C/G/Am/F/Dm/Em (键序即和弦序)。
  3) 回归防护:
     · mech_bridge.c 里【不得】再出现 ui_set_mech_state —— 那会让
       "就绪"被 ui_app 的"压弦中"覆盖, 界面永远卡在压弦中。
     · ui_main.c 刷屏必须走独立缓冲 g_flushbuf, 不得直接把画布指针
       交给 st7789_flush(它会就地改字节序 -> 花屏)。
  4) 语法解析: pycparser 解析 ui_main.c / input.c / mech_bridge.c。

用法:
  env -u PYTHONPATH PYTHONPATH=<site-packages> python check_ui_main.py
退出码 0 = 全过。

依据来源(硬件事实, 硬编码在下面的 REF_* 表里):
  · 档3 笔记 48_接线总表_重接用.md §2.1/§2.5 (10-10 立创官方 40PIN 表复核)
  · 档3 笔记 29_触摸条硬件验收.md  (6/6 触摸键实机通过)
  · 档3 key_test.py / lcd_check.py
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

OK, FAIL = [], []


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("OK" if cond else "XX", name,
                           ("  <- " + str(detail)) if (detail and not cond) else ""))


def strip_comments(t):
    t = re.sub(r'/\*.*?\*/', '', t, flags=re.S)
    return re.sub(r'//[^\n]*', '', t)


def read(path):
    return open(os.path.join(ROOT, path), encoding="utf-8").read()


def gpio_num(name):
    """档3 的 GPIO 名 -> RK3566 全局号。'GPIO3_B0' -> 3*32 + 1*8 + 0 = 104"""
    s = name.replace("GPIO", "")
    bank, rest = s.split("_")
    grp, idx = rest[0], rest[1:]
    return int(bank) * 32 + (ord(grp) - ord("A")) * 8 + int(idx)


# ---------------------------------------------------------------------------
# 硬件事实表(来自档3 实机笔记; 改接线时同步改这里 + ui_main.c)
# ---------------------------------------------------------------------------

# 彩屏(1.54" ST7789 240x240)。笔记 48 §2.5: DC=脚29=GPIO3_B0, RES=脚31=GPIO3_C2
# ★ 注意: 笔记里明确"此前脚本误用 114 当 DC, 已修" —— DC 是 104, 不是 114。
REF_SCREEN = [
    ("UI_DEF_GPIO_DC",  "GPIO3_B0", "脚29"),
    ("UI_DEF_GPIO_RST", "GPIO3_C2", "脚31"),
]

# 右手 4 键(机械按键接 GND)。笔记 48 §2.1 表格
REF_KEYS = [
    ("UI_DEF_KEY_UP",   "GPIO3_A1", "脚11"),
    ("UI_DEF_KEY_DOWN", "GPIO3_A3", "脚13"),
    ("UI_DEF_KEY_SET",  "GPIO3_A2", "脚15"),
    ("UI_DEF_KEY_ENT",  "GPIO3_A4", "脚16"),
]

# 左手 6 键(TTP223 触摸)。笔记 29 实机验收 6/6 通过
REF_LEFT = [
    ("C",  "GPIO1_A4", "脚7"),
    ("G",  "GPIO3_C4", "脚12"),
    ("Am", "GPIO3_A5", "脚18"),
    ("F",  "GPIO3_A6", "脚22"),
    ("Dm", "GPIO3_A7", "脚26"),
    ("Em", "GPIO3_C5", "脚32"),
]


def num_of(csrc, macro):
    """抽 `#define MACRO 104` 里的数字"""
    m = re.search(r'#define\s+%s\s+(-?\d+)' % re.escape(macro), csrc)
    return int(m.group(1)) if m else None


def main():
    print("=" * 70)
    print("板端主程序 / 左手键 静态核对")
    print("=" * 70)

    ui = strip_comments(read("src/ui_main.c"))
    inp = strip_comments(read("src/ui/input.c"))
    brg = strip_comments(read("src/mech/mech_bridge.c"))

    # ---- 1. 屏接线 ----
    print("\n— 1. 彩屏接线(1.54\" ST7789 240x240) —")
    for macro, gname, pin in REF_SCREEN:
        got = num_of(ui, macro)
        want = gpio_num(gname)
        check("%s == %d (%s = %s)" % (macro, want, pin, gname),
              got == want, "C 里写的 %s" % got)
    # 那条最容易抄错的: DC 不是 114
    check("DC 不是被误用的 114", num_of(ui, "UI_DEF_GPIO_DC") != 114,
          "若为 114 说明照抄了旧的错误脚本")

    # ---- 2. 右手 4 键 ----
    print("\n— 2. 右手 4 键(机械, 低电平有效) —")
    for macro, gname, pin in REF_KEYS:
        got = num_of(ui, macro)
        want = gpio_num(gname)
        check("%s == %d (%s = %s)" % (macro, want, pin, gname),
              got == want, "C 里写的 %s" % got)

    # ---- 3. 左手 6 键 ----
    print("\n— 3. 左手 6 键(TTP223 触摸, 高电平有效) —")
    m = re.search(r'UI_DEF_LEFT_GPIO\[GA_LEFTKEY_COUNT\]\s*=\s*\{([^}]*)\}', ui)
    got = [int(x) for x in re.findall(r'-?\d+', m.group(1))] if m else []
    want = [gpio_num(g) for _, g, _ in REF_LEFT]
    check("UI_DEF_LEFT_GPIO == %s" % want, got == want, "C 里写的 %s" % got)
    for i, (nm, gname, pin) in enumerate(REF_LEFT):
        if i < len(got):
            check("  左手键%d = %s (%s = %s)" % (i + 1, nm, pin, gname),
                  got[i] == gpio_num(gname), got[i])

    # ---- 4. 和弦槽位顺序 = 键序 ----
    print("\n— 4. 和弦槽位顺序(键序即和弦序) —")
    m = re.search(r'UI_QUICK_NAMES\[UI_QUICK_CHORDS\]\s*=\s*\{([^}]*)\}', ui)
    got_names = re.findall(r'"([^"]+)"', m.group(1)) if m else []
    want_names = [nm for nm, _, _ in REF_LEFT]
    check("UI_QUICK_NAMES == %s" % want_names, got_names == want_names,
          "C 里写的 %s" % got_names)
    check("第 5 个是 Dm(档3 丝印) 而不是 D", len(got_names) >= 5
          and got_names[4] == "Dm", got_names)

    # ---- 5. 极性: 两套键必须相反 ----
    print("\n— 5. 按键极性(两套相反, 弄错=完全不响应) —")
    check("ui_main.c 右手 icfg.active_low = 1(机械接 GND)",
          re.search(r'icfg\.active_low\s*=\s*1\s*;', ui) is not None)
    check("ui_main.c 左手 lcfg.active_low = 0(TTP223 高有效)",
          re.search(r'lcfg\.active_low\s*=\s*0\s*;', ui) is not None)
    check("input.c 左手默认 active_low = 0",
          re.search(r'int\s+active_low\s*=\s*0\s*;', inp) is not None)
    check("input.c 两处 gpio_open_line 都传 0(不交给内核反转)",
          len(re.findall(r'gpio_open_line\(\s*gpios\[i\]\s*,\s*0\s*\)', inp)) == 2,
          "找到 %d 处" % len(re.findall(
              r'gpio_open_line\(\s*gpios\[i\]\s*,\s*0\s*\)', inp)))

    # ---- 6. 回归防护 ----
    print("\n— 6. 回归防护(两个已修 bug 不许复活) —")
    check("mech_bridge.c 不再改 UI 的机构状态",
          "ui_set_mech_state" not in brg,
          "它会让'就绪'被'压弦中'覆盖 -> 界面永远卡在压弦中")
    check("ui_main.c 刷屏走独立缓冲 g_flushbuf",
          re.search(r'st7789_flush\([^;]*g_flushbuf', ui, flags=re.S) is not None)
    check("ui_main.c 不把画布指针直接传给 st7789_flush",
          not re.search(r'st7789_flush\([^;]*sc\.canvas\.px', ui, flags=re.S),
          "直接传会被就地改字节序 -> 花屏")
    check("ui_main.c 有 --test-display 点屏自检",
          "test_display" in ui and "test-display" in ui)
    check("ui_main.c 用完画布脏矩形后 invalidate",
          "ui_canvas_invalidate(&sc.canvas)" in ui)

    # ---- 7. 关键结构 ----
    print("\n— 7. 主循环关键结构 —")
    check("音频跑在独立线程(pthread_create)", "pthread_create" in ui)
    check("退出时 join 音频线程", "pthread_join" in ui)
    check("帧率用绝对时间睡眠(clock_nanosleep TIMER_ABSTIME)",
          "clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME" in ui)
    check("左手键轮询已接入主循环", "ga_leftkey_poll" in ui)
    check("退出时舵机归位 + 断使能",
          "ga_mech_home" in ui and "ga_mech_disable" in ui)
    check("无 ALSA 也能编(音频段有 #ifdef GA_HAVE_ALSA)",
          ui.count("GA_HAVE_ALSA") >= 3, ui.count("GA_HAVE_ALSA"))

    # ---- 8. pycparser 语法解析 ----
    print("\n— 8. C 语法解析 (pycparser) —")
    try:
        from pycparser import c_parser
    except ImportError:
        check("pycparser 可用", False, "未安装, 跳过语法检查")
        return 1 if FAIL else 0

    MACROS = {
        # 项目宏
        "GA_LEFTKEY_COUNT": "6", "UI_QUICK_CHORDS": "6",
        "UI_APP_LEFT_KEYS": "6", "UI_W": "240", "UI_H": "240",
        "UI_DEF_SPI_DEV": '"/dev/spidev3.0"', "UI_DEF_SPI_HZ": "40000000",
        "UI_DEF_GPIO_DC": "104", "UI_DEF_GPIO_RST": "114",
        "UI_DEF_GPIO_BLK": "(-1)", "UI_DEF_XOFF": "0", "UI_DEF_YOFF": "0",
        "UI_DEF_KEY_UP": "97", "UI_DEF_KEY_DOWN": "99",
        "UI_DEF_KEY_SET": "98", "UI_DEF_KEY_ENT": "100",
        "UI_DEF_MECH_DEV": '"/dev/ttyS3"', "UI_DEF_MECH_BAUD": "921600",
        "UI_DEF_AUDIO": '"default"', "UI_DEF_FPS": "30", "UI_DEF_SETTLE_MS": "400",
        "GA_MECH_DEFAULT_DEV": '"/dev/ttyS3"', "GA_MECH_DEFAULT_BAUD": "921600",
        "GA_MECH_CHORD_CH": "12", "GA_MECH_STRUM_CH": "6",
        "GA_MECH_STRINGS": "6", "GA_MECH_TOTAL_CH": "18",
        "GA_FRAME_MAX_TOTAL": "253", "GA_FRAME_MAX_PAYLOAD": "247",
        "GA_FRAME_SYNC0": "0xAA", "GA_FRAME_SYNC1": "0x55",
        "GA_CRC16_POLY": "0x1021",
        "GA_STRUM_ANGLE_MIN": "55.0f", "GA_STRUM_ANGLE_MAX": "125.0f",
        "GA_PRESS_ZERO": "90.0f",
        # 音频/算法
        "GA_SAMPLE_RATE": "44100", "GA_CHANNELS": "1",
        "GA_FRAME_SIZE": "1024", "GA_ANALYSIS_SIZE": "2048", "GA_YIN_TAU_MAX": "1024",
        "GA_STRING_COUNT": "6", "GA_CENTS_IN_TUNE": "5.0", "GA_CENTS_CLOSE": "15.0",
        "GA_CHROMA_FFT_N": "4096",
        # ★ 注意: 不要把枚举常量名(如 GA_BACKEND_TEMPLATE)放进来 ——
        #   它们出现在定义处会被展开成 "0 = 0", 直接把语法解析搞崩。
        # POSIX / 系统
        "SIGINT": "2", "SIGTERM": "15",
        "CLOCK_MONOTONIC": "1", "TIMER_ABSTIME": "1",
        "O_RDONLY": "0", "O_WRONLY": "1", "O_RDWR": "2",
        "SEEK_SET": "0", "EBUSY": "16", "STDIN_FILENO": "0",
    }

    PRE = (
        "typedef signed char int8_t;\n"
        "typedef short int16_t;\n"
        "typedef int int32_t;\n"
        "typedef long long int64_t;\n"
        "typedef unsigned char uint8_t;\n"
        "typedef unsigned short uint16_t;\n"
        "typedef unsigned int uint32_t;\n"
        "typedef unsigned long long uint64_t;\n"
        "typedef unsigned long size_t;\n"
        "typedef long ssize_t;\n"
        "typedef struct _FILE FILE;\n"
        "typedef int pid_t;\n"
        "struct timespec { long tv_sec; long tv_nsec; };\n"
        "typedef unsigned long pthread_t;\n"
        "typedef union { char __pad[64]; } pthread_mutex_t;\n"
        "extern int errno;\n"
        "int printf(const char *, ...);\n"
        "int fprintf(struct _FILE *, const char *, ...);\n"
        "int snprintf(char *, unsigned long, const char *, ...);\n"
        "int strcmp(const char *, const char *);\n"
        "void *memset(void *, int, unsigned long);\n"
        "void *memcpy(void *, const void *, unsigned long);\n"
        "void *calloc(unsigned long, unsigned long);\n"
        "void free(void *);\n"
        "int atoi(const char *);\n"
        "long strtol(const char *, char **, int);\n"
        "int open(const char *, int, ...);\n"
        "long read(int, void *, unsigned long);\n"
        "long write(int, const void *, unsigned long);\n"
        "long lseek(int, long, int);\n"
        "int close(int);\n"
        "int usleep(unsigned int);\n"
        "int clock_gettime(int, struct timespec *);\n"
        "int clock_nanosleep(int, int, const struct timespec *, struct timespec *);\n"
        "char *strerror(int);\n"
        "double fabs(double);\n"
        "long lround(double);\n"
        "void (*signal(int, void (*)(int)))(int);\n"
        "void *pthread_create(pthread_t *, const void *, void *(*)(void *), void *);\n"
        "int pthread_join(pthread_t, void **);\n"
        "int pthread_mutex_init(pthread_mutex_t *, const void *);\n"
        "int pthread_mutex_lock(pthread_mutex_t *);\n"
        "int pthread_mutex_unlock(pthread_mutex_t *);\n"
        "int pthread_mutex_destroy(pthread_mutex_t *);\n"
    )

    def sanitize(text):
        text = strip_comments(text)
        # 交互链验证里那个多行 SNAP 宏: 连块注释一起去掉(它内含 '\' 续行)
        text = re.sub(r'#\s*define\s+SNAP[\s\S]*?while\s*\(0\)\s*\n', '', text)
        text = re.sub(
            r'#\s*ifdef\s+__cplusplus\s*\n\s*extern\s+"C"\s*\{\s*\n\s*#\s*endif', '', text)
        text = re.sub(
            r'#\s*ifdef\s+__cplusplus\s*\n\s*\}\s*\n\s*#\s*endif', '', text)
        out = []
        in_cont = False
        for ln in text.split('\n'):
            if in_cont:
                in_cont = ln.rstrip().endswith('\\')
                continue
            if ln.lstrip().startswith('#'):
                in_cont = ln.rstrip().endswith('\\')
                continue
            out.append(ln.replace('extern "C"', ''))
        text = '\n'.join(out)
        # NEED_VAL(x); 是带参宏(参数解析里用), 文本化成空语句
        text = re.sub(r'\bNEED_VAL\s*\([^)]*\)\s*;', ';', text)
        for k, v in MACROS.items():
            text = re.sub(r'\b%s\b' % re.escape(k), v, text)
        text = re.sub(r'\bNULL\b', '((void*)0)', text)
        return text

    MECH = ["src/mech/frame_codec.h", "src/mech/chord_angles.h",
            "src/mech/mech_driver.h"]
    UIH = ["src/ui/ui_draw.h", "src/ui/ui_screen.h", "src/ui/input.h",
           "src/ui/ui_app.h",
           "src/ui/display_drv.h", "src/ui/st7789.h"]
    BRIDGE = ["src/mech/mech_bridge.h"]      # 依赖 mech_driver.h, 单独拼
    ALGO = ["include/guitar_common.h", "src/dsp/yin.h", "src/dsp/pitch_map.h",
            "src/chord/chord_db.h", "src/chord/chroma.h",   # chroma 要在 recog 之前
            "src/chord/chord_recog.h"]

    units = {
        "ui_input":       UIH + ["src/ui/input.c"],
        "ui_mech_bridge": MECH + UIH + BRIDGE + ["src/mech/mech_bridge.c"],
        "ui_main":        MECH + UIH + BRIDGE + ALGO + ["src/ui_main.c"],
    }
    parser = c_parser.CParser()
    for name, files in units.items():
        # 逐文件累积解析: 一旦失败就能指出是"哪个文件"引入的,
        # 而不是只给一个拼接后的大行号。
        acc = PRE
        bad_file, bad_err, bad_src = None, None, None
        for f in files:
            acc += "\n" + sanitize(read(f))
            try:
                parser.parse(acc, filename=f)
            except Exception as e:
                bad_file, bad_err = f, str(e)
                bad_src = sanitize(read(f))
                break
        if bad_file is None:
            check("%s 语法解析通过" % name, True)
        else:
            first = bad_err.split("\n")[0]
            tail = "\n".join("      | %s" % ln
                             for ln in bad_src.split("\n")[-14:] if ln.strip())
            check("%s 语法解析通过" % name, False,
                  "%s   [引入问题的文件: %s]\n%s" % (first, bad_file, tail))

    print("\n" + "=" * 70)
    print("结果: %d 通过 / %d 失败" % (len(OK), len(FAIL)))
    for f in FAIL:
        print("  - %s" % f)
    print("=" * 70)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
