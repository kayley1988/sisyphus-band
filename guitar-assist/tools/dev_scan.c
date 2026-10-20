/*
 * dev_scan.c - ALSA 设备扫描
 *
 * 板子上插了 USB 声卡后, 到底该用 hw:几,0 ? 这个工具直接列出来,
 * 并且逐个尝试打开, 打印实际生效的采样率/延迟参数。
 * 省得在板子上反复猜设备号。
 */
#include "guitar_common.h"
#include "alsa_capture.h"

#include <stdio.h>
#include <alsa/asoundlib.h>

int main(void) {
    printf("=== ALSA 播放/采集设备列表 ===\n\n");

    printf("--- 采集设备(CAPTURE, 你要用这个) ---\n");
    snd_ctl_card_info_t *info = NULL;
    snd_ctl_card_info_alloca(&info);

    int card = -1;
    int found = 0;
    while (snd_card_next(&card) == 0 && card >= 0) {
        char ctl_name[32];
        snprintf(ctl_name, sizeof(ctl_name), "hw:%d", card);

        snd_ctl_t *ctl = NULL;
        if (snd_ctl_open(&ctl, ctl_name, 0) < 0) continue;
        if (snd_ctl_card_info(ctl, info) < 0) { snd_ctl_close(ctl); continue; }

        printf("  卡 %d: [%s] %s\n", card,
               snd_ctl_card_info_get_id(info),
               snd_ctl_card_info_get_name(info));

        int dev = -1;
        while (snd_ctl_pcm_next_device(ctl, &dev) == 0 && dev >= 0) {
            snd_pcm_info_t *pcminfo = NULL;
            snd_pcm_info_alloca(&pcminfo);
            snd_pcm_info_set_device(pcminfo, (unsigned)dev);
            snd_pcm_info_set_subdevice(pcminfo, 0);
            snd_pcm_info_set_stream(pcminfo, SND_PCM_STREAM_CAPTURE);
            if (snd_ctl_pcm_info(ctl, pcminfo) < 0) continue;

            char devname[32];
            snprintf(devname, sizeof(devname), "hw:%d,%d", card, dev);
            printf("      采集设备: %-10s  %s\n", devname,
                   snd_pcm_info_get_name(pcminfo));
            found++;
        }
        snd_ctl_close(ctl);
    }
    if (!found) printf("  (没找到采集设备, 检查麦克风/声卡连接)\n");

    printf("\n--- 尝试打开并实测参数 ---\n");
    /* 常见候选: 优先试 USB 声卡(hw:1,0), 再试板载(hw:0,0) */
    const char *cands[] = { "hw:1,0", "hw:0,0", "default" };
    for (size_t i = 0; i < sizeof(cands)/sizeof(cands[0]); i++) {
        printf("  %-10s : ", cands[i]);
        fflush(stdout);
        ga_capture_t *cap = ga_capture_open(cands[i], GA_SAMPLE_RATE, 256);
        if (cap) {
            printf("        OK  (设备名 %s)\n", ga_capture_device(cap));
            ga_capture_close(cap);
        } else {
            printf("        打不开\n");
        }
    }

    printf("\n提示:\n");
    printf("  USB 声卡一般会注册成新的卡号, 插拔前后对比卡号变化即可确定。\n");
    printf("  用 `arecord -l` 也能快速列出, 但本工具会实测能否打开。\n");
    return 0;
}
