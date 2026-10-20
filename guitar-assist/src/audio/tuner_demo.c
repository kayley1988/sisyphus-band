/*
 * tuner_demo.c - 命令行版调音器 Demo (PC / RK3566 通用)
 *
 * 两种模式:
 *   ./tuner_demo live [设备名]   -> 实时采集并打印音高 (默认 default)
 *   ./tuner_demo file <x.wav>    -> 对 WAV 逐帧分析, 验证算法精度
 *
 * file 模式是交叉编译到 RK3566 之前的"先在 PC 上验算法"的关键一步:
 * 拿手机录一段吉他音, 在 PC 上跑, 确认读数正确, 再上板子调采集链路。
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <signal.h>

#include "guitar_common.h"
#include "yin.h"
#include "pitch_map.h"
#include "wav_io.h"
#include "../../src/audio/alsa_capture.h"

static volatile int g_running = 1;
static void on_sigint(int sig) { (void)sig; g_running = 0; }

/* 把音分偏差画成一条横向偏置条,便于用户一眼看出高低 */
static void draw_cents_bar(double cents) {
    const int W = 41;                /* 奇数长度, 中间是 0 */
    char line[64];
    memset(line, '-', (size_t)W);
    line[W] = '\0';

    double clamped = ga_clampd(cents, -50.0, 50.0);
    int idx = (int)lround((clamped + 50.0) / 100.0 * (W - 1));
    if (idx < 0) idx = 0;
    if (idx >= W) idx = W - 1;

    const char *mark;
    if (fabs(cents) <= GA_CENTS_IN_TUNE)      mark = "O";  /* 准 */
    else if (cents > 0)                        mark = ">";  /* 偏高 */
    else                                       mark = "<";  /* 偏低 */

    line[idx] = mark[0];
    /* 中间标一个参考位 */
    if (line[W/2] == '-') line[W/2] = '|';
    printf("  [%s]  %+7.1f 音分\n", line, cents);
}

static void report(const ga_pitch_result_t *r) {
    if (r->freq_hz <= 0.0) {
        printf("  -- 无有效信号 (置信度 %.2f)\n", r->confidence);
        return;
    }
    printf("  %7.2f Hz  %-4s  (置信度 %.2f)  ",
           r->freq_hz, r->nearest_note, r->confidence);
    if (r->nearest_string >= 0) {
        printf("弦: %s", GA_STANDARD_TUNING[r->nearest_string].name);
        if (fabs(r->cents_offset) <= GA_CENTS_IN_TUNE) printf("  -> 准\n");
        else if (r->cents_offset > 0)                  printf("  -> 偏高, 松弦\n");
        else                                            printf("  -> 偏低, 紧弦\n");
    } else {
        printf("(非空弦音)\n");
    }
    draw_cents_bar(r->cents_offset);
}

static int run_live(const char *device) {
    ga_capture_t *cap = ga_capture_open(device, GA_SAMPLE_RATE, 256);
    if (!cap) return 1;

    ga_yin_t yin;
    if (ga_yin_init(&yin, GA_ANALYSIS_SIZE, GA_YIN_TAU_MAX,
                    (double)GA_SAMPLE_RATE) != 0) {
        fprintf(stderr, "yin 初始化失败\n");
        ga_capture_close(cap);
        return 1;
    }

    int16_t *ring = (int16_t *)calloc((size_t)GA_ANALYSIS_SIZE, sizeof(int16_t));
    int16_t *chunk = (int16_t *)calloc((size_t)GA_FRAME_SIZE, sizeof(int16_t));
    double  *scratch = (double *)calloc((size_t)GA_ANALYSIS_SIZE, sizeof(double));
    if (!ring || !chunk || !scratch) { ga_capture_close(cap); return 1; }

    /* 滑动窗: 每读 1024 帧, 把窗向前移 1024, 保留后一半做重叠。
     * 这样每 23ms 出一次结果, 既有重叠又不会重复计算太多。 */
    int fill = 0;

    signal(SIGINT, on_sigint);
    printf("\n=== 实时调音 (Ctrl-C 退出) 设备: %s ===\n\n",
           ga_capture_device(cap));

    while (g_running) {
        int n = ga_capture_read(cap, chunk, GA_FRAME_SIZE);
        if (n < 0) break;

        /* 推入环形窗 */
        if (fill + n > GA_ANALYSIS_SIZE) {
            int keep = GA_ANALYSIS_SIZE - n;
            if (keep < 0) keep = 0;
            memmove(ring, ring + (fill - keep), (size_t)keep * sizeof(int16_t));
            fill = keep;
        }
        int copy = n;
        if (fill + copy > GA_ANALYSIS_SIZE) copy = GA_ANALYSIS_SIZE - fill;
        memcpy(ring + fill, chunk, (size_t)copy * sizeof(int16_t));
        fill += copy;

        if (fill < GA_ANALYSIS_SIZE) continue;

        double conf = 0.0;
        double f = ga_yin_pitch_i16(&yin, ring, scratch, &conf);
        if (conf < 0.5) f = 0.0;   /* 置信度太低直接丢弃, 避免指针乱跳 */

        ga_pitch_result_t r;
        ga_match_pitch(f, conf, &r);
        printf("\r");
        report(&r);
    }

    printf("\n退出\n");
    free(ring); free(chunk); free(scratch);
    ga_yin_free(&yin);
    ga_capture_close(cap);
    return 0;
}

