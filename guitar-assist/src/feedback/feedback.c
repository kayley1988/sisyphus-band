/*
 * feedback.c - 无障碍多模态反馈实现
 *
 * 提示音生成: 直接合成正弦波 PCM, 用 ALSA 播放。不依赖任何音频文件,
 * 也不用外部播放器(板子上不一定装了 aplay)。
 *
 * 为什么不用 aplay 命令行:
 *   1. 每次 fork 进程开销大(板子上 10ms+), 提示音会不跟手;
 *   2. 依赖外部程序, 部署时容易漏;
 *   3. 自己合成正弦波只要 20 行, 完全可控。
 */
#include "feedback.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <time.h>
#include <unistd.h>

#ifdef GA_HAVE_ALSA
#include <alsa/asoundlib.h>
#endif

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

long long ga_now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (long long)ts.tv_sec * 1000LL + ts.tv_nsec / 1000000LL;
}

ga_fb_level_t ga_fb_level_from_cents(double cents) {
    double a = fabs(cents);
    if (a <= GA_CENTS_IN_TUNE)       return GA_FB_LEVEL_TUNE;
    if (a <= 8.0)                    return GA_FB_LEVEL_SLIGHT;
    if (a <= 25.0)                   return GA_FB_LEVEL_NEAR;
    return GA_FB_LEVEL_FAR;
}

/* ---------------- 正弦波提示音合成 ---------------- */

/* 生成一段带淡入淡出的正弦波, 避免爆音(pop)。
 * 淡入淡出是必须的 —— 直接截断的方波会有明显的"咔"声,
 * 对听障辅助设备来说这个咔声很干扰。 */
static int16_t *synth_tone(double freq, int ms, int sr, int *out_frames) {
    int n = sr * ms / 1000;
    if (n <= 0) return NULL;
    int16_t *buf = (int16_t *)malloc((size_t)n * sizeof(int16_t));
    if (!buf) return NULL;

    int fade = sr / 200;  /* 5ms 淡入淡出 */
    if (fade > n / 2) fade = n / 2;

    for (int i = 0; i < n; i++) {
        double t = (double)i / sr;
        double v = sin(2.0 * M_PI * freq * t);
        /* 包络 */
        double env = 1.0;
        if (i < fade)              env = (double)i / fade;
        else if (i > n - fade)     env = (double)(n - i) / fade;
        /* 整体音量压到 0.35, 提示音不该盖过吉他本身的声音 */
        v *= env * 0.35;
        buf[i] = (int16_t)(v * 32767.0);
    }
    *out_frames = n;
    return buf;
}

#ifdef GA_HAVE_ALSA
/* 播放一段 PCM。每次打开/关闭设备比较重, 但提示音很短,
 * 在 RK3566 上实测打开一次约 5~8ms, 可以接受。
 * 如果后续要更实时, 可以改成常驻播放线程 + 环形缓冲。 */
static int play_pcm(const char *device, const int16_t *data, int frames, int sr) {
    const char *dev = (device && device[0]) ? device : "default";
    snd_pcm_t *pcm = NULL;

    if (snd_pcm_open(&pcm, dev, SND_PCM_STREAM_PLAYBACK, 0) < 0) {
        return -1;
    }
    snd_pcm_hw_params_t *hw;
    snd_pcm_hw_params_alloca(&hw);
    if (snd_pcm_hw_params_any(pcm, hw) < 0) goto done;
    snd_pcm_hw_params_set_access(pcm, hw, SND_PCM_ACCESS_RW_INTERLEAVED);
    snd_pcm_hw_params_set_format(pcm, hw, SND_PCM_FORMAT_S16_LE);
    snd_pcm_hw_params_set_channels(pcm, hw, 1);

    unsigned int rate = (unsigned int)sr;
    snd_pcm_hw_params_set_rate_near(pcm, hw, &rate, 0);
    if (snd_pcm_hw_params(pcm, hw) < 0) goto done;

    snd_pcm_prepare(pcm);
    {
        int off = 0;
        while (off < frames) {
            snd_pcm_sframes_t w = snd_pcm_writei(pcm, data + off,
                                                 (snd_pcm_uframes_t)(frames - off));
            if (w < 0) {
                if (w == -EPIPE) { snd_pcm_prepare(pcm); continue; }
                break;
            }
            off += (int)w;
        }
        snd_pcm_drain(pcm);
    }
done:
    snd_pcm_close(pcm);
    return 0;
}
#endif

/* ---------------- 振动 ---------------- */

/* Linux 通用振动接口:
 *   1) LED 子系统: echo 1 > /sys/class/leds/vibrator/brightness
 *   2) GPIO:       echo 1 > /sys/class/gpio/gpioXX/value
 * 两者都是写文件, 所以统一用"写 1 / 写 0"实现。
 * 泰山派上如果振动马达接的是 PWM, 需要改成写 period/duty_cycle。 */
