/*
 * main.c - 无障碍吉他助手 主程序
 *
 * 这是把 Stage1~4 串起来的应用层。设计目标是:
 *   - 一个主循环同时处理调音和和弦识别(共用同一份采集数据, 不重复采集)
 *   - 完整的无障碍反馈(听觉/振动/语音)
 *   - 键盘操作, 方便在板子上接按钮或串口控制
 *
 * 运行模式:
 *   guitar_assist tuner              # 只做调音
 *   guitar_assist chord              # 只做和弦识别
 *   guitar_assist both               # 同时运行(默认)
 *   guitar_assist file <x.wav>       # 离线分析 wav(调音+逐段和弦)
 *
 * 键盘控制(实时模式):
 *   q / Ctrl-C   退出
 *   r            播放当前弦的参考音(调音模式)
 *   s            语音播报当前状态
 *   1~6          指定目标弦, 只对该弦做反馈
 *   0            回到自动识别弦
 *   p            切换播放模式(调音/和弦/两者)
 *
 * 关于线程模型的取舍:
 *   这里用【单线程 + 非阻塞读】而不是多线程。原因:
 *   1. RK3566 是 4 核, 但单核跑这个负载绰绰有余(每帧 < 5ms 计算);
 *   2. 单线程避免了音频线程和 UI 线程的数据竞争, 嵌入式上更可靠;
 *   3. 调试简单 —— 板子上出了问题时, 串口日志是线性的, 好排查。
 *   如果以后要加 GUI(LVGL), 再把采集+算法放一个线程, GUI 在主线程。
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <signal.h>
#include <unistd.h>
#include <fcntl.h>
#include <termios.h>
#include <sys/select.h>

#include "guitar_common.h"
#include "yin.h"
#include "pitch_map.h"
#include "alsa_capture.h"
#include "feedback.h"
#include "chord_db.h"
#include "chord_recog.h"
#include "chroma.h"
#include "wav_io.h"

typedef enum {
    MODE_TUNER = 0,
    MODE_CHORD,
    MODE_BOTH
} run_mode_t;

static volatile int g_running = 1;
static void on_sigint(int sig) { (void)sig; g_running = 0; }

/* ---------------- 终端原始模式(读单键) ---------------- */

static struct termios g_orig_termios;
static int g_term_raw = 0;

static void term_raw_on(void) {
    if (!isatty(STDIN_FILENO)) return;
    if (tcgetattr(STDIN_FILENO, &g_orig_termios) != 0) return;
    struct termios raw = g_orig_termios;
    /* 关掉回显和行缓冲, 这样按一下键就能立刻收到, 不用回车 */
    raw.c_lflag &= (unsigned)~(ICANON | ECHO);
    raw.c_cc[VMIN]  = 0;
    raw.c_cc[VTIME] = 0;
    tcsetattr(STDIN_FILENO, TCSANOW, &raw);
    g_term_raw = 1;
}

static void term_raw_off(void) {
    if (g_term_raw) {
        tcsetattr(STDIN_FILENO, TCSANOW, &g_orig_termios);
        g_term_raw = 0;
    }
}

/* 非阻塞取一个按键, 没有返回 -1 */
static int read_key(void) {
    fd_set fds;
    FD_ZERO(&fds);
    FD_SET(STDIN_FILENO, &fds);
    struct timeval tv = { 0, 0 };
    if (select(STDIN_FILENO + 1, &fds, NULL, NULL, &tv) <= 0) return -1;
    unsigned char c;
    if (read(STDIN_FILENO, &c, 1) != 1) return -1;
    return (int)c;
}

/* ---------------- 界面绘制 ---------------- */