static int run_file(const char *path) {
    ga_wav_t w;
    if (ga_wav_load(path, &w) != 0) return 1;

    int n = 0;
    int16_t *mono = ga_wav_to_mono(&w, &n);
    if (!mono) { ga_wav_free(&w); return 1; }

    printf("文件: %s\n", path);
    printf("采样率: %d Hz  声道: %d  帧数: %d (%.2f 秒)\n\n",
           w.sample_rate, w.channels, n, (double)n / w.sample_rate);

    ga_yin_t yin;
    if (ga_yin_init(&yin, GA_ANALYSIS_SIZE, GA_YIN_TAU_MAX,
                    (double)w.sample_rate) != 0) {
        free(mono); ga_wav_free(&w); return 1;
    }
    double *scratch = (double *)calloc((size_t)GA_ANALYSIS_SIZE, sizeof(double));

    /* 每 0.25 秒分析一次, 覆盖整段音频 */
    int hop = w.sample_rate / 4;
    int analyzed = 0, voiced = 0;
    double freq_sum = 0.0;

    for (int off = 0; off + GA_ANALYSIS_SIZE <= n; off += hop) {
        double conf = 0.0;
        double f = ga_yin_pitch_i16(&yin, mono + off, scratch, &conf);
        if (conf < 0.5) continue;
        if (f > 0.0) { freq_sum += f; voiced++; }

        printf("t=%6.2fs  ", (double)off / w.sample_rate);
        ga_pitch_result_t r;
        ga_match_pitch(f, conf, &r);
        report(&r);
        analyzed++;
    }

    printf("\n---- 汇总 ----\n");
    printf("分析窗口: %d 个, 有效音高: %d 个\n", analyzed, voiced);
    if (voiced > 0) {
        printf("平均基频: %.2f Hz\n", freq_sum / voiced);
    }
    printf("结论: 若某根弦反复读出接近其标准频率的值, 说明 YIN 参数适配良好。\n");
    printf("      标准频率参考: ");
    for (int i = 0; i < GA_STRING_COUNT; i++) {
        printf("%s=%.2f ", GA_STANDARD_TUNING[i].note,
               GA_STANDARD_TUNING[i].freq);
    }
    printf("\n");

    free(scratch);
    ga_yin_free(&yin);
    free(mono);
    ga_wav_free(&w);
    return 0;
}

int main(int argc, char **argv) {
    printf("无障碍吉他助手 - 调音内核 v0.1\n");
    printf("平台: 泰山派 RK3566 / 通用 Linux\n\n");

    if (argc < 2) {
        printf("用法:\n");
        printf("  %s live [alsa设备]   实时调音, 设备默认 default\n", argv[0]);
        printf("  %s file <x.wav>      离线分析 WAV\n", argv[0]);
        printf("\n查看采集设备: arecord -l\n");
        return 0;
    }

    if (strcmp(argv[1], "live") == 0) {
        return run_live(argc >= 3 ? argv[2] : "default");
    } else if (strcmp(argv[1], "file") == 0 && argc >= 3) {
        return run_file(argv[2]);
    }

    fprintf(stderr, "未知命令: %s\n", argv[1]);
    return 1;
}
