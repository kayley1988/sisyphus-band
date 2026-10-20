/*
 * ui_demo.c - 交互链验证(PC 端)
 *
 * 模拟一次真实使用过程, 每一步渲染成图:
 *   1. 开机 → 演奏页
 *   2. 左手按键 3(Am) → 机构压弦 → 就绪
 *   3. Down 键 → 切下一个和弦
 *   4. Set 键 → 切到调音页
 *   5. Set 键 → 切回演奏
 *   6. 长按 Set → 进入设置
 *   7. ENT 调亮度
 *   8. 长按 Set → 退出设置
 *
 * 用法: ./ui_demo out_dir
 */
#include "ui_app.h"
#include "ui_draw.h"
#include "mech_driver.h"
#include "mech_bridge.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

/* 6 个常用和弦(与 chord_db 一致) */
static const ui_quick_chord_t QUICK[UI_QUICK_CHORDS] = {
    { "C",  "C 大", { -1, 3, 2, 0, 1, 0 },  0, 3, 0 },
    { "G",  "G 大", {  3, 2, 0, 0, 0, 3 },  0, 3, 0 },
    { "Am", "A 小", { -1, 0, 2, 2, 1, 0 },  0, 2, 1 },
    { "F",  "F 大", {  1, 3, 3, 2, 1, 1 },  1, 3, 0 },
    { "D",  "D 大", { -1, -1, 0, 2, 3, 2 }, 0, 3, 0 },
    { "Em", "E 小", {  0, 2, 2, 0, 0, 0 },  0, 2, 1 },
};

static void write_ppm(const char *path, const ui_canvas_t *c) {
    FILE *fp = fopen(path, "wb");
    if (!fp) return;
    fprintf(fp, "P6\n%d %d\n255\n", UI_W, UI_H);
    for (int y = 0; y < UI_H; y++) {
        for (int x = 0; x < UI_W; x++) {
            uint16_t v = c->px[y * UI_W + x];
            unsigned char rgb[3] = {
                (unsigned char)(((v >> 11) & 0x1F) * 255 / 31),
                (unsigned char)(((v >> 5)  & 0x3F) * 255 / 63),
                (unsigned char)(( v        & 0x1F) * 255 / 31),
            };
            fwrite(rgb, 1, 3, fp);
        }
    }
    fclose(fp);
    printf("  %s\n", path);
}

/* 模拟机构动作完成(真实板子上这是机构反馈的信号) */
static void mech_done(ui_app_t *app) {
    ui_set_mech_state(app->screen, 0, 1);
}

static ga_key_event_t ev(ga_key_t k, ga_key_event_type_t t) {
    ga_key_event_t e = { t, k };
    return e;
}

int main(int argc, char **argv) {
    const char *dir = (argc > 1) ? argv[1] : ".";
    mkdir(dir, 0777);
    char path[512];

    ui_screen_t sc;
    ui_screen_init(&sc);
    ui_screen_set_quick_chords(&sc, QUICK, UI_QUICK_CHORDS);

    ui_app_t app;
    ui_app_init(&app, &sc);

    /* 挂真实机构驱动层(PC 上 fd=-1 -> 离线构造帧, 便于验证链路) */
    ga_mech_t mech;
    ga_mech_open(&mech, NULL, 0);
    ga_mech_bridge_t bridge;
    ga_mech_bridge_init(&bridge, &mech, &app);   /* 设置 on_chord_changed/on_confirm */

    int step = 0;

#define SNAP(label) do { \
    ui_app_render(&app); \
    snprintf(path, sizeof(path), "%s/%02d_%s.ppm", dir, step++, label); \
    write_ppm(path, &sc.canvas); \
} while (0)

    printf("== ui_demo: 交互链验证 ==\n");

    /* 1. 开机 */
    printf("[1] 开机(演奏模式)\n");
    ui_app_render(&app);
    snprintf(path, sizeof(path), "%s/%02d_boot.ppm", dir, step++);
    write_ppm(path, &sc.canvas);

    /* 2. 左手按第 3 键(Am) */
    printf("[2] 左手键3 -> Am\n");
    ui_app_left_key(&app, 2);
    SNAP("am_moving");
    printf("[2b] 机构到位\n");
    mech_done(&app);
    SNAP("am_ready");

    /* 3. Down 键 → 下一个(F) */
    printf("[3] Down -> F\n");
    ui_app_handle(&app, ev(GA_KEY_DOWN, GA_KEY_EV_SHORT));
    mech_done(&app);
    SNAP("f_ready");

    /* 4. Up 键 → 上一个(Am) */
    printf("[4] Up -> Am\n");
    ui_app_handle(&app, ev(GA_KEY_UP, GA_KEY_EV_SHORT));
    mech_done(&app);
    SNAP("am_again");

    /* 5. Set → 调音页 */
    printf("[5] Set -> 调音页\n");
    ui_app_handle(&app, ev(GA_KEY_SET, GA_KEY_EV_SHORT));
    ui_update_tuner(&sc, "E2", 82.4, 0.0, 5, UI_TUNE_OK);
    SNAP("tuner");

    /* 6. Set → 回演奏 */
    printf("[6] Set -> 演奏页\n");
    ui_app_handle(&app, ev(GA_KEY_SET, GA_KEY_EV_SHORT));
    SNAP("back_play");

    /* 7. 左手键6(Em) */
    printf("[7] 左手键6 -> Em\n");
    ui_app_left_key(&app, 5);
    mech_done(&app);
    SNAP("em_ready");

    /* 8. 长按 Set → 设置 */
    printf("[8] 长按Set -> 设置\n");
    ui_app_handle(&app, ev(GA_KEY_SET, GA_KEY_EV_LONG));
    SNAP("settings");

    /* 9. ENT 加亮度 */
    printf("[9] ENT -> 亮度+10\n");
    ui_app_handle(&app, ev(GA_KEY_ENT, GA_KEY_EV_SHORT));
    SNAP("settings_bright");

    /* 10. Down 移到"退出", 长按 Set 退出 */
    printf("[10] Down + 长按Set -> 退出\n");
    ui_app_handle(&app, ev(GA_KEY_DOWN, GA_KEY_EV_SHORT));
    ui_app_handle(&app, ev(GA_KEY_SET, GA_KEY_EV_LONG));
    SNAP("exit_settings");

    printf("== 完成 %d 帧 ==\n", step);

    /* 证明"按键 -> UI -> 机构驱动层 -> 帧"整条链走通 */
    printf("   机构: 共发出 %lu 帧, 末帧 %zuB: ",
           mech.tx_frames, mech.last_frame_len);
    for (size_t i = 0; i < mech.last_frame_len; i++) printf("%02X ", mech.last_frame[i]);
    printf("\n");
    ga_mech_close(&mech);
    return 0;
}