static void draw_header(run_mode_t mode) {
    printf("\033[2J\033[H");   /* 清屏 + 光标归位 */
    printf("╔══════════════════════════════════════════════════════╗\n");
    printf("║        无障碍吉他助手  泰山派 RK3566                 ║\n");
    printf("╠══════════════════════════════════════════════════════╣\n");
    const char *m = "调音 + 和弦";
    if (mode == MODE_TUNER) m = "仅调音";
    else if (mode == MODE_CHORD) m = "仅和弦";
    printf("║ 模式: %-14s  q=退出  s=播报  p=切换     ║\n", m);
    printf("╚══════════════════════════════════════════════════════╝\n\n");
}

/* 把音分偏差画成条形。这是给"低视力"用户用的视觉通道,
 * 所以除了指针, 同时用文字明确写"偏高/偏低" —— 不要只用颜色区分,
 * 色盲用户看不出红绿差别。 */
static void draw_cents(double cents, int target_string) {
    const int W = 51;
    char line[80];
    memset(line, '-', (size_t)W);
    line[W] = '\0';

    double clamped = ga_clampd(cents, -50.0, 50.0);
    int idx = (int)lround((clamped + 50.0) / 100.0 * (W - 1));
    if (idx < 0) idx = 0;
    if (idx >= W) idx = W - 1;

    line[idx] = (fabs(cents) <= GA_CENTS_IN_TUNE) ? 'O'
              : (cents > 0) ? '>' : '<';
    if (line[W/2] == '-') line[W/2] = '|';   /* 中间 = 0 刻度 */

    printf("      %s\n", line);
    printf("      偏低 -50        0        +50 偏高     %+6.1f 音分\n",
           cents);

    if (fabs(cents) <= GA_CENTS_IN_TUNE) {
        printf("      >>> 准了 <<<\n");
    } else if (cents > 0) {
        printf("      >>> 偏高, 请松弦 <<<\n");
    } else {
        printf("      >>> 偏低, 请紧弦 <<<\n");
    }
    if (target_string >= 0) {
        printf("      (锁定 %s)\n", GA_STANDARD_TUNING[target_string].name);
    }
}

/* ---------------- 离线文件分析 ---------------- */

static int run_file(const char *path) {
    ga_wav_t w;
    if (ga_wav_load(path, &w) != 0) return 1;
    int n = 0;
    int16_t *mono = ga_wav_to_mono(&w, &n);
    if (!mono) { ga_wav_free(&w); return 1; }

    printf("=== 离线分析: %s ===\n", path);
    printf("采样率 %d Hz, %d 帧 (%.2f 秒)\n\n",
           w.sample_rate, n, (double)n / w.sample_rate);

    ga_yin_t yin;
    ga_yin_init(&yin, GA_ANALYSIS_SIZE, GA_YIN_TAU_MAX,
                (double)w.sample_rate);

    ga_chord_recog_t cr;
    ga_chord_recog_init(&cr, GA_BACKEND_TEMPLATE, NULL);

    double *scratch = (double *)calloc((size_t)GA_ANALYSIS_SIZE, sizeof(double));

    /* 每 0.5 秒出一个分析结果 */
    int hop = w.sample_rate / 2;
    int voiced = 0, chord_hits = 0, windows = 0;

    for (int off = 0; off + GA_ANALYSIS_SIZE <= n; off += hop) {
        windows++;
        double conf = 0.0;
        double f = ga_yin_pitch_i16(&yin, mono + off, scratch, &conf);
        ga_pitch_result_t pr;
        ga_match_pitch(conf >= 0.5 ? f : 0.0, conf, &pr);

        printf("t=%6.2fs  ", (double)off / w.sample_rate);
        if (pr.freq_hz > 0.0) {
            voiced++;
            printf("音高 %7.2f Hz %-4s (%+6.1f 音分)",
                   pr.freq_hz, pr.nearest_note, pr.cents_offset);
            if (pr.nearest_string >= 0) {
                printf("  [%s]", GA_STANDARD_TUNING[pr.nearest_string].name);
            }
        } else {
            printf("音高 --              ");
        }

        /* 和弦识别需要 4096 样本的窗口 */
        if (off + GA_CHROMA_FFT_N <= n) {
            const ga_chord_def_t *cd =
                ga_chord_recog_feed(&cr, mono + off, GA_CHROMA_FFT_N);
            if (cd) {
                chord_hits++;
                printf("   和弦 %s", cd->name);
            } else if (cr.current_name[0] != '-' && cr.is_voiced) {
                printf("   和弦 %s", cr.current_name);
            }
        }
        printf("\n");
    }

    printf("\n---------------- 汇总 ----------------\n");
    printf("分析窗口: %d, 有效音高: %d, 识别到和弦: %d\n",
           windows, voiced, chord_hits);
    printf("说明: 和弦识别受 8 帧(约 0.8 秒)平滑影响, 短促音符可能不触发。\n");

    free(scratch);
    ga_yin_free(&yin);
    ga_chord_recog_free(&cr);
    free(mono);
    ga_wav_free(&w);
    return 0;
}

