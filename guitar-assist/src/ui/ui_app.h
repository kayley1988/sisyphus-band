/*
 * ui_app.h - 应用控制器: 把按键事件变成界面动作
 *
 * 按键布局(物理):
 *   左手 6 个触摸键 → 直接选 6 个和弦(一键直达, 无需确认)
 *   右手 4 个触摸键 → Up / Down / Set / ENT
 *
 * 双模式设计(用户明确要求"演奏/设置区分"):
 *
 *   【演奏模式】(默认, 开机即入)
 *     Up      = 上一个和弦
 *     Down    = 下一个和弦
 *     Set     = 切到调音页(再按切回演奏)
 *     ENT     = 触发机械压弦(确认当前和弦)
 *     6 键    = 直接选和弦(选完自动触发压弦 —— 演奏时手在琴上,
 *               不应该再需要一次"确认"动作, 这是半机械的意义)
 *
 *   【设置模式】(长按 Set 1 秒进入, 再长按退出)
 *     Up/Down = 在菜单项之间移动
 *     ENT     = 进入/修改当前项
 *     Set     = 返回上一级
 *     菜单内容后续按需求加(和弦组自定义、机构灵敏度、屏幕亮度等),
 *     框架先留好, 本版实现"亮度"和"退出"两项演示。
 *
 * 【机械机构接口】
 *   通过回调注入, 控制器不直接碰硬件:
 *     on_chord_changed(name, fingering)  → 机构去压对应和弦
 *     ui_app_confirm()                   → 手动触发(ENT)
 *   PC 上这些回调打印日志, 板子上接 GPIO/串口/步进电机驱动。
 */
#ifndef GA_UI_APP_H
#define GA_UI_APP_H

#include "ui_screen.h"
#include "input.h"

/* 6 个左手快捷键对应的槽位索引。物理键 1..6 → 槽位 0..5 */
#define UI_APP_LEFT_KEYS 6

typedef enum {
    UI_APP_MODE_PLAY = 0,     /* 演奏 */
    UI_APP_MODE_SETTINGS,     /* 设置 */
} ui_app_mode_t;

/* 设置菜单项 */
typedef enum {
    UI_APP_SET_BRIGHTNESS = 0,
    UI_APP_SET_EXIT,
    UI_APP_SET_COUNT
} ui_app_setting_t;

typedef struct {
    ui_screen_t *screen;

    ui_app_mode_t app_mode;          /* 演奏/设置 */
    ui_app_setting_t set_cursor;     /* 设置菜单光标 */
    int brightness;                  /* 10~100 */

    /* —— 机械机构回调(必填, 板子上接真硬件, PC 上打日志) —— */
    void (*on_chord_changed)(void *ctx, const char *name,
                             const int *fingering);
    void (*on_confirm)(void *ctx);   /* ENT: 重新压弦 */
    void (*on_brightness)(void *ctx, int percent);
    void *ctx;

    /* 长按 Set 的状态跟踪 */
    int  set_long_pending;
} ui_app_t;

void ui_app_init(ui_app_t *app, ui_screen_t *sc);

/* 处理一个按键事件。返回 1 = 界面有变化需要重绘。 */
int  ui_app_handle(ui_app_t *app, ga_key_event_t ev);

/* 左手 6 键直达(独立于 4 键事件流, 板子上单独 poll 这 6 个 GPIO)。
 * key_idx: 0..5。返回 1 = 有变化。 */
int  ui_app_left_key(ui_app_t *app, int key_idx);

/* 当前有效亮度(0~100) */
int  ui_app_brightness(const ui_app_t *app);

/* 渲染当前帧(含设置页)。渲染循环每帧调用这个, 不要直接调 ui_screen_render */
void ui_app_render(ui_app_t *app);

#endif /* GA_UI_APP_H */