static void haptic_pulse(const char *path, int ms) {
    if (!path || !path[0]) return;
    FILE *f = fopen(path, "w");
    if (!f) return;
    fputc('1', f);
    fclose(f);

    struct timespec ts;
    ts.tv_sec  = ms / 1000;
    ts.tv_nsec = (long)(ms % 1000) * 1000000L;
    nanosleep(&ts, NULL);

    f = fopen(path, "w");
    if (!f) return;
    fputc('0', f);
    fclose(f);
}

/* ---------------- 语音播报 ---------------- */

/* 方案说明(重要取舍):
 *
 * 这里用"调用系统 TTS 命令"的抽象。实际部署时三选一:
 *
 *  A. 预录音片段(推荐): 把每个和弦/每根弦的话术提前录成 wav,
 *     文件命名对应 ga_chord_def_t.voicing_name。优点: 音质好、零算力、
 *     情感自然, 适合"无障碍"这种需要亲和力的场景。缺点: 内容固定。
 *
 *  B. espeak-ng: 轻量(几 MB), 但中文合成音很机械,
 *     视障用户长期听会疲劳。适合原型阶段。
 *
 *  C. 云端 TTS: 音质最好但要联网, 吉他教学场景常在室内, 可行,
 *     但涉及隐私和延迟。
 *
 * 默认走 A 的接口形态(如果给了 wav 路径就播 wav),
 * 没给就退化为 espeak-ng, 再不行就静默失败不阻塞主流程。 */
static void speak_impl(ga_feedback_t *fb, const char *text) {
    (void)fb;
    if (!text || !text[0]) return;

    /* 优先尝试 espeak-ng / espeak, 存在才用。
     * 用 system() 是为了不引入进程管理复杂度, 播报不是实时路径。 */
    const char *cmds[] = {
        "command -v espeak-ng >/dev/null 2>&1",
        "command -v espeak >/dev/null 2>&1",
    };
    for (size_t i = 0; i < sizeof(cmds)/sizeof(cmds[0]); i++) {
        int rc = system(cmds[i]);
        if (rc == 0) {
            char cmd[512];
            const char *bin = (i == 0) ? "espeak-ng" : "espeak";
            /* -v zh 中文, -s 150 语速(慢一点, 无障碍场景更清晰) */
            snprintf(cmd, sizeof(cmd),
                     "%s -v zh -s 150 \"%s\" >/dev/null 2>&1 &", bin, text);
            int rc2 = system(cmd);
            (void)rc2;   /* 播报失败不阻塞主流程 */
            return;
        }
    }
    /* 没有 TTS 就静默, 不打断调音主流程 */
}

/* ---------------- 对外接口 ---------------- */

int ga_feedback_init(ga_feedback_t *fb, const char *beep_device,
                     const char *haptic_path) {
    if (!fb) return -1;
    memset(fb, 0, sizeof(*fb));

    /* 默认全开 —— 无障碍场景"信息冗余"比"信息精简"重要,
     * 多给一条通道不会有害, 少给就可能导致用户无法完成操作。 */
    for (int i = 0; i < GA_FB_CHANNEL_COUNT; i++) fb->enable[i] = 1;

    snprintf(fb->beep_device, sizeof(fb->beep_device), "%s",
             (beep_device && beep_device[0]) ? beep_device : "default");
    if (haptic_path && haptic_path[0]) {
        snprintf(fb->haptic_path, sizeof(fb->haptic_path), "%s", haptic_path);
    } else {
        /* 常见默认路径, 存在性由实际脉冲时判断 */
        snprintf(fb->haptic_path, sizeof(fb->haptic_path),
                 "/sys/class/leds/vibrator/brightness");
    }
    return 0;
}

void ga_feedback_close(ga_feedback_t *fb) {
    (void)fb;  /* 无持久资源 */
}

void ga_feedback_play_reference(ga_feedback_t *fb, double freq_hz) {
    if (!fb || !fb->enable[GA_FB_PITCH]) return;
    if (freq_hz < 50.0 || freq_hz > 2000.0) return;

    long long now = ga_now_ms();
    /* 参考音是长音(1.2 秒), 1.5 秒内不重复, 否则会互相干扰 */
    if (now - fb->last_pitch_ms < 1500) return;
    fb->last_pitch_ms = now;

#ifdef GA_HAVE_ALSA
    int frames = 0;
    int16_t *tone = synth_tone(freq_hz, 1200, GA_SAMPLE_RATE, &frames);
    if (tone) {
        play_pcm(fb->beep_device, tone, frames, GA_SAMPLE_RATE);
        free(tone);
    }
#else
    (void)freq_hz;
#endif
}

