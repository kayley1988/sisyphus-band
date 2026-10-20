/*
 * chroma.h - 色度图(Chromagram)特征提取
 *
 * 这是和弦识别的关键中间层。原理:
 *
 *   1. 对音频做 STFT(短时傅里叶变换), 得到每帧的频谱
 *   2. 把频谱的每个 bin 按频率折叠到 12 个半音类别(pitch class)
 *      哪怕 C2/C3/C4/C5 频率差很多, 它们都折叠到 "C" 这一维
 *   3. 得到 12 维向量, 表示"这帧里 12 个半音各有多少能量"
 *
 *   为什么折叠到 12 维而不是保留完整频谱:
 *   - 和弦的本质是"音级组合"(C-E-G = C 和弦), 与八度无关。
 *     弹 C 和弦, 无论在哪把位, 色度图都是 C/E/G 三个峰值。
 *   - 12 维的输入维度极小, CNN 可以做得非常小, 跑在 RK3566 NPU 上
 *     毫无压力(模型 < 100KB)。
 *
 *   工程细节(踩过的坑):
 *   - 十二平均律下, A4=440, 每个半音频率比 = 2^(1/12)。
 *     计算 pitch class 用: bin_freq -> midi -> midi % 12
 *   - 低频分辨率差: 低音 E(82Hz) 相邻半音只差 ~5Hz, 而 FFT bin 宽 =
 *     sr/N = 44100/2048 ≈ 21.5Hz。算下来一个 bin 就跨了好几个半音!
 *     所以【低频必须用更长的 FFT 或对数频率重采样】。
 *     本实现用 N=4096(bin 10.8Hz) + 倍频程加权来缓解。
 */
#ifndef GA_CHROMA_H
#define GA_CHROMA_H

#include "guitar_common.h"

#define GA_CHROMA_BINS   12      /* 12 个半音类别 */
#define GA_CHROMA_FFT_N  4096    /* FFT 长度, 决定频率分辨率 */

typedef struct {
    int    fft_n;
    double sample_rate;
    /* 窗函数(汉宁窗) */
    double *window;
    /* FFT 工作缓冲(实数) */
    double *fft_re;
    double *fft_im;
    /* 频率 bin -> 12 半音类别的映射表, 预先算好避免每帧做 log */
    int    *bin_to_chroma;
    /* 可选: 倍频程加权(低频权重略高, 因为吉他基频能量集中在低频端) */
    double *bin_weight;

    /* 内部 FFT 旋转因子缓存 */
    double *tw_re;
    double *tw_im;
} ga_chroma_t;

int  ga_chroma_init(ga_chroma_t *c, int fft_n, double sample_rate);
void ga_chroma_free(ga_chroma_t *c);

/* 从 int16 PCM(长度 >= fft_n) 算出一帧色度图。
 * out 长度 GA_CHROMA_BINS, 已归一化(和为 1, 便于后续模板匹配)。
 * 返回 0 成功。 */
int ga_chroma_frame(ga_chroma_t *c, const int16_t *pcm, double *out);

/* 把多帧色度图叠起来(和弦识别用较长时间窗更稳)。
 * frames x 12 的矩阵, 沿时间轴平均。 */
void ga_chroma_average(const double *frames, int n_frames, double *out);

#endif /* GA_CHROMA_H */
