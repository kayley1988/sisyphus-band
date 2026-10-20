/*
 * ui_main.c - 板端主程序(带屏版): 把感知层与执行层装进一个进程
 *
 * ============ 这个文件解决什么问题 ============
 *
 * 改造前, 这个项目在真板上只有【命令行版】(src/main.c):
 *   调音/和弦识别都往终端里 printf, 板子必须挂串口终端才能用;
 *   ui_app / ui_screen 只在 ui_demo / ui_preview 两个 PC 工具里跑过 ——
 *   那 240x240 的屏从来没亮过。
 *
 * 本文件把整条链装成一个可执行文件 guitar_assist_ui:
 *
 *   音频线程 ──采集──> YIN 调音 / 和弦识别 ──> 共享结果(加锁)
 *                                                │
 *   主线程  ── 右手4键 + 左手6键 ──> ui_app ──────┼──> 渲染 240x240 画布
 *                                                │         │
 *                          机构回调 <── mech_bridge ───────┘  脏矩形刷 ST7789
 *                              │
 *                              └──> /dev/ttyS3 @921600 ──> ESP32 ──> 18 路舵机
 *
 * ============ 为什么音频要单独一个线程 ============
 *
 * ga_capture_read() 是【阻塞】的(ALSA snd_pcm_readi)。若放进 UI 主循环,
 * 每读一次就卡住主线程 20~90ms, 界面立刻变幻灯片, 按键也迟滞。
 * 主循环必须保持稳定帧率, 所以"采集 + 算法"挪到独立线程, 只把【结果】
 * 通过互斥锁交给主线程。这也是 src/main.c 结尾注释里预告的做法。
 *
 * ★ 线程边界(不会互相拖慢的关键):
 *   音频线程【只】碰 yin / recog / audio_shared_t, 绝不碰 UI、屏、串口;
 *   主线程【只】碰 UI、屏、串口, 绝不碰音频缓冲。
 *   两者不共享任何对象, 只有一个 audio_shared_t 需要加锁, 临界区极小。
 *
 * ============ 屏幕尺寸 ============
 * 本程序的 UI 是 240x240(UI_W x UI_H), 对应实物 1.54" IPS / ST7789 / SPI。
 * 顶盖(v8)的开窗也是按这块屏开的。若将来换回 1.83"(240x284),
 * 改 ui_draw.h 的 UI_W/UI_H + 这里 st7789 的窗口偏移即可。
 *
 * ============ 用法 ============
 *   guitar_assist_ui                      # 全部用默认接线(见下)
 *   guitar_assist_ui --test-display       # 只点屏: 红绿蓝白黑依次整屏
 *   guitar_assist_ui --spi /dev/spidev3.0 --dc 104 --rst 114
 *   guitar_assist_ui --lk 36,116,101,102,103,117
 *   guitar_assist_ui --no-mech --no-audio # 只跑界面(不碰舵机/声卡)
 *   guitar_assist_ui -h                   # 完整参数表
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <signal.h>
#include <time.h>
#include <math.h>
#include <pthread.h>

#include "guitar_common.h"
#include "yin.h"
#include "pitch_map.h"
#include "chord_db.h"
#include "chord_recog.h"
#include "chroma.h"
#ifdef GA_HAVE_ALSA
#include "alsa_capture.h"      /* 无 ALSA 时整个音频线程不编译(见下) */
#endif

#include "ui_draw.h"
#include "ui_app.h"
#include "st7789.h"
#include "input.h"
#include "mech_driver.h"
#include "mech_bridge.h"

/* ==========================================================================
 * 默认接线 —— 全部来自档3(《拨云》)实机实测, 不是猜的
 * ==========================================================================
 *
 * 依据:
 *   · 48_接线总表_重接用.md §2.1 / §2.5   (10-10 立创官方 40PIN 表逐项复核)
 *   · 29_触摸条硬件验收.md                (6/6 触摸键实机通过, 2026-10-05)
 *   · key_test.py                         (4 菜单键实机监测)
 *
 * GPIO 全局号换算式: bank*32 + 组*8 + 序, 其中 A/B/C/D = 0/1/2/3。
 *   例 GPIO3_A1 = 3*32 + 0*8 + 1 = 97
 *
 * ★ 换板子/换接线时, 只改这一段, 或用命令行覆盖(见 usage)。
 */

/* —— 屏: 1.54" ST7789 240x240, 4 线 SPI ——
 *   DIN(MOSI)=脚19  CLK(SCLK)=脚23  CS=脚24(硬件片选, 代码不用管)
 *   DC=脚29        RES=脚31         VCC/GND/BL=脚17/14/17(3.3V 常亮) */
#define UI_DEF_SPI_DEV    "/dev/spidev3.0"   /* SPI3。找不到就 ls /dev/spidev* */
#define UI_DEF_SPI_HZ     40000000           /* 40MHz。花屏/雪花 -> 降到 12000000 */
#define UI_DEF_GPIO_DC    104                /* 脚29 = GPIO3_B0。★注意不是 114 */
#define UI_DEF_GPIO_RST   114                /* 脚31 = GPIO3_C2 */
#define UI_DEF_GPIO_BLK   (-1)               /* 背光直接接 3.3V 常亮; 接进 GPIO 才填 */
#define UI_DEF_XOFF       0                  /* 240x240 通常 (0,0) */
#define UI_DEF_YOFF       0

