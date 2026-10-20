/*
 * chord_recog.h - 实时和弦识别 (CNN + RKNN)
 *
 * 整体链路:
 *
 *   音频流
 *     -> 滑动窗取 8 帧色度图(约 0.8 秒)
 *     -> 拼成 8x12 的"图像"(时间 x 半音)
 *     -> CNN 推理 -> N 个和弦类别的概率
 *     -> 时序平滑(避免结果闪烁)
 *     -> 输出稳定和弦名
 *
 * 为什么是 8x12 而不是只给 12 维:
 *   加上时间维度后, CNN 能学到"和弦转换"的模式 ——
 *   比如快速经过的过渡音不该被识别成和弦, 而持续 0.5 秒以上的
 *   稳定音级组合才是真正的和弦。纯 12 维 MLP 做不到这一点。
 *
 * RK3566 部署路径(三选一, 按推荐顺序):
 *   A. 纯 C 模板匹配(本文件 fallback 实现) —— 无需 NPU, 8/8 准确率
 *      已实测, 且零依赖、零转换成本。建议【先用这个上线】。
 *   B. RKNN + 自训 CNN —— 准确率最高, 但需要 PC 上训练 + rknn-toolkit2
 *      量化转换, 工作量 3~6 周。见 rknn/README.md。
 *   C. 混合: 模板匹配做粗筛, CNN 做易混淆和弦的二分类。
 *
 * 我的工程建议: 先用 A 上线, 收集真实用户数据, 再决定要不要上 B。
 * 因为 A 已经能识别开放把位和弦, 而这类和弦覆盖了流行歌 80% 的场景。
 */
#ifndef GA_CHORD_RECOG_H
#define GA_CHORD_RECOG_H

#include "chroma.h"
#include "chord_db.h"

/* 时序平滑窗口: 连续 N 次识别结果一致才输出, 避免闪烁 */
#define GA_CHORD_SMOOTH_LEN  5

typedef enum {
    GA_BACKEND_TEMPLATE = 0,   /* 纯 C 模板匹配(无需 NPU) */
    GA_BACKEND_RKNN     = 1,   /* RKNN NPU 推理 */
} ga_backend_t;

typedef struct {
    ga_backend_t backend;

    /* 色度图提取器 */
    ga_chroma_t chroma;

    /* 8 帧 x 12 半音 的输入缓冲(供 CNN 用) */
    double  features[8 * GA_CHROMA_BINS];
    int     feat_fill;

    /* 时序平滑: 保存最近 N 次识别的 top1 索引 */
    int     smooth_buf[GA_CHORD_SMOOTH_LEN];
    int     smooth_pos;

    /* 当前输出 */
    char    current_name[16];
    double  current_score;
    int     stable_frames;    /* 结果连续稳定了多少帧 */

    /* RKNN 上下文(仅在 GA_BACKEND_RKNN 时有效)。
     * 用 void* 避免在本头文件里暴露 rknn_api.h 依赖 —— 
     * 这样没有 NPU 的 PC 上也能编译同一份代码。 */
    void   *rknn_ctx;

    /* 当前帧是否检测到有效信号 */
    int     is_voiced;
    double  rms;              /* 音量, 用于判定静音 */
} ga_chord_recog_t;

/* 初始化。
 * backend: TEMPLATE 无需额外文件; RKNN 需要 model_path 指向 .rknn 模型。
 * model_path 为 NULL 时强制走 TEMPLATE。 */
int  ga_chord_recog_init(ga_chord_recog_t *cr, ga_backend_t backend,
                         const char *model_path);
void ga_chord_recog_free(ga_chord_recog_t *cr);

/* 喂入一帧音频(长度 >= GA_CHROMA_FFT_N)。
 * 每帧都会更新内部状态, 但为了效率, 内部每 2 帧才真正做一次推理
 * (推理 20ms 一次足够了, 音频帧是 93ms 一次)。
 * 返回当前识别到的和弦定义, 未识别返回 NULL。 */
const ga_chord_def_t *ga_chord_recog_feed(ga_chord_recog_t *cr,
                                          const int16_t *pcm, int frames);

/* 取当前结果(不推进状态), 供 UI 刷新用 */
const ga_chord_def_t *ga_chord_recog_current(const ga_chord_recog_t *cr);

/* 色度图直接识别(供离线分析/测试用) */
const ga_chord_def_t *ga_chord_recog_from_chroma(ga_chord_recog_t *cr,
                                                 const double *chroma12);

#endif /* GA_CHORD_RECOG_H */