/* ---------------- 实时主循环 ---------------- */

static int run_live(const char *device, run_mode_t mode) {
    ga_capture_t *cap = ga_capture_open(device, GA_SAMPLE_RATE, 256);
    if (!cap) {
        fprintf(stderr, "\n无法打开采集设备。\n"
                        "请先运行 dev_scan 找到正确的设备号。\n");
        return 1;
    }

    ga_yin_t yin;
    if (ga_yin_init(&yin, GA_ANALYSIS_SIZE, GA_YIN_TAU_MAX,
                    (double)GA_SAMPLE_RATE) != 0) {
        fprintf(stderr, "yin 初始化失败\n");
        ga_capture_close(cap);
        return 1;
    }

    ga_feedback_t fb;
    ga_feedback_init(&fb, NULL, NULL);

    ga_chord_recog_t cr;
    ga_chord_recog_init(&cr, GA_BACKEND_TEMPLATE, NULL);

    int16_t *chunk  = (int16_t *)calloc((size_t)GA_CHROMA_FFT_N, sizeof(int16_t));
    double  *scratch = (double *)calloc((size_t)GA_CHROMA_FFT_N, sizeof(double));
    if (!chunk || !scratch) { ga_capture_close(cap); return 1; }

    int fill = 0;
    int target_string = -1;      /* -1 = 自动 */
    const ga_chord_def_t *last_chord = NULL;
    char last_chord_name[16] = "--";

    signal(SIGINT, on_sigint);
    term_raw_on();

    if (mode != MODE_CHORD) draw_header(mode);

    while (g_running) {
        /* ---- 键盘 ---- */
        int k = read_key();
        if (k > 0) {
            if (k == 'q' || k == 3 /* Ctrl-C */) break;
            if (k == 's') {
                char buf[256];
                if (last_chord) {
                    snprintf(buf, sizeof(buf), "当前和弦 %s", last_chord->name);
                } else {
                    snprintf(buf, sizeof(buf), "当前和弦 %s", last_chord_name);
                }
                ga_feedback_speak(&fb, buf);
            } else if (k == 'r' && target_string >= 0) {
                ga_feedback_play_reference(&fb,
                    GA_STANDARD_TUNING[target_string].freq);
            } else if (k >= '1' && k <= '6') {
                target_string = k - '1';    /* '1' -> 索引 0 = 6弦 */
                char buf[128];
                snprintf(buf, sizeof(buf), "%s",
                         GA_STANDARD_TUNING[target_string].name);
                ga_feedback_speak(&fb, buf);
            } else if (k == '0') {
                target_string = -1;
                ga_feedback_speak(&fb, "自动识别");
            } else if (k == 'p') {
                mode = (run_mode_t)(((int)mode + 1) % 3);
                draw_header(mode);
            }
        }

        /* ---- 采集 ---- */
        int got = ga_capture_read(cap, chunk + fill,
                                  GA_FRAME_SIZE);
        if (got < 0) break;
        fill += got;

        /* 收集到 4096 样本(93ms)才做一轮分析。
         * 这个长度是色度图 FFT 的要求, 同时调音也能用。 */
        if (fill < GA_CHROMA_FFT_N) continue;

        /* ============ 调音 ============ */
        if (mode == MODE_TUNER || mode == MODE_BOTH) {
            double conf = 0.0;
            /* YIN 只用前 2048 样本(46ms), 比色度图窗口短,
             * 这样调音响应更快 —— 拨弦后约 46ms 就能出读数。 */
            double f = ga_yin_pitch_i16(&yin, chunk, scratch, &conf);
            if (conf < 0.5) f = 0.0;

            ga_pitch_result_t pr;
            ga_match_pitch(f, conf, &pr);

            /* 如果用户锁定了某根弦, 改用该弦做参考计算偏差 */
            if (target_string >= 0 && pr.freq_hz > 0.0) {
                pr.nearest_string = target_string;
                pr.cents_offset = ga_cents_diff(
                    pr.freq_hz, GA_STANDARD_TUNING[target_string].freq);
            }

            /* 驱动无障碍反馈 */
            ga_feedback_update(&fb, &pr);

            /* 视觉输出(给低视力用户) */
            if (mode == MODE_TUNER) {
                printf("\033[H\033[6;1H");   /* 定位到固定行, 避免刷屏 */
                if (pr.freq_hz > 0.0) {
                    printf("      检测: %7.2f Hz  音名 %-4s  置信度 %.2f"
                           "                    \n",
                           pr.freq_hz, pr.nearest_note, pr.confidence);
                    if (pr.nearest_string >= 0) {
                        printf("      最近弦: %-14s"
                               "                    \n",
                               GA_STANDARD_TUNING[pr.nearest_string].name);
                    }
                    draw_cents(pr.cents_offset, target_string);
                } else {
                    printf("      等待拨弦...                          "
                           "                              \n");
                    printf("      %52s\n", "");
                    printf("      %52s\n", "");
                    printf("      %52s\n", "");
                }
                fflush(stdout);
            }
        }

        /* ============ 和弦 ============ */
        if (mode == MODE_CHORD || mode == MODE_BOTH) {
            const ga_chord_def_t *cd =
                ga_chord_recog_feed(&cr, chunk, GA_CHROMA_FFT_N);

            /* 只在和弦变化时反馈, 避免持续刷屏/刷语音 */
            if (cd && strcmp(cd->name, last_chord_name) != 0) {
                snprintf(last_chord_name, sizeof(last_chord_name),
                         "%s", cd->name);
                last_chord = cd;

                ga_feedback_success(&fb);
                char speak[512];
                ga_chord_describe(cd, 1, speak, sizeof(speak));
                ga_feedback_speak(&fb, speak);
            } else if (!cd && cr.current_name[0] != '-' &&
                       strcmp(cr.current_name, last_chord_name) != 0) {
                snprintf(last_chord_name, sizeof(last_chord_name),
                         "%s", cr.current_name);
                last_chord = NULL;
            }

            if (mode == MODE_CHORD) {
                printf("\033[H\033[6;1H");
                if (last_chord) {
                    printf("      当前和弦: %-6s (分数 %.3f)"
                           "                    \n",
                           last_chord->name, cr.current_score);
                    printf("      %52s\n", "");
                    printf("      %52s\n", "");
                    printf("      %52s\n", "");
                } else if (strcmp(last_chord_name, "--") != 0) {
                    printf("      当前和弦: %-6s (库中无此指法)"
                           "                    \n", last_chord_name);
                    printf("      %52s\n", "");
                    printf("      %52s\n", "");
                    printf("      %52s\n", "");
                } else {
                    printf("      等待演奏...                          "
                           "                              \n");
                    printf("      %52s\n", "");
                    printf("      %52s\n", "");
                    printf("      %52s\n", "");
                }
                fflush(stdout);
            }
        }

        /* 窗向前滑动一半, 保留重叠便于平滑 */
        memmove(chunk, chunk + GA_FRAME_SIZE,
                (size_t)(GA_CHROMA_FFT_N - GA_FRAME_SIZE) * sizeof(int16_t));
        fill = GA_CHROMA_FFT_N - GA_FRAME_SIZE;
    }

    term_raw_off();
    printf("\033[2J\033[H");
    printf("已退出。\n");

    free(chunk); free(scratch);
    ga_chord_recog_free(&cr);
    ga_yin_free(&yin);
    ga_capture_close(cap);
    return 0;
}