/* —— 右手 4 键: 机械按键一端接脚、一端接 GND, 内部上拉 -> 低电平有效 ——
 *   UP=脚11 GPIO3_A1  DN=脚13 GPIO3_A3  SET=脚15 GPIO3_A2  ESC/ENT=脚16 GPIO3_A4
 * ⚠ A3/A2 与脚号的对应【不单调】(脚13->A3, 脚15->A2), 照表填别外推。 */
#define UI_DEF_KEY_UP     97                 /* 脚11 GPIO3_A1 */
#define UI_DEF_KEY_DOWN   99                 /* 脚13 GPIO3_A3 */
#define UI_DEF_KEY_SET    98                 /* 脚15 GPIO3_A2 */
#define UI_DEF_KEY_ENT    100                /* 脚16 GPIO3_A4 */

/* —— 左手 6 键: TTP223 触摸模块 -> 高电平有效 ——
 *   C1=C=脚7 GPIO1_A4(36)     C2=G=脚12 GPIO3_C4(116)
 *   C3=Am=脚18 GPIO3_A5(101)  C4=F=脚22 GPIO3_A6(102)
 *   C5=Dm=脚26 GPIO3_A7(103)  C6=Em=脚32 GPIO3_C5(117)
 * ★ 键序即和弦序, 必须和 UI_QUICK_NAMES 一一对应。 */
static const int UI_DEF_LEFT_GPIO[GA_LEFTKEY_COUNT] = { 36, 116, 101, 102, 103, 117 };

/* —— 机构(舵机) —— */
#define UI_DEF_MECH_DEV   GA_MECH_DEFAULT_DEV    /* /dev/ttyS3 */
#define UI_DEF_MECH_BAUD  GA_MECH_DEFAULT_BAUD   /* 921600 */

/* —— 音频 —— */
#define UI_DEF_AUDIO      "default"              /* 档3 用 USB 声卡: hw:1,0 */

/* —— 其它 —— */
#define UI_DEF_FPS        30
#define UI_DEF_SETTLE_MS  400     /* "压弦中" -> "就绪" 的乐观延时(见下) */

/* 左手 6 键 -> 6 个和弦槽位。顺序必须与 UI_DEF_LEFT_GPIO 一致。
 * ★ 档3 实机映射是 [C, G, Am, F, Dm, Em] —— 第 5 个是 Dm 不是 D,
 *   而 ui_demo 的演示数据写的是 D。以硬件丝印为准, 这里用 Dm。 */
static const char *UI_QUICK_NAMES[UI_QUICK_CHORDS] = {
    "C", "G", "Am", "F", "Dm", "Em"
};

/* ==========================================================================
 * 运行配置(命令行可覆盖)
 * ========================================================================== */

typedef struct {
    const char *spi_dev;
    int  spi_hz;
    int  gpio_dc, gpio_rst, gpio_blk;
    int  x_off, y_off;

    int  key_up, key_down, key_set, key_ent;   /* -1 = 不用该键 */
    int  left_gpio[GA_LEFTKEY_COUNT];

    const char *mech_dev;
    int  mech_baud;
    const char *mech_csv;
    int  no_mech;

    const char *audio_dev;
    int  no_audio;

    int  fps;
    int  settle_ms;

    int  test_display;
    int  verbose;
} ui_cfg_t;

/* ==========================================================================
 * 全局运行状态
 * ========================================================================== */

static volatile int g_running = 1;
static void on_sigint(int sig) { (void)sig; g_running = 0; }

static long long now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (long long)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

/* 音频线程 -> 主线程 的唯一共享结构。临界区里只做标量/短串拷贝。 */
typedef struct {
    pthread_mutex_t lock;

    /* 调音 */
    int    tune_valid;
    char   note[8];
    double freq, cents, conf;
    int    string_idx;
    ui_tune_state_t tstate;

    /* 和弦识别(后台跑; 主线程只在变化时打一行日志) */
    char   chord[16];
    double chord_score;
} audio_shared_t;

static audio_shared_t g_sh;

/* 刷屏缓冲。★ 必须是独立缓冲: st7789_flush 会【就地】改字节序,
 * 直接把画布指针传进去会把画布改坏 —— 下一帧重绘同样内容时再翻一次,
 * 表现为花屏。 */
static uint16_t g_flushbuf[UI_W * UI_H];

/* 背光回调要用到屏句柄(mech_bridge 拿不到它) */
static ga_display_t *g_disp = NULL;

/* ==========================================================================
 * 屏: 点屏自检 + 脏矩形刷屏
 * ========================================================================== */

/* 全屏依次填 红/绿/蓝/白/黑, 每色停 700ms, 人眼确认即"屏通了"。
 * 这就是 docs/LCD-SETUP.md 里承诺但一直没实现的 ./guitar_assist --test-display。
 * 排查顺序见该文档第 4 步的表格。 */
