/*
 * test_chroma.c - 色度图特征提取验证
 *
 * 验证方法: 合成已知和弦的音频(比如 C 和弦 = C+E+G 三个音同时响),
 * 看色度图是否在 C/E/G 三个位置出现峰值。
 * 这是和弦识别链路能不能work的第一道关, 必须先过。
 *
 * 顺带做一个【模板匹配基线】: 不用 CNN, 直接用 12 维色度图与理论
 * 和弦模板做余弦相似度。如果这个基线准确率已经很高, 说明 CNN 的上限
 * 也很高; 如果基线很差, 要先怀疑特征而不是模型。
 */
#include "chroma.h"
#include "chord_db.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

/* 半音名 -> 索引, C=0 */
static const char *PC_NAMES[12] = {
    "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"
};

/* 合成一个和弦: 每个组成音按 3 个八度叠加(模拟真实吉他多弦同时响) */
static void synth_chord(int16_t *buf, int n, const int *pcs, int n_pc,
                        double sr) {
    memset(buf, 0, (size_t)n * sizeof(int16_t));
    double *acc = (double *)calloc((size_t)n, sizeof(double));
    if (!acc) return;

    /* 每个音级分布在 2 个八度上, 根音区从 C3 开始 */
    for (int pi = 0; pi < n_pc; pi++) {
        for (int oct = 0; oct < 2; oct++) {
            /* 基频: C3 = 130.81Hz; pc=0(C) oct=0 -> C3, oct=1 -> C4 */
            double f0 = 130.81 * pow(2.0, pcs[pi] / 12.0 + oct);
            if (f0 > 1500.0) continue;
            double amp = (oct == 0) ? 1.0 : 0.6;

            /* 每个音带谐波 */
            for (int h = 1; h <= 5; h++) {
                double f = f0 * h;
                if (f > sr / 2 - 1) break;
                double ha = amp * pow((double)h, -0.9);
                for (int i = 0; i < n; i++) {
                    acc[i] += ha * sin(2.0 * M_PI * f * i / sr);
                }
            }
        }
    }
    /* 归一化 + 转换 */
    double mx = 1e-9;
    for (int i = 0; i < n; i++) if (fabs(acc[i]) > mx) mx = fabs(acc[i]);
    for (int i = 0; i < n; i++) {
        double v = acc[i] / mx * 0.85;
        if (v >  1.0) v =  1.0;
        if (v < -1.0) v = -1.0;
        buf[i] = (int16_t)(v * 32767.0);
    }
    free(acc);
}

/* ASCII 柱状图, 更直观 */
static void print_chroma_bars(const double *ch, const char *label) {
    printf("    %s:\n", label);
    for (int i = 0; i < 12; i++) {
        printf("      %-3s |", PC_NAMES[i]);
        int w = (int)(ch[i] * 100.0);
        for (int k = 0; k < w; k++) putchar('#');
        printf("  %.3f\n", ch[i]);
    }
}

/* 和弦理论模板: 根据性质给出应有的音级 */
static int chord_template_pcs(int root_pc, ga_chord_quality_t q, int *out) {
    switch (q) {
        case GA_CHORD_MAJOR:         out[0]=root_pc; out[1]=(root_pc+4)%12;
                                     out[2]=(root_pc+7)%12; return 3;
        case GA_CHORD_MINOR:         out[0]=root_pc; out[1]=(root_pc+3)%12;
                                     out[2]=(root_pc+7)%12; return 3;
        case GA_CHORD_SEVENTH:       out[0]=root_pc; out[1]=(root_pc+4)%12;
                                     out[2]=(root_pc+7)%12; out[3]=(root_pc+10)%12;
                                     return 4;
        case GA_CHORD_MINOR_SEVENTH: out[0]=root_pc; out[1]=(root_pc+3)%12;
                                     out[2]=(root_pc+7)%12; out[3]=(root_pc+10)%12;
                                     return 4;
        case GA_CHORD_MAJOR_SEVENTH: out[0]=root_pc; out[1]=(root_pc+4)%12;
                                     out[2]=(root_pc+7)%12; out[3]=(root_pc+11)%12;
                                     return 4;
        case GA_CHORD_SUS4:          out[0]=root_pc; out[1]=(root_pc+5)%12;
                                     out[2]=(root_pc+7)%12; return 3;
        default: return 0;
    }
}

/* 模板匹配基线: 余弦相似度 */
static double match_template(const double *chroma, int root_pc,
                             ga_chord_quality_t q, int *matched_pcs) {
    int pcs[4];
    int n = chord_template_pcs(root_pc, q, pcs);
    if (n == 0) return 0.0;

    /* 构造二值模板 */
    double tpl[12];
    for (int i = 0; i < 12; i++) tpl[i] = 0.0;
    for (int i = 0; i < n; i++) tpl[pcs[i]] = 1.0;
    if (matched_pcs) memcpy(matched_pcs, pcs, sizeof(int) * (size_t)n);

    /* 余弦相似度 */
    double dot = 0, na = 0, nb = 0;
    for (int i = 0; i < 12; i++) {
        dot += chroma[i] * tpl[i];
        na  += chroma[i] * chroma[i];
        nb  += tpl[i] * tpl[i];
    }
    if (na < 1e-12 || nb < 1e-12) return 0.0;
    return dot / (sqrt(na) * sqrt(nb));
}

