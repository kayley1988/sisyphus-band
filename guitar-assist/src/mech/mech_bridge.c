/*
 * mech_bridge.c - 机构回调桥接实现
 */
#include "mech_bridge.h"

#include <stdio.h>

static void bridge_chord(void *ctx, const char *name, const int *fing)
{
    ga_mech_bridge_t *b = (ga_mech_bridge_t *)ctx;
    if (!b || !b->mech || !fing) return;

    int r = ga_mech_set_chord(b->mech, fing);
    b->last_ok = (r >= 0);

    /* ★ 这里【故意不】碰 UI 的机构状态 —— 本桥只负责"把命令发出去"。
     *
     * 曾经的写法是这里调 ui_set_mech_state(0, 1)("帧一发出就算就绪"),
     * 但 ui_app 的 fire_chord_changed() 在回调【之后】紧接着调
     * ui_set_mech_state(1, 0)("压弦中") —— 后者覆盖前者, 结果是
     * 界面永远停在"压弦中", 从不显示"就绪"。
     *
     * 正确的职责划分:
     *   ui_app     : 选完和弦 -> 置"压弦中"(意图表达)
     *   主循环     : 机构动作到位 -> 置"就绪"(完成表达)
     *   本桥       : 只转发命令, 不反向改 UI 状态
     * 待 ESP32 上报到位事件后, "就绪"改由事件驱动(ui_main.c 里替换那段
     * 乐观延时即可), UI 侧不用动。 */
    printf("[mech] 压弦 %s -> 帧 %zuB (tx=%lu)\n",
           name ? name : "?", b->mech->last_frame_len, b->mech->tx_frames);
}

static void bridge_confirm(void *ctx)
{
    ga_mech_bridge_t *b = (ga_mech_bridge_t *)ctx;
    if (!b || !b->mech || !b->app) return;

    /* 重发当前和弦(取 UI 当前选中槽位的指法)。同样不改 UI 状态, 理由同上。 */
    ui_state_t *st = &b->app->screen->state;
    if (st->active >= 0 && st->active < st->quick_count) {
        const int *fing = st->quick[st->active].fingering;
        int r = ga_mech_set_chord(b->mech, fing);
        b->last_ok = (r >= 0);
    }
}

static void bridge_brightness(void *ctx, int percent)
{
    (void)ctx; (void)percent;
    /* 背光由显示驱动负责(见 display_drv / st7789_backlight)。
     * 这里留空, 需要时把 percent 转给显示层。 */
}

void ga_mech_bridge_init(ga_mech_bridge_t *b, ga_mech_t *mech, ui_app_t *app)
{
    if (!b) return;
    b->mech = mech;
    b->app  = app;
    b->last_ok = 0;
    if (app) {
        app->ctx = b;
        app->on_chord_changed = bridge_chord;
        app->on_confirm = bridge_confirm;
        app->on_brightness = bridge_brightness;
    }
}
