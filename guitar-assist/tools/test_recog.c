/*
 * test_recog.c - 和弦识别端到端验证
 *
 * 走完整链路: 合成音频 -> 喂入 ga_chord_recog_feed -> 检查输出
 * 这比 test_chroma 更接近真实使用(包含 RMS 门限、时序平滑、库查询)。
 */
#include "chord_recog.h"
#include "chord_db.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

static void synth_chord_i16(int16_t *buf, int n, const int *pcs, int n_pc,
                            double sr, double noise) {
    double *acc = (double *)calloc((size_t)n, sizeof(double));
    if (!acc) return;
    for (int pi = 0; pi < n_pc; pi++) {
        for (int oct = 0; oct < 2; oct++) {
            double f0 = 130.81 * pow(2.0, pcs[pi] / 12.0 + oct);
            if (f0 > 1500.0) continue;
            double amp = (oct == 0) ? 1.0 : 0.6;
            for (int h = 1; h <= 5; h++) {
                double f = f0 * h;
                if (f > sr / 2 - 1) break;
                double ha = amp * pow((double)h, -0.9);
                for (int i = 0; i < n; i++)
                    acc[i] += ha * sin(2.0 * M_PI * f * i / sr);
            }
        }
    }
    double mx = 1e-9;
    for (int i = 0; i < n; i++) if (fabs(acc[i]) > mx) mx = fabs(acc[i]);
    for (int i = 0; i < n; i++) {
        double v = acc[i] / mx * 0.8;
        if (noise > 0.0) v += noise * ((double)rand()/RAND_MAX - 0.5) * 2.0;
        if (v >  1.0) v =  1.0;
        if (v < -1.0) v = -1.0;
        buf[i] = (int16_t)(v * 32767.0);
    }
    free(acc);
}

int main(void) {
    srand(4242);
    printf("=== 和弦识别端到端验证 ===\n\n");

    ga_chord_recog_t cr;
    if (ga_chord_recog_init(&cr, GA_BACKEND_TEMPLATE, NULL) != 0) {
        fprintf(stderr, "初始化失败\n");
        return 1;
    }
    printf("后端: %s\n\n", cr.backend == GA_BACKEND_TEMPLATE ? "模板匹配" : "RKNN");

    struct { const char *name; int pcs[4]; int n_pc; } cases[] = {
        { "C",   {0,4,7,-1},   3 },
        { "Am",  {9,0,4,-1},   3 },
        { "G",   {7,11,2,-1},  3 },
        { "Em",  {4,7,11,-1},  3 },
        { "Dm",  {2,5,9,-1},   3 },
        { "F",   {5,9,0,-1},   3 },
        { "D",   {2,6,9,-1},   3 },
        { "E",   {4,8,11,-1},  3 },
        { "G7",  {7,11,2,5},   4 },
        { "Am7", {9,0,4,7},    4 },
    };
    int n_cases = (int)(sizeof(cases)/sizeof(cases[0]));

    int n = GA_CHROMA_FFT_N;
    int16_t *buf = (int16_t *)malloc((size_t)n * sizeof(int16_t));

    printf("[连续喂入 8 帧 (模拟 0.8 秒演奏), 观察是否稳定输出]\n\n");
    int pass = 0;
    for (int i = 0; i < n_cases; i++) {
        /* 重置识别器状态, 模拟切到新和弦 */
        ga_chord_recog_free(&cr);
        ga_chord_recog_init(&cr, GA_BACKEND_TEMPLATE, NULL);

        const ga_chord_def_t *out = NULL;
        for (int f = 0; f < 8; f++) {
            synth_chord_i16(buf, n, cases[i].pcs, cases[i].n_pc,
                            GA_SAMPLE_RATE, 0.03);
            const ga_chord_def_t *r = ga_chord_recog_feed(&cr, buf, n);
            if (r) out = r;
        }

        int ok = (out && strcmp(out->name, cases[i].name) == 0);
        printf("    期望 %-5s -> 输出 %-5s (分数 %.3f, 稳定帧 %d)  %s\n",
               cases[i].name,
               out ? out->name : "(未识别)",
               cr.current_score, cr.stable_frames,
               ok ? "PASS" : "FAIL");
        if (ok) pass++;
    }

    printf("\n[静音测试: 应不输出任何和弦]\n");
    ga_chord_recog_free(&cr);
    ga_chord_recog_init(&cr, GA_BACKEND_TEMPLATE, NULL);
    memset(buf, 0, (size_t)n * sizeof(int16_t));
    const ga_chord_def_t *out = NULL;
    for (int f = 0; f < 8; f++) {
        const ga_chord_def_t *r = ga_chord_recog_feed(&cr, buf, n);
        if (r) out = r;
    }
    int silent_ok = (out == NULL);
    printf("    全静音 -> %s  %s\n", out ? out->name : "(无输出)",
           silent_ok ? "PASS" : "FAIL");
    if (silent_ok) pass++;

    int total = n_cases + 1;
    printf("\n========================================\n");
    printf("识别准确率: %d / %d\n", pass, total);
    printf("========================================\n");

    printf("\n说明:\n");
    printf("  - 合成信号是理想情况, 真实录音准确率会下降(混响/和弦外音/杂音)。\n");
    printf("  - 低音E/A弦因 FFT 分辨率不足, 在色度图中被丢弃, 这是设计取舍。\n");
    printf("  - 真实场景建议: 用真实录音重跑, 收集混淆矩阵, 再决定是否上 CNN。\n");

    ga_chord_recog_free(&cr);
    free(buf);
    return (pass == total) ? 0 : 1;
}