static int test_display(ga_display_t *d) {
    static const struct { const char *name; uint16_t color; } SEQ[] = {
        { "红", C_RED }, { "绿", C_GREEN }, { "蓝", C_BLUE },
        { "白", C_WHITE }, { "黑", C_BLACK },
    };
    static uint16_t buf[UI_W * UI_H];

    printf("[test] 整屏依次填色, 每色 0.7 秒 —— 请盯着屏看:\n");
    for (unsigned i = 0; i < sizeof(SEQ) / sizeof(SEQ[0]); i++) {
        for (int k = 0; k < UI_W * UI_H; k++) buf[k] = SEQ[i].color;
        st7789_flush(d, 0, 0, UI_W, UI_H, buf);
        printf("  %s ...\n", SEQ[i].name);
        fflush(stdout);
        usleep(700000);
    }
    printf("[test] 五种颜色都出现了 -> 接线、SPI、DC/RES 全对 ✅\n"
           "[test] 全黑(连背光都没有) -> 先查 VCC/GND/BL 是否接 3.3V 脚17\n"
           "[test] 背光亮但全白/全黑   -> 调 --hz 降到 12000000 再试\n"
           "[test] 颜色错乱/雪花       -> SPI 太快, 同上\n"
           "[test] 红蓝互换           -> 属正常, 改 st7789.c 的 MADCTL\n");
    return 0;
}

/* 把画布的一块矩形刷到屏上 */
static void flush_rect(ga_display_t *d, ui_canvas_t *c,
                       int x, int y, int w, int h) {
    if (w <= 0 || h <= 0) return;
    for (int j = 0; j < h; j++) {
        memcpy(&g_flushbuf[(size_t)j * (size_t)w],
               &c->px[(size_t)(y + j) * UI_W + x],
               (size_t)w * sizeof(uint16_t));
    }
    st7789_flush(d, x, y, w, h, g_flushbuf);
}

/* ==========================================================================
 * 和弦槽位: 从 chord_db 取, 而不是在代码里写死 6 条
 * ========================================================================== */

/* 按 UI_QUICK_NAMES 的顺序从和弦库查出指法, 填进 UI 槽位。
 * 库里查不到的名字跳过(该槽位空着), 演奏页仍可用 Up/Down 在有效槽位间切。
 * 返回实际装载的槽位数。 */
static int load_quick_from_db(ui_screen_t *sc) {
    ui_quick_chord_t q[UI_QUICK_CHORDS];
    int n = 0;
    memset(q, 0, sizeof(q));

    for (int i = 0; i < UI_QUICK_CHORDS; i++) {
        const ga_chord_def_t *c = ga_chord_find(UI_QUICK_NAMES[i]);
        if (!c) {
            fprintf(stderr, "[ui] 和弦库里没有 '%s', 左手键%d 对应的槽位留空\n",
                    UI_QUICK_NAMES[i], i + 1);
            continue;
        }
        snprintf(q[n].name, sizeof(q[n].name), "%s", c->name);

        /* 中文名: "C" + 大 -> "C 大", "Am" + 小 -> "A 小" */
        char root[8] = "?";
        ga_midi_to_name(c->root_midi, root, sizeof(root));
        for (char *p = root; *p; p++) {
            if (*p >= '0' && *p <= '9') { *p = '\0'; break; }
        }
        const char *qual = (c->quality == GA_CHORD_MINOR) ? "小"
                         : (c->quality == GA_CHORD_SEVENTH) ? "七"
                         : (c->quality == GA_CHORD_MINOR_SEVENTH) ? "小七"
                         : (c->quality == GA_CHORD_MAJOR_SEVENTH) ? "大七"
                         : (c->quality == GA_CHORD_SUS4) ? "挂四" : "大";
        snprintf(q[n].name_cn, sizeof(q[n].name_cn), "%s %s", root, qual);

        int has_open = 0, fmin = 99, fmax = 0;
        for (int k = 0; k < GA_STRING_COUNT; k++) {
            int f = c->frets[k];
            q[n].fingering[k] = f;
            if (f == 0) has_open = 1;
            if (f > 0 && f < fmin) fmin = f;
            if (f > fmax) fmax = f;
        }
        /* 指法图范围: 有空弦就从 0 品画起, 否则从最低按品画起。
         * (和 ui_demo 的演示数据 C=0..3 / F=1..3 一致) */
        q[n].fret_min = has_open ? 0 : (fmin == 99 ? 0 : fmin);
        q[n].fret_max = fmax > 0 ? fmax : 4;
        q[n].is_minor = (c->quality == GA_CHORD_MINOR) ? 1 : 0;
        n++;
    }

    if (n > 0) ui_screen_set_quick_chords(sc, q, n);
    return n;
}

/* ==========================================================================
 * 音频线程
 * ==========================================================================
 * 没有 ALSA 的构建(纯界面调试版)整个函数不编译, 主循环里"取音频结果"
 * 会读到 g_sh 的初始值(无音高/无和弦), 调音页显示"等待拨弦" —— 界面、
 * 按键、机构三块照常工作。 */
#ifdef GA_HAVE_ALSA