/* ---------------- 入口 ---------------- */

static void usage(const char *prog) {
    printf("无障碍吉他助手 v0.1  目标平台: 泰山派 RK3566\n\n");
    printf("用法:\n");
    printf("  %s [both|tuner|chord] [alsa设备]   实时模式(默认 both)\n",
           prog);
    printf("  %s file <x.wav>                    离线分析\n", prog);
    printf("  %s chords                          列出所有和弦指法\n", prog);
    printf("\n示例:\n");
    printf("  %s tuner hw:1,0     只在 USB 声卡上调音\n", prog);
    printf("  %s both             调音 + 和弦识别\n", prog);
    printf("  %s file guitar.wav  离线分析录音\n", prog);
    printf("\n实时模式按键: q退出 s播报 r参考音 1-6锁定弦 0自动 p切换模式\n");
    printf("查看采集设备用: ./dev_scan 或 arecord -l\n");
}

static void list_chords(void) {
    static const char *PCN[12] = {
        "C","C#","D","D#","E","F","F#","G","G#","A","A#","B"
    };
    printf("=== 和弦库 (%d 个) ===\n\n", ga_chord_count());
    printf("%-8s %-6s %-6s %s\n", "名称", "根音", "性质", "指法(6弦->1弦)");
    printf("---------------------------------------------\n");
    for (int i = 0; i < ga_chord_count(); i++) {
        const ga_chord_def_t *c = ga_chord_at(i);
        const char *q = "";
        switch (c->quality) {
            case GA_CHORD_MAJOR: q = "大"; break;
            case GA_CHORD_MINOR: q = "小"; break;
            case GA_CHORD_SEVENTH: q = "属七"; break;
            case GA_CHORD_MINOR_SEVENTH: q = "小七"; break;
            case GA_CHORD_MAJOR_SEVENTH: q = "大七"; break;
            case GA_CHORD_SUS4: q = "挂四"; break;
            default: break;
        }
        char root[8];
        ga_midi_to_name(c->root_midi, root, sizeof(root));
        for (char *p = root; *p; p++) {
            if (*p >= '0' && *p <= '9') { *p = '\0'; break; }
        }
        printf("%-8s %-6s %-6s ", c->name, root, q);
        for (int k = 0; k < GA_STRING_COUNT; k++) {
            if (c->frets[k] < 0) printf("x  ");
            else                 printf("%-2d ", c->frets[k]);
        }
        printf("\n");
    }
    (void)PCN;
}

int main(int argc, char **argv) {
    run_mode_t mode = MODE_BOTH;
    const char *device = "default";

    if (argc >= 2) {
        if (strcmp(argv[1], "-h") == 0 || strcmp(argv[1], "--help") == 0) {
            usage(argv[0]);
            return 0;
        }
        if (strcmp(argv[1], "chords") == 0) {
            list_chords();
            return 0;
        }
        if (strcmp(argv[1], "file") == 0) {
            if (argc < 3) {
                fprintf(stderr, "file 模式需要 wav 路径\n");
                return 1;
            }
            return run_file(argv[2]);
        }
        if (strcmp(argv[1], "tuner") == 0)      mode = MODE_TUNER;
        else if (strcmp(argv[1], "chord") == 0) mode = MODE_CHORD;
        else if (strcmp(argv[1], "both") == 0)  mode = MODE_BOTH;
        else device = argv[1];   /* 直接给了设备名 */

        if (argc >= 3 && argv[2][0] != '-') device = argv[2];
    }

    return run_live(device, mode);
}
