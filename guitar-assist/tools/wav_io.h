/*
 * wav_io.h - 极简 WAV 读写 (仅 16bit PCM 单声道/立体声)
 * 用于离线测试: 把手机录的吉他音 / 合成音 喂给调音算法验证精度。
 */
#ifndef GA_WAV_IO_H
#define GA_WAV_IO_H

#include <stdint.h>

typedef struct {
    int16_t *data;      /* 交错样本 */
    int      frames;    /* 每声道帧数 */
    int      channels;
    int      sample_rate;
} ga_wav_t;

/* 成功返回 0 */
int  ga_wav_load(const char *path, ga_wav_t *out);
void ga_wav_free(ga_wav_t *w);

/* 单声道化: 立体声混成一路, 结果写入 malloc 出来的数组 */
int16_t *ga_wav_to_mono(const ga_wav_t *w, int *out_frames);

/* 保存单声道 16bit */
int ga_wav_save_mono(const char *path, const int16_t *data, int frames,
                     int sample_rate);

#endif /* GA_WAV_IO_H */
