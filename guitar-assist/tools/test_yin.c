/*
 * test_yin.c - YIN 算法精度自测
 *
 * 原理: 用带丰富谐波的合成吉他音(基频 + 2~8 次谐波, 二次谐波刻意
 * 做得比基频还强, 复现真实吉他的"基频弱"特性)来测。
 * 这是 YIN 相对于朴素自相关的核心优势场景 —— 如果这里测不准,
 * 上板子实测一定也不准。
 */
#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <string.h>

#include "guitar_common.h"
#include "yin.h"
#include "pitch_map.h"

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

/* 合成一个"类吉他"信号: 谐波幅度 1/n^0.8, 但二次谐波额外增强 */
static void synth_guitar(int16_t *buf, int n, double f0, double sr) {
    for (int i = 0; i < n; i++) {
        double t = (double)i / sr;
        double v = 0.0;
        for (int h = 1; h <= 8; h++) {
            double amp = pow((double)h, -0.8);
            /* 模拟真实吉他: 2 次谐波常常比基频还响 */
            if (h == 2) amp *= 2.2;
            if (h == 3) amp *= 1.4;
            v += amp * sin(2.0 * M_PI * f0 * h * t);
        }
        /* 加一点白噪声, 信噪比约 30dB, 模拟真实采集环境 */
        v += 0.03 * ((double)rand() / RAND_MAX - 0.5) * 2.0;
        v *= 3000.0;   /* 缩放到 int16 量级 */
        if (v >  32767.0) v =  32767.0;
        if (v < -32768.0) v = -32768.0;
        buf[i] = (int16_t)v;
    }
}

static int test_one(double f0, ga_yin_t *yin, double *scratch, int16_t *buf) {
    synth_guitar(buf, GA_ANALYSIS_SIZE, f0, GA_SAMPLE_RATE);
    double conf = 0.0;
    double f = ga_yin_pitch_i16(yin, buf, scratch, &conf);
    double cents = (f > 0.0) ? ga_cents_diff(f, f0) : 9999.0;
    int ok = (f > 0.0) && fabs(cents) < 5.0;   /* 允许 5 音分误差 */

    printf("  目标 %8.2f Hz -> 测得 %8.2f Hz  (%+7.2f 音分)  "
           "置信度 %.2f  %s\n",
           f0, f, cents, conf, ok ? "PASS" : "FAIL");
    return ok;
}

int main(void) {
    srand(12345);

    printf("=== YIN 精度自测 (吉他标准调弦 + 延伸范围) ===\n\n");

    ga_yin_t yin;
    if (ga_yin_init(&yin, GA_ANALYSIS_SIZE, GA_YIN_TAU_MAX,
                    (double)GA_SAMPLE_RATE) != 0) {
        fprintf(stderr, "初始化失败\n");
        return 1;
    }
    double  *scratch = (double *)malloc((size_t)GA_ANALYSIS_SIZE * sizeof(double));
    int16_t *buf     = (int16_t *)malloc((size_t)GA_ANALYSIS_SIZE * sizeof(int16_t));

    int pass = 0, total = 0;

    printf("[标准调弦六根弦]\n");
    for (int i = 0; i < GA_STRING_COUNT; i++) {
        total++;
        if (test_one(GA_STANDARD_TUNING[i].freq, &yin, scratch, buf)) pass++;
    }

    printf("\n[降弦 / 变调场景]\n");
    double extra[] = {
        73.4162,   /* Drop D 的低音 D */
        65.4064,   /* C 调弦低音 C */
        123.4708,  /* 2 弦 B 的高八度参考 */
        415.3047,  /* 高音 G# */
    };
    for (size_t i = 0; i < sizeof(extra)/sizeof(extra[0]); i++) {
        total++;
        if (test_one(extra[i], &yin, scratch, buf)) pass++;
    }

    printf("\n[微小失谐检测: 模拟真实调音过程]\n");
    double detune[] = { -30.0, -12.0, -5.0, 0.0, 5.0, 12.0, 30.0 }; /* 音分 */
    for (size_t i = 0; i < sizeof(detune)/sizeof(detune[0]); i++) {
        total++;
        double target = 110.0 * pow(2.0, detune[i] / 1200.0); /* A2 弦附近 */
        printf("  偏移 %+6.1f 音分: ", detune[i]);
        synth_guitar(buf, GA_ANALYSIS_SIZE, target, GA_SAMPLE_RATE);
        double conf = 0.0;
        double f = ga_yin_pitch_i16(&yin, buf, scratch, &conf);
        double measured = (f > 0.0) ? ga_cents_diff(f, 110.0) : 9999.0;
        double err = fabs(measured - detune[i]);
        int ok = (f > 0.0) && err < 3.0;
        printf("测得 %+6.1f 音分 (误差 %.2f)  %s\n",
               measured, err, ok ? "PASS" : "FAIL");
        if (ok) pass++;
    }

    printf("\n[静音/噪声抗性: 应返回 0 或低置信度]\n");
    total++;
    memset(buf, 0, (size_t)GA_ANALYSIS_SIZE * sizeof(int16_t));
    double conf = 0.0;
    double f = ga_yin_pitch_i16(&yin, buf, scratch, &conf);
    printf("  全零输入 -> freq=%.2f conf=%.2f  %s\n",
           f, conf, (f <= 0.0 || conf < 0.5) ? "PASS" : "FAIL");
    if (f <= 0.0 || conf < 0.5) pass++;

    total++;
    for (int i = 0; i < GA_ANALYSIS_SIZE; i++) {
        buf[i] = (int16_t)((rand() % 20000) - 10000);   /* 纯白噪声 */
    }
    f = ga_yin_pitch_i16(&yin, buf, scratch, &conf);
    printf("  白噪声   -> freq=%.2f conf=%.2f  %s\n",
           f, conf, (f <= 0.0 || conf < 0.5) ? "PASS" : "FAIL");
    if (f <= 0.0 || conf < 0.5) pass++;

    printf("\n========================================\n");
    printf("结果: %d / %d 通过\n", pass, total);
    printf("========================================\n");

    free(scratch); free(buf);
    ga_yin_free(&yin);
    return (pass == total) ? 0 : 1;
}