int main(void) {
    printf("=== 色度图特征 + 和弦模板匹配验证 ===\n\n");

    ga_chroma_t ch;
    if (ga_chroma_init(&ch, GA_CHROMA_FFT_N, (double)GA_SAMPLE_RATE) != 0) {
        fprintf(stderr, "chroma 初始化失败\n");
        return 1;
    }

    int n = GA_CHROMA_FFT_N;
    int16_t *buf = (int16_t *)malloc((size_t)n * sizeof(int16_t));
    double chroma[GA_CHROMA_BINS];

    /* 测试用例: 用和弦库里的和弦, 取其真实组成音 */
    struct {
        const char *name;
        int pcs[4];
        int n_pc;
    } cases[] = {
        { "C",    {0,4,7,-1},       3 },
        { "Am",   {9,0,4,-1},       3 },
        { "G",    {7,11,2,-1},      3 },   /* G-B-D */
        { "Em",   {4,7,11,-1},      3 },
        { "Dm",   {2,5,9,-1},       3 },
        { "F",    {5,9,0,-1},       3 },
        { "G7",   {7,11,2,5},       4 },
        { "Am7",  {9,0,4,7},        4 },
    };
    int n_cases = (int)(sizeof(cases)/sizeof(cases[0]));

    printf("[1] 单个和弦的色度图 (音级能量分布)\n");
    for (int i = 0; i < 2; i++) {   /* 只详细打印前两个, 其他看汇总 */
        synth_chord(buf, n, cases[i].pcs, cases[i].n_pc, GA_SAMPLE_RATE);
        ga_chroma_frame(&ch, buf, chroma);
        print_chroma_bars(chroma, cases[i].name);
        printf("\n");
    }

    printf("[2] 模板匹配准确率 (基线, 无 CNN)\n");
    int hit = 0;
    for (int i = 0; i < n_cases; i++) {
        synth_chord(buf, n, cases[i].pcs, cases[i].n_pc, GA_SAMPLE_RATE);
        /* 用多帧平均, 模拟真实的多帧分析 */
        double frames[8 * GA_CHROMA_BINS];
        for (int f = 0; f < 8; f++) {
            /* 每帧加一点噪声, 测试鲁棒性 */
            for (int k = 0; k < n; k++) {
                int v = buf[k] + (rand() % 400 - 200);
                if (v >  32767) v =  32767;
                if (v < -32768) v = -32768;
                buf[k] = (int16_t)v;
            }
            ga_chroma_frame(&ch, buf, frames + f * GA_CHROMA_BINS);
        }
        double avg[GA_CHROMA_BINS];
        ga_chroma_average(frames, 8, avg);

        /* 遍历所有候选和弦找最佳匹配 */
        const char *best_name = "?";
        double best_score = -1.0;
        for (int r = 0; r < 12; r++) {
            for (int q = GA_CHORD_MAJOR; q <= GA_CHORD_SUS4; q++) {
                double s = match_template(avg, r, (ga_chord_quality_t)q, NULL);
                if (s > best_score) {
                    best_score = s;
                    /* 生成名字做对比 */
                    static char nb[16];
                    const char *suf = "";
                    switch (q) {
                        case GA_CHORD_MAJOR: suf = ""; break;
                        case GA_CHORD_MINOR: suf = "m"; break;
                        case GA_CHORD_SEVENTH: suf = "7"; break;
                        case GA_CHORD_MINOR_SEVENTH: suf = "m7"; break;
                        case GA_CHORD_MAJOR_SEVENTH: suf = "maj7"; break;
                        case GA_CHORD_SUS4: suf = "sus4"; break;
                        default: break;
                    }
                    snprintf(nb, sizeof(nb), "%s%s", PC_NAMES[r], suf);
                    best_name = nb;
                }
            }
        }
        int ok = (strcmp(best_name, cases[i].name) == 0);
        printf("    期望 %-6s -> 识别 %-6s (相似度 %.3f)  %s\n",
               cases[i].name, best_name, best_score, ok ? "PASS" : "FAIL");
        if (ok) hit++;
    }

    printf("\n    模板匹配准确率: %d/%d\n", hit, n_cases);

    printf("\n[3] 结论与建议\n");
    printf("    若基线准确率 >= 7/8: 特征提取可靠, CNN 能在此基础上做到更高;\n");
    printf("    若 < 6/8: 应先调整 chroma 的频率范围/权重, 而不是急于上 CNN。\n");

    free(buf);
    ga_chroma_free(&ch);
    return 0;
}