static void *audio_thread(void *arg) {
    const char *dev = (const char *)arg;

    ga_capture_t *cap = ga_capture_open(dev, GA_SAMPLE_RATE, 256);
    if (!cap) {
        fprintf(stderr, "[audio] 打不开采集设备 '%s' —— 界面照常用, "
                        "只是调音页没有读数。\n"
                        "        找设备: ./dev_scan  或  arecord -l\n", dev);
        return NULL;
    }
    ga_yin_t yin;
    if (ga_yin_init(&yin, GA_ANALYSIS_SIZE, GA_YIN_TAU_MAX,
                    (double)GA_SAMPLE_RATE) != 0) {
        fprintf(stderr, "[audio] yin 初始化失败\n");
        ga_capture_close(cap);
        return NULL;
    }
    ga_chord_recog_t cr;
    ga_chord_recog_init(&cr, GA_BACKEND_TEMPLATE, NULL);

    int16_t *chunk   = (int16_t *)calloc((size_t)GA_CHROMA_FFT_N, sizeof(int16_t));
    double  *scratch = (double *)calloc((size_t)GA_CHROMA_FFT_N, sizeof(double));
    if (!chunk || !scratch) {
        free(chunk); free(scratch);
        ga_chord_recog_free(&cr);
        ga_yin_free(&yin);
        ga_capture_close(cap);
        return NULL;
    }

    printf("[audio] 已打开 %s @ %d Hz\n", ga_capture_device(cap), GA_SAMPLE_RATE);

    int fill = 0;
    while (g_running) {
        int got = ga_capture_read(cap, chunk + fill, GA_FRAME_SIZE);
        if (got < 0) break;
        fill += got;
        if (fill < GA_CHROMA_FFT_N) continue;

        /* ---- 调音: YIN 只用前 2048 样本(46ms), 响应快 ---- */
        double conf = 0.0;
        double f = ga_yin_pitch_i16(&yin, chunk, scratch, &conf);
        if (conf < 0.5) f = 0.0;
        ga_pitch_result_t pr;
        ga_match_pitch(f, conf, &pr);

        /* ---- 和弦识别 ---- */
        const ga_chord_def_t *cd =
            ga_chord_recog_feed(&cr, chunk, GA_CHROMA_FFT_N);

        pthread_mutex_lock(&g_sh.lock);
        g_sh.tune_valid = (pr.freq_hz > 0.0);
        g_sh.freq   = pr.freq_hz;
        g_sh.conf   = pr.confidence;
        g_sh.cents  = pr.cents_offset;
        g_sh.string_idx = pr.nearest_string;
        snprintf(g_sh.note, sizeof(g_sh.note), "%s",
                 pr.freq_hz > 0.0 ? pr.nearest_note : "");
        g_sh.tstate = !g_sh.tune_valid ? UI_TUNE_NONE
                    : (fabs(pr.cents_offset) <= GA_CENTS_IN_TUNE ? UI_TUNE_OK
                       : (pr.cents_offset > 0 ? UI_TUNE_HIGH : UI_TUNE_LOW));
        if (cd) {
            snprintf(g_sh.chord, sizeof(g_sh.chord), "%s", cd->name);
            g_sh.chord_score = cr.current_score;
        } else if (cr.is_voiced && cr.current_name[0] != '-') {
            snprintf(g_sh.chord, sizeof(g_sh.chord), "%s", cr.current_name);
            g_sh.chord_score = cr.current_score;
        } else {
            snprintf(g_sh.chord, sizeof(g_sh.chord), "--");
        }
        pthread_mutex_unlock(&g_sh.lock);

        /* 窗向前滑一半, 保留重叠 */
        memmove(chunk, chunk + GA_FRAME_SIZE,
                (size_t)(GA_CHROMA_FFT_N - GA_FRAME_SIZE) * sizeof(int16_t));
        fill = GA_CHROMA_FFT_N - GA_FRAME_SIZE;
    }

    free(chunk);
    free(scratch);
    ga_chord_recog_free(&cr);
    ga_yin_free(&yin);
    ga_capture_close(cap);
    return NULL;
}

#endif /* GA_HAVE_ALSA */

/* ==========================================================================
 * 背光回调: mech_bridge 里留空(它拿不到屏句柄), 这里接上
 * ========================================================================== */

static void ui_on_brightness(void *ctx, int percent) {
    (void)ctx;
    if (g_disp) st7789_backlight(g_disp, percent);
}

/* ==========================================================================
 * 主循环
 * ========================================================================== */

