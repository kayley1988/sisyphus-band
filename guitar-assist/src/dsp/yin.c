/*
 * yin.c - YIN 基频检测实现
 */
#include "yin.h"
#include "guitar_common.h"

#include <stdlib.h>
#include <string.h>
#include <math.h>

int ga_yin_init(ga_yin_t *y, int buf_size, int tau_max, double sample_rate) {
    if (!y || buf_size <= 0 || tau_max <= 0) return -1;
    /* tau_max 必须小于 buf_size/2, 否则差值函数后半段无数据可用 */
    if (tau_max > buf_size / 2) tau_max = buf_size / 2;

    y->buf_size    = buf_size;
    y->tau_max     = tau_max;
    y->threshold   = 0.12;   /* 经验值: 吉他上 0.10~0.15 之间最稳 */
    y->sample_rate = sample_rate;

    y->yin_buf = (double *)calloc((size_t)tau_max, sizeof(double));
    y->frame   = (double *)calloc((size_t)tau_max, sizeof(double));
    if (!y->yin_buf || !y->frame) {
        ga_yin_free(y);
        return -1;
    }
    return 0;
}

void ga_yin_free(ga_yin_t *y) {
    if (!y) return;
    free(y->yin_buf); y->yin_buf = NULL;
    free(y->frame);   y->frame   = NULL;
}

/* 抛物线插值: 用 tau 及其左右邻点拟合极小值, 把整样本精度提升到亚样本。
 * 没有这一步, 高频弦(高音 E 329Hz)误差能到 10+ 音分。 */
static double parabolic_interp(double s0, double s1, double s2) {
    double denom = 2.0 * (2.0 * s1 - s2 - s0);
    if (fabs(denom) < 1e-12) return 0.0;
    return (s2 - s0) / denom;
}

double ga_yin_pitch(ga_yin_t *y, const double *buf, double *confidence) {
    if (confidence) *confidence = 0.0;
    if (!y || !buf) return 0.0;

    const int W = y->buf_size;
    const int tau_max = y->tau_max;

    /* ---- 步骤1: 差值函数 d(tau) ----
     * d(tau) = sum_{j=0}^{W-tau-1} (x[j] - x[j+tau])^2
     * 复杂度 O(W*tau_max), W=2048/tau_max=1024 时约 200 万次乘加,
     * A55 上单次耗时 < 1ms, 完全够用。 */
    for (int tau = 0; tau < tau_max; tau++) {
        double sum = 0.0;
        const int n = W - tau;
        for (int j = 0; j < n; j++) {
            double diff = buf[j] - buf[j + tau];
            sum += diff * diff;
        }
        y->frame[tau] = sum;
    }

    /* ---- 步骤2: 累积均值归一化 CMNDF ----
     * d'(tau) = d(tau) / ((1/tau) * sum_{k=1}^{tau} d(k))
     * 这一步是 YIN 抗八度误判的关键: 把随 tau 增大而累积的能量归一化掉。
     * 注意 tau=0 时 d'(0) 恒为 1。 */
    y->yin_buf[0] = 1.0;
    double running = 0.0;
    for (int tau = 1; tau < tau_max; tau++) {
        running += y->frame[tau];
        y->yin_buf[tau] = (running > 1e-12)
                        ? (y->frame[tau] * (double)tau / running)
                        : 1.0;
    }

    /* ---- 步骤3: 绝对阈值 + 局部极小 ----
     * 找第一个"降到阈值以下"的局部极小点。
     * 从 tau_min 开始(而不是 0)避免把直流/低频漂移当成基频。
     * tau_min 对应 1000Hz 以上直接忽略,吉他最高也就高音 E 的 329Hz,
     * 泛音顶多到 1.3kHz 左右。 */
    const int tau_min = (int)(y->sample_rate / 1500.0); /* ~29 @44.1k */
    int tau_est = -1;

    for (int tau = tau_min; tau < tau_max - 1; tau++) {
        if (y->yin_buf[tau] < y->threshold) {
            /* 往后找这个凹坑的最低点 */
            while (tau + 1 < tau_max &&
                   y->yin_buf[tau + 1] < y->yin_buf[tau]) {
                tau++;
            }
            tau_est = tau;
            break;
        }
    }

    /* 没有低于阈值的点: 退化为"取全局最小", 但置信度会打折。
     * 加这一步是为了弱奏/噪声情况下仍能给出一个估计值。 */
    if (tau_est < 0) {
        double best = 1e9;
        for (int tau = tau_min; tau < tau_max - 1; tau++) {
            if (y->yin_buf[tau] < best) { best = y->yin_buf[tau]; tau_est = tau; }
        }
        if (tau_est < 0) return 0.0;
    }

    /* ---- 步骤4: 抛物线插值 ----
     * 注意 tau_est 可能是局部极小而非全局, 插值时需防越界。 */
    double better_tau = (double)tau_est;
    if (tau_est > 0 && tau_est < tau_max - 1) {
        double s0 = y->yin_buf[tau_est - 1];
        double s1 = y->yin_buf[tau_est];
        double s2 = y->yin_buf[tau_est + 1];
        /* 只在确实是凹坑时插值 */
        if (s1 <= s0 && s1 <= s2) {
            better_tau += parabolic_interp(s0, s1, s2);
        }
    }
    if (better_tau <= 0.0) return 0.0;

    double freq = y->sample_rate / better_tau;

    /* ---- 置信度 ----
     * 直接用 1 - CMNDF(tau_est): CMNDF 越小说明周期性越强。
     * 低于 ~0.5 时结果基本不可信。 */
    double cmndf = y->yin_buf[tau_est];
    if (confidence) {
        *confidence = ga_clampd(1.0 - cmndf, 0.0, 1.0);
    }

    /* 物理边界: 吉他最高弦高音 E 329.63Hz, 但泛音检测时可能测到
     * 空弦的高八度, 所以上限放宽到 1500Hz; 下限到 60Hz(比低音 E
     * 还低一点, 兼容降弦调音如 Drop D = 73.4Hz, 甚至 C 调弦)。 */
    if (freq < 60.0 || freq > 1500.0) return 0.0;

    return freq;
}

double ga_yin_pitch_i16(ga_yin_t *y, const int16_t *pcm,
                        double *scratch, double *confidence) {
    if (!y || !pcm || !scratch) return 0.0;
    const int W = y->buf_size;
    /* int16 -> double, 归一化到 [-1,1]。CMNDF 是比值形式, 其实
     * 不归一化也不影响结果, 但归一化能避免大数相加的精度损失。 */
    const double inv = 1.0 / 32768.0;
    for (int i = 0; i < W; i++) {
        scratch[i] = (double)pcm[i] * inv;
    }
    return ga_yin_pitch(y, scratch, confidence);
}
