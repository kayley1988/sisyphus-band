/*
 * yin.h - YIN 基频检测算法 (de Cheveigne & Kawahara, 2002)
 *
 * 为什么选 YIN 而不是自相关(ACF):
 *   ACF 在吉他上八度误判率很高 —— 弹低音 E 经常报成 E3(高八度),
 *   因为吉他谐波能量分布特殊(基频常常弱于二次谐波)。
 *   YIN 通过"累积均值归一化差函数"(CMNDF)抑制了这一点,
 *   并且带一个绝对阈值 + 抛物线插值,精度可达 1 音分以内。
 *
 * 实现完全无外部依赖,只用到 libm,方便交叉编译到 RK3566。
 */
#ifndef GA_YIN_H
#define GA_YIN_H

#include <stddef.h>
#include <stdint.h>

typedef struct {
    int    tau_max;      /* 最大延迟, 决定最低可测频率 */
    int    buf_size;     /* 分析窗长度(帧) */
    double threshold;    /* CMNDF 绝对阈值, 常用 0.10~0.15 */
    double sample_rate;

    /* 内部缓冲, 由 yin_init 分配 */
    double *yin_buf;     /* CMNDF 结果, 长度 tau_max */
    double *frame;       /* 差值函数 d(tau) 的工作区 */
} ga_yin_t;

/* 初始化。buf_size 建议 >= 2*tau_max, 否则低音精度下降。
 * 返回 0 成功, -1 失败(内存)。 */
int  ga_yin_init(ga_yin_t *y, int buf_size, int tau_max, double sample_rate);

void ga_yin_free(ga_yin_t *y);

/* 对 buf (长度 = y->buf_size) 做基频估计。
 * 返回基频 Hz; 返回 0 表示未检出(静音或噪声)。
 * *confidence 输出置信度 0~1。
 *
 * 线程安全说明: 内部有状态, 每个采集线程独占一个 ga_yin_t 实例。 */
double ga_yin_pitch(ga_yin_t *y, const double *buf, double *confidence);

/* 便捷封装: 输入 int16 PCM, 内部转 double 后调用。
 * scratch 需外部提供, 长度 = y->buf_size。 */
double ga_yin_pitch_i16(ga_yin_t *y, const int16_t *pcm,
                        double *scratch, double *confidence);

#endif /* GA_YIN_H */