static int run_ui(const ui_cfg_t *cfg) {
    /* ---------- 1. 屏 ---------- */
    ga_display_t disp;
    if (st7789_open(&disp, cfg->spi_dev, cfg->spi_hz,
                    cfg->gpio_dc, cfg->gpio_rst, cfg->gpio_blk,
                    cfg->x_off, cfg->y_off) != 0) {
        fprintf(stderr,
            "\n[ui] 屏初始化失败, 无法继续。\n"
            "  先单独点屏:  %s --test-display --spi %s --dc %d --rst %d\n"
            "  常用排查:    ① ls /dev/spidev*  看节点是否存在\n"
            "               ② 设备树是否使能 SPI3 + spidev\n"
            "               ③ 是否 root(或属 dialout 组)\n"
            "  详见 docs/RUN-ON-BOARD.md\n",
            "guitar_assist_ui", cfg->spi_dev, cfg->gpio_dc, cfg->gpio_rst);
        return 1;
    }
    g_disp = &disp;
    ga_display_info(&disp);

    if (cfg->test_display) {
        int r = test_display(&disp);
        st7789_close(&disp);
        return r;
    }

    /* ---------- 2. 按键 ---------- */
    ga_input_t in;
    ga_input_cfg_t icfg;
    memset(&icfg, 0, sizeof(icfg));
    icfg.gpio_up   = cfg->key_up;
    icfg.gpio_down = cfg->key_down;
    icfg.gpio_set  = cfg->key_set;
    icfg.gpio_ent  = cfg->key_ent;
    icfg.active_low = 1;          /* ★ 机械按键接 GND: 低电平有效 */
    int have_keys = (ga_input_open(&in, &icfg) == 0);
    if (!have_keys)
        fprintf(stderr, "[ui] 右手 4 键一个都没打开 —— 只能用左手键(若有)\n");

    ga_leftkey_t left;
    ga_leftkey_cfg_t lcfg;
    memset(&lcfg, 0, sizeof(lcfg));
    for (int i = 0; i < GA_LEFTKEY_COUNT; i++) lcfg.gpio[i] = cfg->left_gpio[i];
    lcfg.active_low = 0;          /* ★ TTP223 触摸: 高电平有效 */
    int have_left = (ga_leftkey_open(&left, &lcfg) == 0);
    if (!have_left)
        fprintf(stderr, "[ui] 左手 6 键一个都没打开 —— 演奏页用 Up/Down 换和弦。\n"
                        "     需一键直达请核对接线并用 --lk 指定 GPIO。\n");
    else
        printf("[ui] 左手键已就绪: %d/6 个\n", left.count);

    /* ---------- 3. 机构 ---------- */
    ga_mech_t mech;
    if (cfg->no_mech) {
        /* 传一个肯定打不开的路径 -> 走离线模式(仍会构造帧, 只是不写串口)。
         * 注意 [mech] 离线模式的提示会打到 stderr, 属正常。 */
        ga_mech_open(&mech, "/dev/guitar_assist_no_mech", 0);
        printf("[mech] 已按 --no-mech 关闭(只构造帧, 不写串口)\n");
    } else {
        ga_mech_open(&mech, cfg->mech_dev, cfg->mech_baud);
        if (mech.fd < 0)
            fprintf(stderr, "[mech] %s 打不开或被别的进程锁住(flock) —— "
                            "机构不会动, 界面照常。\n", cfg->mech_dev);
        else
            printf("[mech] 已打开 %s @ %d, 排他锁=%d\n",
                   cfg->mech_dev, cfg->mech_baud, mech.lock_held);
    }
    if (cfg->mech_csv) {
        if (ga_mech_load_csv(&mech, cfg->mech_csv) == 0)
            printf("[mech] 已载入校准参数 %s\n", cfg->mech_csv);
        else
            fprintf(stderr, "[mech] 载入 %s 失败, 用内置默认参数\n", cfg->mech_csv);
    }
    if (!cfg->no_mech) ga_mech_enable(&mech);   /* 舵机电源使能 */

    /* ---------- 4. UI ---------- */
    ui_screen_t sc;
    ui_screen_init(&sc);
    int nq = load_quick_from_db(&sc);
    if (nq == 0)
        fprintf(stderr, "[ui] 和弦库一个都没匹配上, 演奏页将无和弦可选\n");
    else
        printf("[ui] 装载 %d 个和弦槽位:", nq);
    for (int i = 0; i < sc.state.quick_count; i++)
        printf(" %s", sc.state.quick[i].name);
    printf("\n");

    ui_app_t app;
    ui_app_init(&app, &sc);

    ga_mech_bridge_t bridge;
    ga_mech_bridge_init(&bridge, &mech, &app);

    /* 亮度: 桥里是留空的(它拿不到屏), 这里覆盖成真实现 */
    app.on_brightness = ui_on_brightness;
    app.brightness = 80;

    /* ---------- 5. 音频线程 ---------- */
    pthread_mutex_init(&g_sh.lock, NULL);
    snprintf(g_sh.chord, sizeof(g_sh.chord), "--");
    pthread_t ath;
    int have_audio = 0;
#ifdef GA_HAVE_ALSA
    if (!cfg->no_audio) {
        if (pthread_create(&ath, NULL, audio_thread,
                           (void *)cfg->audio_dev) == 0)
            have_audio = 1;
        else
            fprintf(stderr, "[audio] 线程创建失败 —— 调音页无读数\n");
    } else {
        printf("[audio] 已按 --no-audio 关闭\n");
    }
#else
    (void)ath;
    (void)cfg->audio_dev;
    printf("[audio] 本构建未启用 ALSA 采集 —— 调音页只会显示\"等待拨弦\"\n");
#endif

    signal(SIGINT, on_sigint);
    signal(SIGTERM, on_sigint);

    printf("\n[ui] 进入主循环 @ %d fps。按键说明:\n"
           "     左手 1~6 键 = 直接选和弦(并驱动机构压弦)\n"
           "     Up/Down    = 上一个/下一个和弦    Set = 演奏<->调音\n"
           "     ENT        = 重新压弦            长按 Set = 设置(亮度)\n"
           "     Ctrl-C     = 退出\n\n", cfg->fps);

    /* ---------- 6. 主循环 ---------- */
    const long long frame_ns = 1000000000LL / (cfg->fps > 0 ? cfg->fps : UI_DEF_FPS);
    struct timespec next;
    clock_gettime(CLOCK_MONOTONIC, &next);

    int  need_render = 1;
    long long mech_t0 = 0;
    char last_chord_log[16] = "--";
    /* 调音页的"变了才刷"比较基线 */
    char last_note[8] = "";
    int  last_cents_q = 1 << 20, last_si = -99, last_ts = -99;

    while (g_running) {
        /* ---- 右手 4 键 ---- */
        if (have_keys) {
            ga_key_event_t ev = ga_input_poll(&in);
            if (ev.type != GA_KEY_EV_NONE) {
                if (cfg->verbose)
                    printf("[key] %s %s\n",
                           ga_key_name(ev.key), ga_key_event_name(ev.type));
                if (ui_app_handle(&app, ev)) need_render = 1;
            }
        }
        /* ---- 左手 6 键 ---- */
        if (have_left) {
            int k = ga_leftkey_poll(&left);
            if (k >= 0) {
                const char *nm = (k < sc.state.quick_count)
                                 ? sc.state.quick[k].name : "?";
                printf("[key] 左手键%d -> %s\n", k + 1, nm);
                if (ui_app_left_key(&app, k)) need_render = 1;
            }
        }

        /* ---- 取音频结果 ---- */
        pthread_mutex_lock(&g_sh.lock);
        int  tv  = g_sh.tune_valid;
        char note[8];
        snprintf(note, sizeof(note), "%s", g_sh.note);
        double fq = g_sh.freq, ct = g_sh.cents;
        int  si = g_sh.string_idx;
        ui_tune_state_t ts = g_sh.tstate;
        char ch[16];
        snprintf(ch, sizeof(ch), "%s", g_sh.chord);
        double cscore = g_sh.chord_score;
        pthread_mutex_unlock(&g_sh.lock);

        /* 调音页: 只在读数"看得出变化"时更新, 免得每帧都全屏刷。
         * 音分量化到 1 分, 肉眼本来就分辨不出更细的差别。 */
        if (sc.state.mode == UI_MODE_TUNER) {
            int cq = (int)lround(ct);
            if (strcmp(note, last_note) != 0 || cq != last_cents_q ||
                si != last_si || (int)ts != last_ts || tv != (last_note[0] != 0)) {
                snprintf(last_note, sizeof(last_note), "%s", note);
                last_cents_q = cq;
                last_si = si;
                last_ts = (int)ts;
                ui_update_tuner(&sc, tv ? note : "", tv ? fq : 0.0, ct, si, ts);
                need_render = 1;
            }
        }

        /* 后台识别的和弦变化 -> 打一行(只在变化时, 不刷屏)。
         * 用途: 机械压弦后, 对比"识别到的和弦"和"选的和弦"是否一致。 */
        if (strcmp(ch, last_chord_log) != 0) {
            snprintf(last_chord_log, sizeof(last_chord_log), "%s", ch);
            if (ch[0] && ch[0] != '-')
                printf("[chord] 识别到 %-4s (分数 %.3f)\n", ch, cscore);
        }

        /* ---- "压弦中" -> "就绪" 的推进 ----
         * ESP32 目前【不上报到位事件】, 所以这里用固定时长乐观置位。
         * 真实到位信号接入后(见 ADAPT_TO_BOYUN.md 未做项 ④), 把这段换成
         * "收到 ESP32 事件再置位"即可, UI 侧不用改。 */
        if (sc.state.mech_moving) {
            long long t = now_ms();
            if (mech_t0 == 0) mech_t0 = t;
            else if (t - mech_t0 >= cfg->settle_ms) {
                ui_set_mech_state(&sc, 0, 1);
                mech_t0 = 0;
                need_render = 1;
            }
        } else {
            mech_t0 = 0;
        }

        /* ---- 渲染 + 刷屏 ---- */
        if (need_render || sc.dirty) {
            ui_app_render(&app);
            int x, y, w, h;
            ui_canvas_get_dirty(&sc.canvas, &x, &y, &w, &h);
            if (w > 0 && h > 0) {
                flush_rect(&disp, &sc.canvas, x, y, w, h);
                ui_canvas_invalidate(&sc.canvas);
            }
            need_render = 0;
        }

        /* ---- 帧率节拍(绝对时间睡眠, 不累积漂移) ---- */
        next.tv_nsec += frame_ns;
        while (next.tv_nsec >= 1000000000L) {
            next.tv_nsec -= 1000000000L;
            next.tv_sec++;
        }
        clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &next, NULL);
    }

    /* ---------- 7. 收尾 ---------- */
    printf("\n[ui] 退出中...\n");
    if (have_audio) pthread_join(ath, NULL);

    /* 屏清成黑, 避免残影 */
    for (int k = 0; k < UI_W * UI_H; k++) g_flushbuf[k] = C_BLACK;
    st7789_flush(&disp, 0, 0, UI_W, UI_H, g_flushbuf);

    if (!cfg->no_mech) {
        ga_mech_home(&mech);        /* 舵机平放归位 */
        ga_mech_disable(&mech);     /* 断舵机电源, 免得持续发热 */
        printf("[mech] 已归位并断使能, 共发出 %lu 帧\n", mech.tx_frames);
    }
    ga_mech_close(&mech);

    ga_leftkey_close(&left);
    if (have_keys) ga_input_close(&in);
    st7789_close(&disp);
    pthread_mutex_destroy(&g_sh.lock);
    printf("[ui] 已退出。\n");
    return 0;
}

