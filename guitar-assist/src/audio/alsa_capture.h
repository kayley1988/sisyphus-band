/*
 * alsa_capture.h - ALSA 音频采集封装
 *
 * 设计要点(针对低延迟):
 *  1. 直接打开 "hw:" 设备, 绕过 PulseAudio/PipeWire, 省掉一层缓冲。
 *  2. period_size 设小(默认 256), 目标端到端延迟 < 30ms。
 *  3. 非阻塞 + poll, 方便和 UI 事件循环合到一个线程里。
 *
 * 设备名怎么填:
 *  - USB 声卡(推荐): "hw:1,0"  (用 `arecord -l` 查看卡号)
 *  - 板载 I2S 麦克风: "hw:0,0"
 *  - 不确定时用 "default" 能跑, 但延迟会大一些
 */
#ifndef GA_ALSA_CAPTURE_H
#define GA_ALSA_CAPTURE_H

#include <stdint.h>

typedef struct ga_capture ga_capture_t;

/* device 为 NULL 时用 "default"。
 * sample_rate=0 时用 GA_SAMPLE_RATE。
 * 返回 NULL 表示失败(设备不存在/被占用), 具体原因打到 stderr。 */
ga_capture_t *ga_capture_open(const char *device, int sample_rate, int period_size);

void ga_capture_close(ga_capture_t *cap);

/* 读取 num_frames 个 int16 单声道样本。
 * 返回实际读到的帧数, <0 表示出错。阻塞模式。 */
int ga_capture_read(ga_capture_t *cap, int16_t *buf, int num_frames);

/* 丢弃当前缓冲里已存在的数据, 用于调参后重新同步 */
void ga_capture_flush(ga_capture_t *cap);

/* 打开的设备名, 供 UI 显示/日志 */
const char *ga_capture_device(const ga_capture_t *cap);

#endif /* GA_ALSA_CAPTURE_H */
