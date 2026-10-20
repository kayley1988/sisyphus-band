/*
 * feedback.h - 无障碍多模态反馈
 *
 * 设计理念(这是整个项目最核心的部分, 比算法重要):
 *
 *   普通调音器靠"看指针"。但我们的目标用户包括视障用户、低视力用户、
 *   以及不方便看屏幕的演奏者。所以必须做到【闭眼也能调准】。
 *
 *   三通道反馈设计:
 *
 *   1. 音调通道(pitch cue) —— 最优雅的方案
 *      直接播放一个"参考音", 让用户用耳朵对比。
 *      听起来简单, 但这是视障乐手实际用的方法。
 *
 *   2. 节拍/提示音通道(beep cue)
 *      - 偏低: 每秒 2 次的低音"嘟"
 *      - 偏高: 每秒 4 次的高音"嘀"
 *      - 准了: 一段上升的和谐音阶("叮-咚-叮")
 *      速率和音高本身携带信息, 不看屏幕也能判断方向。
 *      心理学依据: 人耳对"节奏快慢"和"音高高低"的感知是直觉性的,
 *      不需要学习成本。
 *
 *   3. 振动通道(haptic cue)
 *      - 偏差大: 长振动(300ms), 低频(慢)
 *      - 偏差小: 短振动(80ms), 高频(快)
 *      - 准了: 一次清晰的"双击"振动
 *      适合嘈杂环境(用户听不见提示音)或听力障碍用户。
 *
 *   关键设计: 三个通道的信息编码必须【一致且有梯度】——
 *   偏差越大, 提示音越快/振动越强。用户本能地就知道往哪个方向调。
 */
#ifndef GA_FEEDBACK_H
#define GA_FEEDBACK_H

#include "guitar_common.h"

typedef enum {
    GA_FB_PITCH = 0,   /* 播放参考音 */
    GA_FB_BEEP,        /* 提示音 */
    GA_FB_HAPTIC,      /* 振动 */
    GA_FB_SPEECH,      /* 语音播报 */
    GA_FB_CHANNEL_COUNT
} ga_fb_channel_t;

/* 反馈强度等级由音分偏差自动推导 */
typedef enum {
    GA_FB_LEVEL_NONE = 0,   /* 无信号 */
    GA_FB_LEVEL_FAR,        /* |dev| > 25 音分, 差得远 */
    GA_FB_LEVEL_NEAR,       /* 8 ~ 25 音分, 接近了 */
    GA_FB_LEVEL_TUNE,       /* <= 5 音分, 准了 */
    GA_FB_LEVEL_SLIGHT      /* 5 ~ 8 音分, 差一点点 */
} ga_fb_level_t;

typedef struct {
    /* 各通道开关, UI 里由用户配置(比如嘈杂环境关掉 beep 只留振动) */
    int enable[GA_FB_CHANNEL_COUNT];

    /* 提示音: 用 ALSA 播或调用系统命令。这里存设备名 */
    char beep_device[64];

    /* 振动: Linux 上走 LED 子系统(leds 目录下的 brightness 文件)
     * 或 gpio 的 value 文件, 存路径。泰山派若有 PWM 振动马达,
     * 改这里为对应的 period/duty_cycle 路径 */
    char haptic_path[128];

    /* 上一次反馈的时间戳(ms), 用于节流, 避免提示音刷屏 */
    long long last_beep_ms;
    long long last_haptic_ms;
    long long last_pitch_ms;
    long long last_speech_ms;
} ga_feedback_t;

/* 初始化: 传入提示音播放设备(如 "hw:0,0" 或 NULL 用默认),
 * 振动设备路径(如 NULL 表示无振动硬件)。 */
int  ga_feedback_init(ga_feedback_t *fb, const char *beep_device,
                      const char *haptic_path);
void ga_feedback_close(ga_feedback_t *fb);

/* 根据调音结果驱动反馈。
 * 内部会做节流: 提示音最快 4Hz, 振动最快 3Hz, 参考音 1Hz。
 * 这是主循环里每次拿到结果后调用的入口。 */
void ga_feedback_update(ga_feedback_t *fb, const ga_pitch_result_t *r);

/* 单次动作(UI 按钮直接调用) */
void ga_feedback_play_reference(ga_feedback_t *fb, double freq_hz);
void ga_feedback_speak(ga_feedback_t *fb, const char *text);
void ga_feedback_success(ga_feedback_t *fb);   /* 调准了的庆祝反馈 */

/* 从音分偏差推导等级 */
ga_fb_level_t ga_fb_level_from_cents(double cents);

/* 取当前毫秒时间戳 */
long long ga_now_ms(void);

#endif /* GA_FEEDBACK_H */