/* ==========================================================================
 * 命令行
 * ========================================================================== */

static void usage(const char *prog) {
    printf(
"guitar_assist_ui - 无障碍吉他助手 板端主程序(带屏)\n"
"\n"
"用法: %s [选项]\n"
"\n"
"屏幕(1.54\" ST7789 240x240 / SPI):\n"
"  --spi <dev>      spidev 节点            默认 %s\n"
"  --hz <n>         SPI 时钟 Hz            默认 %d\n"
"  --dc <gpio>      DC 引脚 GPIO 号        默认 %d (脚29)\n"
"  --rst <gpio>     RES 引脚 GPIO 号       默认 %d (脚31)\n"
"  --blk <gpio>     背光 GPIO 号(-1=不控)  默认 %d\n"
"  --xoff/--yoff <n> 显存偏移              默认 0/0\n"
"  --test-display   只点屏: 红绿蓝白黑依次整屏\n"
"\n"
"按键(GPIO 全局编号):\n"
"  --up/--down/--set/--ent <gpio>   右手 4 键\n"
"              默认 %d/%d/%d/%d (脚11/13/15/16)\n"
"  --lk <g0,g1,g2,g3,g4,g5>         左手 6 键(触摸)\n"
"              默认 %d,%d,%d,%d,%d,%d (脚7/12/18/22/26/32)\n"
"\n"
"机构与音频:\n"
"  --mech <dev>     舵机串口              默认 %s\n"
"  --baud <n>       波特率                默认 %d\n"
"  --csv <file>     载入舵机校准参数(servo_config.csv)\n"
"  --no-mech        不开串口(只构造帧)\n"
"  --audio <dev>    ALSA 采集设备         默认 %s\n"
"  --no-audio       不开声卡(调音页无读数)\n"
"\n"
"其它:\n"
"  --fps <n>        渲染帧率              默认 %d\n"
"  --settle <ms>    \"压弦中\"->\"就绪\" 的乐观延时  默认 %d\n"
"  -v, --verbose    打印每次按键事件\n"
"  -h, --help       本帮助\n"
"\n"
"示例:\n"
"  %s --test-display                     # 上板第一步: 先点屏\n"
"  %s                                    # 全默认接线跑完整界面\n"
"  %s --lk 0,0,0,0,0,0 --no-mech --no-audio   # 无外设时看界面\n",
        prog, UI_DEF_SPI_DEV, UI_DEF_SPI_HZ, UI_DEF_GPIO_DC, UI_DEF_GPIO_RST,
        UI_DEF_GPIO_BLK,
        UI_DEF_KEY_UP, UI_DEF_KEY_DOWN, UI_DEF_KEY_SET, UI_DEF_KEY_ENT,
        UI_DEF_LEFT_GPIO[0], UI_DEF_LEFT_GPIO[1], UI_DEF_LEFT_GPIO[2],
        UI_DEF_LEFT_GPIO[3], UI_DEF_LEFT_GPIO[4], UI_DEF_LEFT_GPIO[5],
        UI_DEF_MECH_DEV, UI_DEF_MECH_BAUD, UI_DEF_AUDIO,
        UI_DEF_FPS, UI_DEF_SETTLE_MS,
        prog, prog, prog);
}

