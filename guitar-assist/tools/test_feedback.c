/*
 * test_feedback.c - 反馈逻辑单元测试
 *
 * 反馈层没有硬件也能测"决策逻辑": 给定音分偏差, 应该触发什么等级的
 * 反馈? 节流是否正确? 这部分逻辑错了会导致用户体验灾难
 * (比如提示音疯狂刷屏), 所以必须测。
 */
#include "feedback.h"

#include <stdio.h>
#include <string.h>

static int pass = 0, total = 0;

static void check(const char *what, int cond) {
    total++;
    printf("    %-52s %s\n", what, cond ? "PASS" : "FAIL");
    if (cond) pass++;
}

int main(void) {
    printf("=== 反馈逻辑测试 ===\n\n");

    printf("[1] 音分偏差 -> 等级映射\n");
    struct { double cents; ga_fb_level_t expect; } cases[] = {
        {   0.0, GA_FB_LEVEL_TUNE   },
        {   5.0, GA_FB_LEVEL_TUNE   },
        {  -5.0, GA_FB_LEVEL_TUNE   },
        {   6.5, GA_FB_LEVEL_SLIGHT },
        {  -7.0, GA_FB_LEVEL_SLIGHT },
        {  20.0, GA_FB_LEVEL_NEAR   },
        { -20.0, GA_FB_LEVEL_NEAR   },
        {  40.0, GA_FB_LEVEL_FAR    },
        { -99.0, GA_FB_LEVEL_FAR    },
    };
    for (size_t i = 0; i < sizeof(cases)/sizeof(cases[0]); i++) {
        ga_fb_level_t got = ga_fb_level_from_cents(cases[i].cents);
        char buf[128];
        snprintf(buf, sizeof(buf), "%.1f 音分 -> 等级 %d (期望 %d)",
                 cases[i].cents, (int)got, (int)cases[i].expect);
        check(buf, got == cases[i].expect);
    }

    printf("\n[2] 时间戳单调性\n");
    long long t1 = ga_now_ms();
    long long t2 = ga_now_ms();
    check("ga_now_ms 单调不减", t2 >= t1);
    check("ga_now_ms 量级合理(> 0)", t1 > 0);

    printf("\n[3] 初始化与配置\n");
    ga_feedback_t fb;
    int rc = ga_feedback_init(&fb, "hw:0,0", NULL);
    check("初始化返回 0", rc == 0);
    int all_on = 1;
    for (int i = 0; i < GA_FB_CHANNEL_COUNT; i++) {
        if (!fb.enable[i]) all_on = 0;
    }
    check("默认所有反馈通道开启", all_on);
    check("提示音设备名正确写入", strcmp(fb.beep_device, "hw:0,0") == 0);
    check("振动路径有默认值", fb.haptic_path[0] != '\0');

    printf("\n[4] 节流逻辑(关键: 防止提示音刷屏)\n");
    /* 连续快速调用 update, 第二次应该被节流拦住 */
    ga_pitch_result_t r;
    memset(&r, 0, sizeof(r));
    r.freq_hz = 110.0;
    r.confidence = 0.95;
    r.cents_offset = 40.0;   /* FAR 等级 */
    r.nearest_string = 1;

    ga_feedback_t fb2;
    ga_feedback_init(&fb2, "default", NULL);
    /* 关掉音频输出避免测试时出声 */
    fb2.enable[GA_FB_BEEP] = 0;
    fb2.enable[GA_FB_HAPTIC] = 0;
    fb2.enable[GA_FB_SPEECH] = 0;

    ga_feedback_update(&fb2, &r);   /* 第一次 */
    long long first_ts = fb2.last_beep_ms;
    ga_feedback_update(&fb2, &r);   /* 立刻第二次, 应被节流 */
    check("连续调用不更新节流时间戳", fb2.last_beep_ms == first_ts);

    printf("\n[5] 低置信度应被忽略\n");
    ga_feedback_t fb3;
    ga_feedback_init(&fb3, "default", NULL);
    ga_pitch_result_t weak;
    memset(&weak, 0, sizeof(weak));
    weak.freq_hz = 110.0;
    weak.confidence = 0.2;      /* 太低 */
    weak.cents_offset = 50.0;
    ga_feedback_update(&fb3, &weak);
    check("低置信度不触发反馈", fb3.last_beep_ms == 0);

    printf("\n[6] 无效输入不应崩溃\n");
    ga_feedback_init(&fb3, "default", NULL);
    ga_pitch_result_t invalid;
    memset(&invalid, 0, sizeof(invalid));
    invalid.freq_hz = 0.0;      /* 无信号 */
    ga_feedback_update(&fb3, &invalid);
    check("freq=0 安全处理", fb3.last_beep_ms == 0);
    ga_feedback_update(NULL, NULL);
    check("NULL 参数安全处理(无崩溃)", 1);

    printf("\n[7] 参考音频率范围检查\n");
    ga_feedback_t fb4;
    ga_feedback_init(&fb4, "nonexistent_device_xyz", "/nonexistent/path");
    /* 这些调用在没有硬件时应该安静失败, 不崩溃 */
    ga_feedback_play_reference(&fb4, 110.0);
    ga_feedback_play_reference(&fb4, 10.0);      /* 超范围, 应被拒绝 */
    ga_feedback_play_reference(&fb4, 99999.0);   /* 超范围 */
    ga_feedback_success(&fb4);
    ga_feedback_speak(&fb4, "测试播报");
    check("无硬件时安全降级(无崩溃/无卡死)", 1);

    printf("\n========================================\n");
    printf("结果: %d / %d\n", pass, total);
    printf("========================================\n");
    return (pass == total) ? 0 : 1;
}
