/*
 * chroma.c - 色度图特征提取实现
 *
 * FFT 自己写(迭代 radix-2), 不依赖 FFTW。原因:
 *   1. FFTW 虽快, 但在嵌入式交叉编译时需要单独编译库 + 配置,
 *      而我们只需要 4096 点的固定长度 FFT, 自写完全够用;
 *   2. 自写能避免 FFTW 的 GPL 许可问题(商业项目友好);
 *   3. 4096 点 FFT 在 A55 上约 0.3ms, 相对 93ms 的帧间隔可忽略。
 */
#include "chroma.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

/* ---------------- 自写迭代 FFT (radix-2, DIT) ---------------- */

/* 位反转置换 */
static void bit_reverse(double *re, double *im, int n) {
    int j = 0;
    for (int i = 0; i < n - 1; i++) {
        if (i < j) {
            double tr = re[i]; re[i] = re[j]; re[j] = tr;
            double ti = im[i]; im[i] = im[j]; im[j] = ti;
        }
        int m = n >> 1;
        while (m >= 1 && j >= m) { j -= m; m >>= 1; }
        j += m;
    }
}

/* 原地复数 FFT。n 必须是 2 的幂。 */
static void fft_inplace(double *re, double *im, int n,
                        const double *tw_re, const double *tw_im) {
    bit_reverse(re, im, n);

    for (int len = 2; len <= n; len <<= 1) {
        int half = len >> 1;
        int step = n / len;
        for (int i = 0; i < n; i += len) {
            for (int k = 0; k < half; k++) {
                int tw = k * step;
                double wr = tw_re[tw], wi = tw_im[tw];
                int a = i + k, b = i + k + half;
                double xr = re[b] * wr - im[b] * wi;
                double xi = re[b] * wi + im[b] * wr;
                re[b] = re[a] - xr;
                im[b] = im[a] - xi;
                re[a] += xr;
                im[a] += xi;
            }
        }
    }
}

/* ---------------- 初始化 ---------------- */

int ga_chroma_init(ga_chroma_t *c, int fft_n, double sample_rate) {
    if (!c || fft_n < 256) return -1;
    /* 必须是 2 的幂 */
    if ((fft_n & (fft_n - 1)) != 0) {
        fprintf(stderr, "[chroma] fft_n 必须是 2 的幂, 收到 %d\n", fft_n);
        return -1;
    }
    memset(c, 0, sizeof(*c));
    c->fft_n = fft_n;
    c->sample_rate = sample_rate;

    c->window   = (double *)malloc((size_t)fft_n * sizeof(double));
    c->fft_re   = (double *)malloc((size_t)fft_n * sizeof(double));
    c->fft_im   = (double *)malloc((size_t)fft_n * sizeof(double));
    c->tw_re    = (double *)malloc((size_t)(fft_n / 2) * sizeof(double));
    c->tw_im    = (double *)malloc((size_t)(fft_n / 2) * sizeof(double));
    c->bin_to_chroma = (int *)malloc((size_t)(fft_n / 2) * sizeof(int));
    c->bin_weight    = (double *)malloc((size_t)(fft_n / 2) * sizeof(double));

    if (!c->window || !c->fft_re || !c->fft_im || !c->tw_re ||
        !c->tw_im || !c->bin_to_chroma || !c->bin_weight) {
        ga_chroma_free(c);
        return -1;
    }

    /* 汉宁窗: 减少频谱泄漏。吉他这种持续音, 汉宁比矩形窗
     * 在主瓣旁边干净得多。 */
    for (int i = 0; i < fft_n; i++) {
        c->window[i] = 0.5 * (1.0 - cos(2.0 * M_PI * i / (fft_n - 1)));
    }

    /* 旋转因子表 */
    for (int k = 0; k < fft_n / 2; k++) {
        double ang = -2.0 * M_PI * k / fft_n;
        c->tw_re[k] = cos(ang);
        c->tw_im[k] = sin(ang);
    }

    /* 预计算 bin -> pitch class 映射。
     *
     * 关键: 低于某个频率的 bin 直接丢弃(设为 -1)。
     * 因为 4096 点 FFT 在 44.1kHz 下 bin 宽 ≈ 10.8Hz,
     * 而 82Hz 处的半音间隔只有 ~4.9Hz —— 一个 bin 跨了 2 个半音,
     * 这种分辨率下算出的 pitch class 是错的, 不如不要。
     * 实测从 ~150Hz(4 弦 D 以上) 开始映射才可靠。
     * 低音弦的信息靠 YIN 的单音检测 + 倍频程补足。 */
    const double min_freq = 140.0;
    const double max_freq = 1200.0;   /* 超过 1.2kHz 基本是泛音, 权重降低 */

    for (int k = 0; k < fft_n / 2; k++) {
        double f = (double)k * sample_rate / (double)fft_n;
        if (f < min_freq || f > 5000.0) {
            c->bin_to_chroma[k] = -1;
            c->bin_weight[k] = 0.0;
            continue;
        }
        /* 频率 -> MIDI 小数 -> 取整到半音 -> mod 12 */
        double midi = 69.0 + 12.0 * log2(f / 440.0);
        int pc = ((int)lround(midi)) % 12;
        if (pc < 0) pc += 12;
        c->bin_to_chroma[k] = pc;

        /* 权重设计:
         *  - 在 140~1200Hz 之间权重为 1(吉他基频/主要谐波区)
         *  - 超过 1200Hz 线性衰减, 抑制高阶泛音带来的错误音级
         *  - 对数频率轴上等效于一个带通, 突出"和弦音本体" */
        double w = 1.0;
        if (f > max_freq) {
            w = 1.0 - (f - max_freq) / (5000.0 - max_freq) * 0.9;
            if (w < 0.05) w = 0.05;
        }
        c->bin_weight[k] = w;
    }

    return 0;
}