/* 解析 "36,116,101" 形式的 6 个 GPIO; 少于 6 个的补 -1 */
static void parse_left_gpios(const char *s, int out[GA_LEFTKEY_COUNT]) {
    for (int i = 0; i < GA_LEFTKEY_COUNT; i++) out[i] = -1;
    int i = 0;
    const char *p = s;
    while (*p && i < GA_LEFTKEY_COUNT) {
        char *end = NULL;
        long v = strtol(p, &end, 10);
        if (end == p) break;
        out[i++] = (int)v;
        p = end;
        while (*p == ',' || *p == ' ') p++;
    }
}

int main(int argc, char **argv) {
    ui_cfg_t cfg;
    memset(&cfg, 0, sizeof(cfg));
    cfg.spi_dev  = UI_DEF_SPI_DEV;
    cfg.spi_hz   = UI_DEF_SPI_HZ;
    cfg.gpio_dc  = UI_DEF_GPIO_DC;
    cfg.gpio_rst = UI_DEF_GPIO_RST;
    cfg.gpio_blk = UI_DEF_GPIO_BLK;
    cfg.x_off    = UI_DEF_XOFF;
    cfg.y_off    = UI_DEF_YOFF;
    cfg.key_up   = UI_DEF_KEY_UP;
    cfg.key_down = UI_DEF_KEY_DOWN;
    cfg.key_set  = UI_DEF_KEY_SET;
    cfg.key_ent  = UI_DEF_KEY_ENT;
    for (int i = 0; i < GA_LEFTKEY_COUNT; i++)
        cfg.left_gpio[i] = UI_DEF_LEFT_GPIO[i];
    cfg.mech_dev  = UI_DEF_MECH_DEV;
    cfg.mech_baud = UI_DEF_MECH_BAUD;
    cfg.audio_dev = UI_DEF_AUDIO;
    cfg.fps       = UI_DEF_FPS;
    cfg.settle_ms = UI_DEF_SETTLE_MS;

/* 取下一个参数(缺了就直接报错退出, 不静默用默认) */
#define NEED_VAL(opt)                                                    \
    do {                                                                 \
        if (i + 1 >= argc) {                                             \
            fprintf(stderr, "选项 %s 缺少参数\n", (opt));                 \
            return 1;                                                    \
        }                                                                \
        i++;                                                             \
    } while (0)

    for (int i = 1; i < argc; i++) {
        const char *a = argv[i];
        if (!strcmp(a, "--spi"))          { NEED_VAL(a); cfg.spi_dev = argv[i]; }
        else if (!strcmp(a, "--hz"))      { NEED_VAL(a); cfg.spi_hz = atoi(argv[i]); }
        else if (!strcmp(a, "--dc"))      { NEED_VAL(a); cfg.gpio_dc = atoi(argv[i]); }
        else if (!strcmp(a, "--rst"))     { NEED_VAL(a); cfg.gpio_rst = atoi(argv[i]); }
        else if (!strcmp(a, "--blk"))     { NEED_VAL(a); cfg.gpio_blk = atoi(argv[i]); }
        else if (!strcmp(a, "--xoff"))    { NEED_VAL(a); cfg.x_off = atoi(argv[i]); }
        else if (!strcmp(a, "--yoff"))    { NEED_VAL(a); cfg.y_off = atoi(argv[i]); }
        else if (!strcmp(a, "--test-display")) cfg.test_display = 1;
        else if (!strcmp(a, "--up"))      { NEED_VAL(a); cfg.key_up = atoi(argv[i]); }
        else if (!strcmp(a, "--down"))    { NEED_VAL(a); cfg.key_down = atoi(argv[i]); }
        else if (!strcmp(a, "--set"))     { NEED_VAL(a); cfg.key_set = atoi(argv[i]); }
        else if (!strcmp(a, "--ent"))     { NEED_VAL(a); cfg.key_ent = atoi(argv[i]); }
        else if (!strcmp(a, "--lk"))      { NEED_VAL(a); parse_left_gpios(argv[i], cfg.left_gpio); }
        else if (!strcmp(a, "--mech"))    { NEED_VAL(a); cfg.mech_dev = argv[i]; }
        else if (!strcmp(a, "--baud"))    { NEED_VAL(a); cfg.mech_baud = atoi(argv[i]); }
        else if (!strcmp(a, "--csv"))     { NEED_VAL(a); cfg.mech_csv = argv[i]; }
        else if (!strcmp(a, "--no-mech")) cfg.no_mech = 1;
        else if (!strcmp(a, "--audio"))   { NEED_VAL(a); cfg.audio_dev = argv[i]; }
        else if (!strcmp(a, "--no-audio")) cfg.no_audio = 1;
        else if (!strcmp(a, "--fps"))     { NEED_VAL(a); cfg.fps = atoi(argv[i]); }
        else if (!strcmp(a, "--settle"))  { NEED_VAL(a); cfg.settle_ms = atoi(argv[i]); }
        else if (!strcmp(a, "-v") || !strcmp(a, "--verbose")) cfg.verbose = 1;
        else if (!strcmp(a, "-h") || !strcmp(a, "--help")) { usage(argv[0]); return 0; }
        else {
            fprintf(stderr, "未知选项: %s\n\n", a);
            usage(argv[0]);
            return 1;
        }
    }
#undef NEED_VAL

    if (cfg.fps < 1)   cfg.fps = 1;
    if (cfg.fps > 120) cfg.fps = 120;

    printf("=== 无障碍吉他助手 板端主程序 ===\n");
    printf("  屏    : %s @ %d Hz  DC=GPIO%d RST=GPIO%d BLK=GPIO%d\n",
           cfg.spi_dev, cfg.spi_hz, cfg.gpio_dc, cfg.gpio_rst, cfg.gpio_blk);
    printf("  右手键: %d/%d/%d/%d\n", cfg.key_up, cfg.key_down,
           cfg.key_set, cfg.key_ent);
    printf("  左手键: ");
    for (int i = 0; i < GA_LEFTKEY_COUNT; i++)
        printf("%d%s", cfg.left_gpio[i], i == GA_LEFTKEY_COUNT - 1 ? "\n" : ",");
    printf("  机构  : %s @ %d%s\n", cfg.mech_dev, cfg.mech_baud,
           cfg.no_mech ? "  [已关闭]" : "");
    printf("  音频  : %s%s\n", cfg.audio_dev, cfg.no_audio ? "  [已关闭]" : "");

    return run_ui(&cfg);
}
