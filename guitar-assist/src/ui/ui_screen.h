/*
 * ui_screen.h - 演奏仪表盘界面(竖屏 240x240)
 *
 * ============ 使用场景(决定了所有设计取舍) ============
 *
 * 用户是手部活动受限的演奏者: 左手能部分张合但压弦力度不够。
 * 整套是"半人工半机械":
 *
 *   左手 6 个触摸键 ──► 选定和弦 ──► 机械机构压弦
 *   右手 4 个触摸键 ──► 菜单/微调
 *   彩色屏(无触摸)  ──► 显示当前状态
 *
 * 【屏幕斜放在吉他上, 使用者离屏约 60~80cm, 且有倾斜角度】
 * 这一个事实决定了整个界面设计:
 *
 *   1. 字必须"极大"。240px 宽的屏上, 和弦名占满 ~90px 高。
 *      这不是"好看"的问题, 是"看不看得见"的问题。
 *   2. 一屏只传达一个信息。远距离 + 单人操作, 没有注意力看细节。
 *   3. 颜色块要大。色块比文字更快被识别(尤其是斜视/余光)。
 *   4. 不显示小数、百分比、置信度这些"工程师觉得有用"的东西。
 *
 * ============ 为什么不做语音 ============
 *
 * 用户能看屏(视力正常), 只是手不方便。加语音反而:
 *   - 演奏时吵闹, 干扰听自己的琴声(这对音乐是致命的)
 *   - 增加延迟和复杂度
 * 所以反馈全部走屏幕。振动保留, 作为"机械机构动作确认"的触觉提示。
 */
#ifndef GA_UI_SCREEN_H
#define GA_UI_SCREEN_H

#include <stdint.h>
#include <stddef.h>
#include "ui_draw.h"

/* 每次按键最多能直达的和弦数(左手 6 个触摸键) */
#define UI_QUICK_CHORDS  6

/* 界面模式 */
typedef enum {
    UI_MODE_PLAY = 0,      /* 演奏: 显示当前和弦 + 指法图 */
    UI_MODE_TUNER,         /* 调音: 显示音名 + 音分 */
    UI_MODE_COUNT
} ui_mode_t;

/* 调音状态 */
typedef enum {
    UI_TUNE_NONE = 0,
    UI_TUNE_LOW,
    UI_TUNE_OK,
    UI_TUNE_HIGH,
} ui_tune_state_t;

/* 一个"快捷和弦"槽位 */
typedef struct {
    char name[12];         /* "C" / "Am" ... */
    char name_cn[16];      /* "C 大" / "A 小" ... */
    int  fingering[6];     /* 各弦品位, -1 不弹, 0 空弦 */
    int  fret_min, fret_max;
    int  is_minor;         /* 用于配色: 小三和弦用不同色 */
} ui_quick_chord_t;

/* 界面完整状态 */
typedef struct {
    ui_mode_t mode;

    /* —— 演奏模式 —— */
    ui_quick_chord_t quick[UI_QUICK_CHORDS];
    int  quick_count;        /* 实际有几个(默认 6) */
    int  active;             /* 当前选中的槽位 0..quick_count-1, -1 无 */
    int  mech_ready;         /* 机械机构是否已压好弦 */
    int  mech_moving;        /* 机构正在动作中 */

    /* —— 调音模式 —— */
    char note_name[8];
    double freq_hz;
    double cents;
    int  string_idx;         /* 0..5, -1 未知 */
    ui_tune_state_t tune_state;

    /* 底部状态文字(简短, 远距离只求"有/无") */
    const char *status;
} ui_state_t;

/* 界面运行时上下文 */
typedef struct {
    ui_canvas_t canvas;
    ui_state_t  state;
    int         dirty;
} ui_screen_t;

/* ---------- 生命周期 ---------- */

void ui_screen_init(ui_screen_t *s);

/* 装载 6 个快捷和弦(通常从和弦库取)。chords 数组长度 n, n<=UI_QUICK_CHORDS */
void ui_screen_set_quick_chords(ui_screen_t *s,
                                const ui_quick_chord_t *chords, int n);

/* ---------- 状态更新 ---------- */

void ui_set_mode(ui_screen_t *s, ui_mode_t m);

/* 选定第 idx 个快捷和弦(0-based)。会标记 dirty。
 * idx 越界则忽略。这对应"左手按了第 N 个触摸键"。 */
void ui_select_quick(ui_screen_t *s, int idx);

/* 在当前槽位基础上偏移(给 Up/Down 用)。
 * 这也是"演奏模式下 Up/Down 的功能": 换和弦。 */
void ui_quick_step(ui_screen_t *s, int delta);

/* 机械机构状态(压弦中/已就绪) */
void ui_set_mech_state(ui_screen_t *s, int moving, int ready);

/* 调音状态。note 为空或 freq<=0 表示静音。 */
void ui_update_tuner(ui_screen_t *s, const char *note, double freq,
                     double cents, int string_idx, ui_tune_state_t st);

/* ---------- 渲染 ---------- */

/* 渲染一帧到画布(不刷屏)。返回 1 表示有内容变化。 */
int  ui_screen_render(ui_screen_t *s);

/* 各页面独立绘制(便于单测和预览) */
void ui_draw_play(ui_canvas_t *c, const ui_state_t *st);
void ui_draw_tuner(ui_canvas_t *c, const ui_state_t *st);

/* 画和弦指法图。横排: 6 弦从左到右 = 6弦..1弦, 和使用者左手顺序一致。 */
void ui_draw_fingering(ui_canvas_t *c, int x, int y, int w, int h,
                       const int *fingering, int fret_min, int fret_max);

/* 画 6 个快捷槽位指示条 */
void ui_draw_quick_bar(ui_canvas_t *c, const ui_state_t *st, int y, int h);

#endif /* GA_UI_SCREEN_H */