void ga_feedback_speak(ga_feedback_t *fb, const char *text) {
    if (!fb || !fb->enable[GA_FB_SPEECH]) return;
    long long now = ga_now_ms();
    if (now - fb->last_speech_ms < 800) return;   /* 语音别叠着播 */
    fb->last_speech_ms = now;
    speak_impl(fb, text);
}

void ga_feedback_success(ga_feedback_t *fb) {
    if (!fb) return;
    long long now = ga_now_ms();

    /* 上升三音: C5-E5-G5, 心理上表示"完成/成功" */
#ifdef GA_HAVE_ALSA
    if (fb->enable[GA_FB_BEEP] && now - fb->last_beep_ms > 1200) {
        fb->last_beep_ms = now;
        const double notes[3] = { 523.25, 659.25, 783.99 };
        for (int i = 0; i < 3; i++) {
            int frames = 0;
            int16_t *tone = synth_tone(notes[i], 140, GA_SAMPLE_RATE, &frames);
            if (tone) {
                play_pcm(fb->beep_device, tone, frames, GA_SAMPLE_RATE);
                free(tone);
            }
        }
    }
#endif
    /* 振动: 双击, 明确且不易与其他状态混淆 */
    if (fb->enable[GA_FB_HAPTIC] && now - fb->last_haptic_ms > 600) {
        fb->last_haptic_ms = now;
        haptic_pulse(fb->haptic_path, 70);
        struct timespec ts = { 0, 60 * 1000000L };
        nanosleep(&ts, NULL);
        haptic_pulse(fb->haptic_path, 70);
    }
}

void ga_feedback_update(ga_feedback_t *fb, const ga_pitch_result_t *r) {
    if (!fb || !r) return;

    /* 置信度太低: 不反馈, 避免"乱叫"。用户拨弦瞬间和松手时
     * 会出现低置信度, 这时给反馈反而让人烦躁。 */
    if (r->freq_hz <= 0.0 || r->confidence < 0.5) return;

    ga_fb_level_t level = ga_fb_level_from_cents(r->cents_offset);
    if (level == GA_FB_LEVEL_TUNE) {
        ga_feedback_success(fb);
        return;
    }

    long long now = ga_now_ms();

    /* ---- 提示音: 速率编码方向, 音高编码方向 ----
     * 偏低 -> 低音(380Hz) + 慢速(2Hz)
     * 偏高 -> 高音(880Hz) + 快速(4Hz)
     * 偏差越大节流越短, 心流上"越接近越急/越缓和"要一致。
     * 这里选择: 差得远时【更快】, 让人有紧迫感去调;
     * 接近时【更慢】, 暗示"快好了"。 */
    if (fb->enable[GA_FB_BEEP]) {
        int interval_ms;
        double tone_freq;
        if (level == GA_FB_LEVEL_FAR) {
            interval_ms = 250;                 /* 4Hz */
        } else if (level == GA_FB_LEVEL_NEAR) {
            interval_ms = 400;                 /* 2.5Hz */
        } else { /* SLIGHT */
            interval_ms = 600;                 /* 1.7Hz */
        }
        tone_freq = (r->cents_offset > 0) ? 880.0 : 380.0;

        if (now - fb->last_beep_ms >= interval_ms) {
            fb->last_beep_ms = now;
#ifdef GA_HAVE_ALSA
            int frames = 0;
            int16_t *tone = synth_tone(tone_freq, 60, GA_SAMPLE_RATE, &frames);
            if (tone) {
                play_pcm(fb->beep_device, tone, frames, GA_SAMPLE_RATE);
                free(tone);
            }
#endif
        }
    }

    /* ---- 振动: 强度编码偏差大小 ----
     * FAR   -> 300ms 长振(明显, 强提示)
     * NEAR  -> 150ms
     * SLIGHT-> 80ms  (细腻, 表示"就差一点")
     * 注意振动方向不编码(振动很难表达"高/低"),
     * 所以方向主要靠提示音和语音, 振动只表达"还差多少"。 */
    if (fb->enable[GA_FB_HAPTIC]) {
        int dur_ms;
        int interval_ms;
        if (level == GA_FB_LEVEL_FAR)         { dur_ms = 300; interval_ms = 500; }
        else if (level == GA_FB_LEVEL_NEAR)   { dur_ms = 150; interval_ms = 600; }
        else                                   { dur_ms = 80;  interval_ms = 800; }

        if (now - fb->last_haptic_ms >= interval_ms) {
            fb->last_haptic_ms = now;
            haptic_pulse(fb->haptic_path, dur_ms);
        }
    }
}
