/*
 * ui_preview.c - PC 端界面预览工具
 *
 * 用途: 不用接屏幕就能看到界面长什么样。
 *
 * 【重要】本工具的"远距离可读性检查":
 *   把渲染结果缩到实际观看尺寸(240px 宽的屏, 在 70cm 外大约相当于
 *   屏幕上 60~70px 宽), 然后看还能不能读出和弦名。
 *   本工具会额外输出 *_small.ppm (缩到 30%), 模拟远距离观看效果。
 *
 * 用法:
 *   ./ui_preview out_dir
 */
#include "ui_screen.h"
#include "ui_draw.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

/* RGB565 → PPM(P6, RGB888) */
static int write_ppm(const char *path, const ui_canvas_t *c, int scale) {
    FILE *fp = fopen(path, "wb");
    if (!fp) { perror(path); return -1; }
    int w = UI_W * scale, h = UI_H * scale;
    fprintf(fp, "P6\n%d %d\n255\n", w, h);

    unsigned char *row = malloc((size_t)w * 3);
    if (!row) { fclose(fp); return -1; }

    for (int y = 0; y < h; y++) {
        int sy = y / scale;
        for (int x = 0; x < w; x++) {
            int sx = x / scale;
            uint16_t v = c->px[sy * UI_W + sx];
            row[x * 3 + 0] = (unsigned char)(((v >> 11) & 0x1F) * 255 / 31);
            row[x * 3 + 1] = (unsigned char)(((v >> 5)  & 0x3F) * 255 / 63);
            row[x * 3 + 2] = (unsigned char)(( v        & 0x1F) * 255 / 31);
        }
        fwrite(row, 1, (size_t)w * 3, fp);
    }
    free(row);
    fclose(fp);
    printf("  写出 %s (%dx%d)\n", path, w, h);
    return 0;
}

/* 缩小版本: 用最近邻缩放, 模拟"远距离看到的尺寸" */
static int write_ppm_shrunk(const char *path, const ui_canvas_t *c, int factor) {
    if (factor < 1) factor = 1;
    FILE *fp = fopen(path, "wb");
    if (!fp) { perror(path); return -1; }
    int w = UI_W / factor, h = UI_H / factor;
    fprintf(fp, "P6\n%d %d\n255\n", w, h);

    unsigned char *row = malloc((size_t)w * 3);
    if (!row) { fclose(fp); return -1; }

    for (int y = 0; y < h; y++) {
        int sy = y * factor;
        for (int x = 0; x < w; x++) {
            int sx = x * factor;
            uint16_t v = c->px[sy * UI_W + sx];
            row[x * 3 + 0] = (unsigned char)(((v >> 11) & 0x1F) * 255 / 31);
            row[x * 3 + 1] = (unsigned char)(((v >> 5)  & 0x3F) * 255 / 63);
            row[x * 3 + 2] = (unsigned char)(( v        & 0x1F) * 255 / 31);
        }
        fwrite(row, 1, (size_t)w * 3, fp);
    }
    free(row);
    fclose(fp);
    printf("  写出 %s (%dx%d, 模拟远距离)\n", path, w, h);
    return 0;
}

static void out_path(char *buf, size_t cap, const char *dir, const char *name) {
    snprintf(buf, cap, "%s/%s.ppm", dir, name);
}

static void dump(ui_screen_t *s, const char *dir, const char *name,
                 int scale, int shrink) {
    ui_screen_render(s);
    char path[512];
    out_path(path, sizeof(path), dir, name);
    write_ppm(path, &s->canvas, scale);
    if (shrink > 1) {
        char sp[600];
        snprintf(sp, sizeof(sp), "%s/%s_small.ppm", dir, name);
        write_ppm_shrunk(sp, &s->canvas, shrink);
    }
}

/* 6 个常用和弦的默认槽位(和 chord_db 里的指法一致) */
static const ui_quick_chord_t QUICK[UI_QUICK_CHORDS] = {
    { "C",  "C 大", { -1, 3, 2, 0, 1, 0 }, 0, 3, 0 },
    { "G",  "G 大", {  3, 2, 0, 0, 0, 3 }, 0, 3, 0 },
    { "Am", "A 小", { -1, 0, 2, 2, 1, 0 }, 0, 2, 1 },
    { "F",  "F 大", {  1, 3, 3, 2, 1, 1 }, 1, 3, 0 },
    { "D",  "D 大", { -1, -1, 0, 2, 3, 2 }, 0, 3, 0 },
    { "Em", "E 小", {  0, 2, 2, 0, 0, 0 }, 0, 2, 1 },
};

int main(int argc, char **argv) {
    const char *dir = (argc > 1) ? argv[1] : ".";
    int scale = 2;
    int shrink = 3;   /* 240/3 = 80px 宽, 模拟远距离 */

    mkdir(dir, 0777);

    ui_screen_t s;
    ui_screen_init(&s);
    ui_screen_set_quick_chords(&s, QUICK, UI_QUICK_CHORDS);

    printf("== 界面预览(竖屏 240x240, 远距离优化) ==\n");

    /* 演奏页: 逐个和弦看配色和放大效果 */
    ui_set_mode(&s, UI_MODE_PLAY);
    for (int i = 0; i < UI_QUICK_CHORDS; i++) {
        ui_select_quick(&s, i);
        ui_set_mech_state(&s, 0, 1);
        char nm[32];
        snprintf(nm, sizeof(nm), "play_%d_%s", i + 1, QUICK[i].name);
        dump(&s, dir, nm, scale, shrink);
    }

    /* 机构动作中 */
    ui_select_quick(&s, 2);            /* Am */
    ui_set_mech_state(&s, 1, 0);
    dump(&s, dir, "play_moving", scale, shrink);

    /* 未选择和弦 */
    ui_set_mode(&s, UI_MODE_PLAY);
    s.state.active = -1;
    s.dirty = 1;
    ui_screen_render(&s);
    {
        char path[512];
        out_path(path, sizeof(path), dir, "play_idle");
        write_ppm(path, &s.canvas, scale);
    }

    /* 调音页 */
    ui_set_mode(&s, UI_MODE_TUNER);
    ui_update_tuner(&s, "E2", 82.4, 0.0, 5, UI_TUNE_OK);
    dump(&s, dir, "tuner_ok", scale, shrink);

    ui_update_tuner(&s, "E2", 79.0, -24.0, 5, UI_TUNE_LOW);
    dump(&s, dir, "tuner_low", scale, shrink);

    ui_update_tuner(&s, "A2", 112.5, 18.0, 4, UI_TUNE_HIGH);
    dump(&s, dir, "tuner_high", scale, shrink);

    printf("== 完成 ==\n");
    printf("看效果: 打开 *_small.ppm —— 那是模拟远距离(80px 宽)的样子。\n");
    return 0;
}
