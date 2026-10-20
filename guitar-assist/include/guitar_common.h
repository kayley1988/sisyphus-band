/*
 * guitar_common.h - 无障碍吉他助手 公共定义
 *
 * 目标平台: 泰山派 RK3566 (Cortex-A55 x4 @1.8GHz)
 * 音频链路: USB 声卡(UAC免驱) / 板载I2S  ->  ALSA
 */
#ifndef GUITAR_COMMON_H
#define GUITAR_COMMON_H

#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ---------------- 音频参数 ---------------- */
/* 采样率 44100 是吉他调音的事实标准。48k 也可以,但 44.1k 与乐器谐波
 * 关系更直观。低音 E 弦 82.41Hz 要求分析窗 >= 50ms(见 YIN 说明)。 */
#define GA_SAMPLE_RATE      44100
#define GA_CHANNELS         1
/* 采集块大小: 1024 帧 ≈ 23.2ms。ALSA period 设小一些换取低延迟,
 * 端到端目标 < 30ms。 */
#define GA_FRAME_SIZE       1024
/* YIN 分析窗: 2048 帧 ≈ 46.4ms。
 * 注意: 若想稳定测低音 E(82.41Hz), 46ms 只有 3.8 个周期,勉强可用;
 * 实测跳动明显时把这里的分析窗调到 4096(93ms),代价是响应变慢。 */
#define GA_ANALYSIS_SIZE    2048
/* YIN 自相关最大延迟 tau: 决定能测的最低频率。
 * 44100 / 1024 ≈ 43Hz,低于最低音 E 弦(82.41Hz)留足余量。 */
#define GA_YIN_TAU_MAX      1024

/* ---------------- 吉他标准调弦 ---------------- */
/* 六弦 -> 一弦, 标准调弦 EADGBE */
#define GA_STRING_COUNT     6

typedef struct {
    const char *name;       /* 如 "6弦 低音E" */
    const char *note;       /* 音名 "E2" */
    double      freq;       /* 目标频率 Hz */
    int         midi;       /* MIDI 音高编号 */
} ga_string_spec_t;

extern const ga_string_spec_t GA_STANDARD_TUNING[GA_STRING_COUNT];

/* ---------------- 音高检测结果 ---------------- */
typedef struct {
    double freq_hz;         /* 检测到的基频, 0 表示无有效信号 */
    double confidence;      /* 置信度 0~1, <0.5 建议视为噪声丢弃 */
    double cents_offset;    /* 相对最近目标音的音分偏差, +偏高 -偏低 */
    int    nearest_string;  /* 最近的目标弦索引 0~5, -1 表示无 */
    int    nearest_midi;    /* 最近音名的 MIDI 编号 */
    char   nearest_note[8]; /* 最近音名, 如 "E2" */
} ga_pitch_result_t;

/* 调音判定阈值(音分) */
#define GA_CENTS_IN_TUNE    5.0   /* |dev| <= 5 视为准 */
#define GA_CENTS_CLOSE      15.0  /* 5~15 接近, >15 明显偏 */

/* ---------------- 和弦 ---------------- */
typedef enum {
    GA_CHORD_UNKNOWN = 0,
    GA_CHORD_MAJOR,
    GA_CHORD_MINOR,
    GA_CHORD_SEVENTH,
    GA_CHORD_MINOR_SEVENTH,
    GA_CHORD_MAJOR_SEVENTH,
    GA_CHORD_SUS4
} ga_chord_quality_t;

typedef struct {
    char name[16];                 /* "Am" */
    int  root_midi;                /* 根音 MIDI, 如 A2 = 45 */
    ga_chord_quality_t quality;
    /* 指法: 六根弦, 值 = 品位, -1 表示不弹(x), 0 表示空弦 */
    int  frets[GA_STRING_COUNT];
    int  fingers[GA_STRING_COUNT]; /* 建议指法 0~4, 0=不按 */
    int  base_fret;                /* 把位(用于大横按显示), 1 = 开放把位 */
    char voicing_name[32];         /* 相对音频文件名, 用于语音播报 */
} ga_chord_def_t;

static inline double ga_clampd(double v, double lo, double hi) {
    return v < lo ? lo : (v > hi ? hi : v);
}

#ifdef __cplusplus
}
#endif
#endif /* GUITAR_COMMON_H */
