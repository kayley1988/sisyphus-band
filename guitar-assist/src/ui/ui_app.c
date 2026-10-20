/*
 * ui_app.c - 应用控制器实现
 */
#include "ui_app.h"

#include <stdio.h>
#include <string.h>

void ui_app_init(ui_app_t *app, ui_screen_t *sc) {
    if (!app || !sc) return;
    memset(app, 0, sizeof(*app));
    app->screen = sc;
    app->app_mode = UI_APP_MODE_PLAY;
    app->set_cursor = UI_APP_SET_BRIGHTNESS;
    app->brightness = 80;
}

/* 把当前选中和弦发给机械机构 */
static void fire_chord_changed(ui_app_t *app) {
    ui_state_t *st = &app->screen->state;
    if (!app->on_chord_changed) return;
    if (st->active < 0 || st->active >= st->quick_count) return;
    const ui_quick_chord_t *q = &st->quick[st->active];
    app->on_chord_changed(app->ctx, q->name, q->fingering);
    /* 机构开始动作 → 界面进入"压弦中" */
    ui_set_mech_state(app->screen, 1, 0);
}

/* ---------------- 设置菜单 ---------------- */

static const char *SET_LABELS[UI_APP_SET_COUNT] = {
    "亮度", "退出设置",
};

static void draw_settings_stub(ui_app_t *app) {
    /* 设置页直接画在画布上(简单列表, 两项)。
     * 放在 .c 里而不进 ui_screen, 因为设置页不属于演奏信息。 */
    ui_canvas_t *c = &app->screen->canvas;
    ui_canvas_invalidate(c);
    ui_canvas_clear(c, C_BLACK);

    ui_text_cn_center(c, UI_W / 2, 16, "设置", UI_FONT_CN16, C_WHITE);
    ui_line_h(c, 0, UI_W - 1, 38, C_MIDGRAY);

    for (int i = 0; i < UI_APP_SET_COUNT; i++) {
        int y = 60 + i * 56;
        int cur = (i == (int)app->set_cursor);
        if (cur) ui_fill_rect(c, 8, y - 8, UI_W - 16, 40, C_DARKGRAY);

        ui_text_cn(c, 20, y, SET_LABELS[i], UI_FONT_CN16,
                   cur ? C_YELLOW : C_MIDGRAY);

        /* 亮度项附加当前值 + 值条 */
        if (i == UI_APP_SET_BRIGHTNESS) {
            char buf[12];
            snprintf(buf, sizeof(buf), "%d%%", app->brightness);
            ui_text(c, 140, y, buf, UI_FONT_MED, C_WHITE);
            int bw = 180;
            ui_fill_rect(c, 20, y + 22, bw, 8, C_DARKGRAY);
            ui_fill_rect(c, 20, y + 22, bw * app->brightness / 100, 8, C_CYAN);
        }
    }
    app->screen->dirty = 0;
}

static void apply_setting(ui_app_t *app, int delta) {
    switch (app->set_cursor) {
        case UI_APP_SET_BRIGHTNESS: {
            app->brightness += delta * 10;
            if (app->brightness > 100) app->brightness = 100;
            if (app->brightness < 10)  app->brightness = 10;
            if (app->on_brightness)
                app->on_brightness(app->ctx, app->brightness);
            break;
        }
        default: break;
    }
}

/* ---------------- 主入口 ---------------- */

int ui_app_handle(ui_app_t *app, ga_key_event_t ev) {
    if (!app || ev.type == GA_KEY_EV_NONE) return 0;
    ui_screen_t *sc = app->screen;
    int changed = 0;

    /* —— 全局: 长按 Set 切换 演奏/设置 模式 —— */
    if (ev.key == GA_KEY_SET && ev.type == GA_KEY_EV_LONG) {
        if (app->app_mode == UI_APP_MODE_PLAY) {
            app->app_mode = UI_APP_MODE_SETTINGS;
            app->set_cursor = UI_APP_SET_BRIGHTNESS;
        } else {
            app->app_mode = UI_APP_MODE_PLAY;
        }
        sc->dirty = 1;
        return 1;
    }

    if (app->app_mode == UI_APP_MODE_SETTINGS) {
        switch (ev.type) {
            case GA_KEY_EV_SHORT:
            case GA_KEY_EV_REPEAT:
                switch (ev.key) {
                    case GA_KEY_UP:
                        if (app->set_cursor > 0) app->set_cursor--;
                        break;
                    case GA_KEY_DOWN:
                        if (app->set_cursor < UI_APP_SET_COUNT - 1)
                            app->set_cursor++;
                        break;
                    case GA_KEY_ENT:
                        apply_setting(app, +1);   /* ENT = 加 */
                        break;
                    case GA_KEY_SET:
                        /* 短按 Set 在设置里 = 减(和 ENT 对称) */
                        apply_setting(app, -1);
                        break;
                    default: break;
                }
                break;
            default: break;
        }
        /* 设置模式下重绘走 stub(下一帧由渲染循环调用) */
        sc->dirty = 1;
        return 1;
    }

    /* —— 演奏模式 —— */
    switch (ev.type) {
        case GA_KEY_EV_SHORT:
        case GA_KEY_EV_REPEAT:
            switch (ev.key) {
                case GA_KEY_UP:
                    ui_quick_step(sc, -1);
                    fire_chord_changed(app);
                    changed = 1;
                    break;
                case GA_KEY_DOWN:
                    ui_quick_step(sc, +1);
                    fire_chord_changed(app);
                    changed = 1;
                    break;
                case GA_KEY_SET:
                    /* 短按 Set: 演奏/调音 切换 */
                    ui_set_mode(sc, sc->state.mode == UI_MODE_PLAY
                                        ? UI_MODE_TUNER : UI_MODE_PLAY);
                    changed = 1;
                    break;
                case GA_KEY_ENT:
                    /* ENT: 手动重触发压弦(比如机构没到位时重试) */
                    if (app->on_confirm) app->on_confirm(app->ctx);
                    ui_set_mech_state(sc, 1, 0);
                    changed = 1;
                    break;
                default: break;
            }
            break;
        default: break;
    }
    return changed;
}

int ui_app_left_key(ui_app_t *app, int key_idx) {
    if (!app || key_idx < 0 || key_idx >= UI_APP_LEFT_KEYS) return 0;
    ui_screen_t *sc = app->screen;

    /* 左手键只在演奏模式有效; 调音时左手在琴弦上, 不该误触发 */
    if (sc->state.mode != UI_MODE_PLAY) return 0;
    /* 槽位数可能少于 6(比如只配了 4 个常用和弦) */
    if (key_idx >= sc->state.quick_count) return 0;

    ui_select_quick(sc, key_idx);
    fire_chord_changed(app);   /* 一键直达 = 选完就压, 不需要确认 */
    return 1;
}

int ui_app_brightness(const ui_app_t *app) {
    return app ? app->brightness : 80;
}

/* 渲染分发: 应用层包一层, 处理设置页 */
void ui_app_render(ui_app_t *app) {
    if (!app) return;
    if (app->app_mode == UI_APP_MODE_SETTINGS) {
        draw_settings_stub(app);
    } else {
        ui_screen_render(app->screen);
    }
}