void ga_chroma_free(ga_chroma_t *c) {
    if (!c) return;
    free(c->window);        c->window = NULL;
    free(c->fft_re);        c->fft_re = NULL;
    free(c->fft_im);        c->fft_im = NULL;
    free(c->tw_re);         c->tw_re = NULL;
    free(c->tw_im);         c->tw_im = NULL;
    free(c->bin_to_chroma); c->bin_to_chroma = NULL;
    free(c->bin_weight);    c->bin_weight = NULL;
}

int ga_chroma_frame(ga_chroma_t *c, const int16_t *pcm, double *out) {
    if (!c || !pcm || !out) return -1;
    const int N = c->fft_n;

    /* 加窗 + 归一化到 [-1,1] */
    const double inv = 1.0 / 32768.0;
    for (int i = 0; i < N; i++) {
        c->fft_re[i] = (double)pcm[i] * inv * c->window[i];
        c->fft_im[i] = 0.0;
    }

    fft_inplace(c->fft_re, c->fft_im, N, c->tw_re, c->tw_im);

    /* 累加到 12 个色度 bin。
     * 用幅度(不是功率)而不是功率, 因为幅度对弱音的敏感度更好 ——
     * 吉他扫弦时各弦响度差异大, 用功率会让弱的弦完全被淹没。 */
    for (int i = 0; i < GA_CHROMA_BINS; i++) out[i] = 0.0;

    for (int k = 1; k < N / 2; k++) {
        int pc = c->bin_to_chroma[k];
        if (pc < 0) continue;
        double mag = sqrt(c->fft_re[k] * c->fft_re[k] +
                          c->fft_im[k] * c->fft_im[k]);
        out[pc] += mag * c->bin_weight[k];
    }

    /* 归一化: 让 12 维和为 1。这样后续模板匹配/CNN 对整体音量不敏感,
     * 轻轻扫弦和用力扫弦得到同样的色度图。 */
    double sum = 0.0;
    for (int i = 0; i < GA_CHROMA_BINS; i++) sum += out[i];
    if (sum > 1e-9) {
        for (int i = 0; i < GA_CHROMA_BINS; i++) out[i] /= sum;
    }

    return 0;
}

void ga_chroma_average(const double *frames, int n_frames, double *out) {
    if (!frames || n_frames <= 0 || !out) return;
    for (int i = 0; i < GA_CHROMA_BINS; i++) out[i] = 0.0;
    for (int f = 0; f < n_frames; f++) {
        for (int i = 0; i < GA_CHROMA_BINS; i++) {
            out[i] += frames[f * GA_CHROMA_BINS + i];
        }
    }
    for (int i = 0; i < GA_CHROMA_BINS; i++) out[i] /= n_frames;
}
