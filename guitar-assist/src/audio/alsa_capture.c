/*
 * alsa_capture.c - ALSA 采集实现
 */
#include "alsa_capture.h"
#include "guitar_common.h"

#include <alsa/asoundlib.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct ga_capture {
    snd_pcm_t *pcm;
    int        sample_rate;
    int        period_size;
    char       device[64];
};

ga_capture_t *ga_capture_open(const char *device, int sample_rate,
                              int period_size) {
    const char *dev = (device && device[0]) ? device : "default";
    if (sample_rate  <= 0) sample_rate  = GA_SAMPLE_RATE;
    if (period_size  <= 0) period_size  = 256;

    snd_pcm_t *pcm = NULL;
    int rc = snd_pcm_open(&pcm, dev, SND_PCM_STREAM_CAPTURE, 0);
    if (rc < 0) {
        fprintf(stderr, "[alsa] 打开 %s 失败: %s\n", dev, snd_strerror(rc));
        fprintf(stderr, "[alsa] 提示: 用 `arecord -l` 列出设备, "
                        "USB 声卡常见为 hw:1,0\n");
        return NULL;
    }

    snd_pcm_hw_params_t *hw = NULL;
    snd_pcm_hw_params_alloca(&hw);
    if ((rc = snd_pcm_hw_params_any(pcm, hw)) < 0) goto fail;

    /* 交错模式下单声道等价于非交错, 用 RW_INTERLEAVED 兼容性最好 */
    if ((rc = snd_pcm_hw_params_set_access(pcm, hw,
              SND_PCM_ACCESS_RW_INTERLEAVED)) < 0) goto fail;

    if ((rc = snd_pcm_hw_params_set_format(pcm, hw,
              SND_PCM_FORMAT_S16_LE)) < 0) {
        fprintf(stderr, "[alsa] 设备不支持 S16_LE\n");
        goto fail;
    }

    if ((rc = snd_pcm_hw_params_set_channels(pcm, hw, GA_CHANNELS)) < 0) {
        fprintf(stderr, "[alsa] 设备不支持单声道采集\n");
        goto fail;
    }

    /* 采样率不匹配时允许 ALSA 就近取一个, 这里读回实际值 */
    unsigned int rate = (unsigned int)sample_rate;
    if ((rc = snd_pcm_hw_params_set_rate_near(pcm, hw, &rate, 0)) < 0) goto fail;
    sample_rate = (int)rate;

    snd_pcm_uframes_t period = (snd_pcm_uframes_t)period_size;
    snd_pcm_uframes_t buffer = period * 8; /* 8 个 period 的环形缓冲 */

    snd_pcm_hw_params_set_period_size_near(pcm, hw, &period, 0);
    snd_pcm_hw_params_set_buffer_size_near(pcm, hw, &buffer);

    if ((rc = snd_pcm_hw_params(pcm, hw)) < 0) goto fail;

    /* 读回 ALSA 实际接受的参数, 后面计算音高必须用真实采样率 */
    snd_pcm_hw_params_get_period_size(hw, &period, 0);
    snd_pcm_hw_params_get_rate(hw, &rate, 0);
    sample_rate = (int)rate;

    /* 启动前先预填充, 避免刚开流时的 underrun/杂音 */
    snd_pcm_prepare(pcm);

    ga_capture_t *cap = (ga_capture_t *)calloc(1, sizeof(*cap));
    if (!cap) { snd_pcm_close(pcm); return NULL; }
    cap->pcm         = pcm;
    cap->sample_rate = sample_rate;
    cap->period_size = (int)period;
    snprintf(cap->device, sizeof(cap->device), "%s", dev);

    fprintf(stderr, "[alsa] 打开 %s 成功: %d Hz, period=%d frames (%.1f ms)\n",
            dev, sample_rate, (int)period,
            1000.0 * (double)period / (double)sample_rate);
    return cap;

fail:
    fprintf(stderr, "[alsa] 配置硬件参数失败: %s\n", snd_strerror(rc));
    snd_pcm_close(pcm);
    return NULL;
}

void ga_capture_close(ga_capture_t *cap) {
    if (!cap) return;
    if (cap->pcm) {
        snd_pcm_drop(cap->pcm);
        snd_pcm_close(cap->pcm);
    }
    free(cap);
}

int ga_capture_read(ga_capture_t *cap, int16_t *buf, int num_frames) {
    if (!cap || !buf || num_frames <= 0) return -1;

    int total = 0;
    while (total < num_frames) {
        snd_pcm_sframes_t got = snd_pcm_readi(cap->pcm, buf + total,
                                              (snd_pcm_uframes_t)(num_frames - total));
        if (got > 0) {
            total += (int)got;
            continue;
        }
        if (got == -EPIPE) {           /* overrun: 采集慢了, 恢复后继续 */
            fprintf(stderr, "[alsa] overrun, 恢复中\n");
            snd_pcm_prepare(cap->pcm);
            continue;
        }
        if (got == -ESTRPIPE) {        /* 系统挂起 */
            while (snd_pcm_resume(cap->pcm) == -EAGAIN) {
                /* 等挂起结束; 实际部署时可在这里让出 CPU */
            }
            snd_pcm_prepare(cap->pcm);
            continue;
        }
        if (got < 0) {
            fprintf(stderr, "[alsa] readi 错误: %s\n", snd_strerror((int)got));
            return -1;
        }
    }
    return total;
}

void ga_capture_flush(ga_capture_t *cap) {
    if (!cap || !cap->pcm) return;
    snd_pcm_drop(cap->pcm);
    snd_pcm_prepare(cap->pcm);
}

const char *ga_capture_device(const ga_capture_t *cap) {
    return cap ? cap->device : "(null)";
}
