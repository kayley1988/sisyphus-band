/*
 * ui_screen.c - 演奏仪表盘实现(竖屏 240x240)
 *
 * 布局(竖屏, 从上到下):
 *
 *   y=0   ┌──────────────────────┐
 *         │ 1  2  3  4  5  6     │  快捷槽位条, 32px
 *   y=32  ├──────────────────────┤
 *         │                      │
 *         │        C             │  和弦名, 88px 超大字
 *         │                      │
 *   y=120 ├──────────────────────┤
 *         │      C 大 调          │  中文名, 24px
 *   y=144 ├──────────────────────┤
 *         │  x 3 2 0 1 0         │  指法图, 64px
 *         │  ● ● ● ● ● ●         │
 *   y=208 ├──────────────────────┤
 *         │  就绪                │  状态, 32px
 *   y=240 └──────────────────────┘
 *
 * 竖直方向 240px 全用满, 没有一处浪费 —— 因为远距离看,
 * 每一像素的高度都直接换算成"能不能看清"。
 */
#include "ui_screen.h"

#include <stdio.h>
#include <string.h>

/* ---------------- 布局常量 ---------------- */
#define QUICK_BAR_H   30      /* 顶部快捷槽位条 */
#define NAME_Y        QUICK_BAR_H          /* 32 */
#define NAME_H        88                   /* 和弦名区 */
#define CN_Y          (NAME_Y + NAME_H)    /* 120 */
#define CN_H          24
#define FING_Y        (CN_Y + CN_H + 2)    /* 146 */
#define FING_H        62
#define STATUS_H      30
#define STATUS_Y      (UI_H - STATUS_H)    /* 210 */

/* ---------------- 工具 ---------------- */

static void copy_str(char *dst, size_t cap, const char *src) {
    if (!dst || cap == 0) return;
    if (!src) { dst[0] = '\0'; return; }
    strncpy(dst, src, cap - 1);
    dst[cap - 1] = '\0';
}

/* 和弦配色: 大三和弦青蓝, 小三和弦暖黄。
 * 为什么按"性质"而不是按"名字"配色:
 *   远距离斜视时, 颜色是最先被识别的特征。让用户先看出"大还是小",
 *   再细看具体是哪个和弦 —— 这个信息层级符合演奏时的心理需求。 */
static uint16_t chord_color(int is_minor) {
    return is_minor ? C_YELLOW : C_CYAN;
}

/* ---------------- 初始化 ---------------- */

void ui_screen_init(ui_screen_t *s) {
    if (!s) return;
    memset(s, 0, sizeof(*s));
    ui_canvas_init(&s->canvas);
    s->state.mode = UI_MODE_PLAY;
    s->state.active = -1;
    s->state.string_idx = -1;
    s->state.quick_count = 0;
    s->state.status = "就绪";
    s->dirty = 1;
}

void ui_screen_set_quick_chords(ui_screen_t *s,
                                const ui_quick_chord_t *chords, int n) {
    if (!s || !chords || n <= 0) return;
    if (n > UI_QUICK_CHORDS) n = UI_QUICK_CHORDS;
    for (int i = 0; i < n; i++) s->state.quick[i] = chords[i];
    s->state.quick_count = n;
    if (s->state.active < 0) s->state.active = 0;
    s->dirty = 1;
}

/* ---------------- 状态更新 ---------------- */

void ui_set_mode(ui_screen_t *s, ui_mode_t m) {
    if (!s || m >= UI_MODE_COUNT) return;
    s->state.mode = m;
    /* 切回演奏页时刷新状态文字, 避免上一页的"准了"残留过来。
     * 注意: status 是 const char*, 不能拿它和字符串字面量比指针(那是未定义行为),
     * 所以这里直接按机构状态重算, 不做字符串比较。 */
    if (m == UI_MODE_PLAY) {
        s->state.status = s->state.mech_moving ? "压弦中"
                        : (s->state.mech_ready ? "就绪" : "待机");
    }
    s->dirty = 1;
}

void ui_select_quick(ui_screen_t *s, int idx) {
    if (!s) return;
    if (idx < 0 || idx >= s->state.quick_count) return;
    if (s->state.active == idx) return;
    s->state.active = idx;
    /* 换了和弦, 机械机构还没动, 先标成"未就绪" */
    s->state.mech_ready = 0;
    s->state.mech_moving = 0;
    s->state.status = "准备压弦";
    s->dirty = 1;
}

void ui_quick_step(ui_screen_t *s, int delta) {
    if (!s || s->state.quick_count <= 0) return;
    int n = s->state.quick_count;
    int cur = s->state.active < 0 ? 0 : s->state.active;
    int nxt = (cur + delta) % n;
    if (nxt < 0) nxt += n;
    ui_select_quick(s, nxt);
}

void ui_set_mech_state(ui_screen_t *s, int moving, int ready) {
    if (!s) return;
    if (s->state.mech_moving == moving && s->state.mech_ready == ready) return;
    s->state.mech_moving = moving;
    s->state.mech_ready = ready;
    if (moving)      s->state.status = "压弦中";
    else if (ready)  s->state.status = "就绪";
    else             s->state.status = "待机";
    s->dirty = 1;
}

void ui_update_tuner(ui_screen_t *s, const char *note, double freq,
                     double cents, int string_idx, ui_tune_state_t st) {
    if (!s) return;
    copy_str(s->state.note_name, sizeof(s->state.note_name), note);
    s->state.freq_hz = freq;
    s->state.cents = cents;
    s->state.string_idx = string_idx;
    s->state.tune_state = st;

    if (!note || !note[0] || freq <= 0.0) {
        s->state.status = "等待拨弦";
    } else {
        switch (st) {
            case UI_TUNE_LOW:  s->state.status = "调紧"; break;
            case UI_TUNE_HIGH: s->state.status = "调松"; break;
            case UI_TUNE_OK:   s->state.status = "准了"; break;
            default:           s->state.status = "识别中"; break;
        }
    }
    s->dirty = 1;
}

/* ---------------- 顶部快捷槽位条 ----------------
 * 6 格, 每格显示和弦名 + 序号。
 * 当前选中格用实心高亮 + 白框, 一眼可辨。
 * 这同时也是"6 个触摸键"的视觉映射 —— 位置和物理键一一对应。 */
void ui_draw_quick_bar(ui_canvas_t *c, const ui_state_t *st, int y, int h) {
    if (!c || !st) return;
    ui_fill_rect(c, 0, y, UI_W, h, C_BLACK);

    int n = st->quick_count > 0 ? st->quick_count : UI_QUICK_CHORDS;
    /* 每格宽度: 240 / 6 = 40。第 6 格右边缘对齐 240 会差 1px, 补齐即可 */
    int cw = UI_W / n;

    for (int i = 0; i < n; i++) {
        int x0 = i * cw;
        int x1 = (i == n - 1) ? UI_W : (i + 1) * cw;
        int active = (i == st->active);

        uint16_t bg = active ? C_WHITE : C_DARKGRAY;
        if (active) ui_fill_rect(c, x0, y, x1 - x0, h, bg);

        /* 只有当前选中的格子才写序号 —— 其他格子留给和弦名。
         * 原因: 格子只有 40px 宽, "序号 + 和弦名"挤在一起会糊成一团,
         * 而序号本身信息量低(用户靠物理键位记顺序, 不靠数字读)。 */
        /* 所有格子统一: 只画和弦名, 居中。
         * 序号不画 —— 格子只有 40px 宽, "序号+名字"挤在一起会糊;
         * 而且用户靠物理键的位置记顺序(键 1 对应格 1), 数字是冗余信息。
         * 选中态靠白底反色区分, 远距离最醒目。 */
        const char *nm = st->quick[i].name;
        if (nm && nm[0]) {
            int tw = ui_text_width(nm, UI_FONT_MED);
            uint16_t nc = active ? C_BLACK : chord_color(st->quick[i].is_minor);
            int tx = x0 + (x1 - x0 - tw) / 2;
            if (tx < x0 + 1) tx = x0 + 1;
            ui_text(c, tx, y + (h - 11) / 2, nm, UI_FONT_MED, nc);
        }

        /* 格间分隔线 */
        if (i > 0) ui_line_v(c, x0, y + 3, y + h - 4, C_BLACK);
    }
    ui_line_h(c, 0, UI_W - 1, y + h - 1, C_MIDGRAY);
}

/* ---------------- 指法图 ----------------
 * 横排 6 弦。上方一行写品位数字(x / 0 / 1..n),
 * 下方画 6 个点表示手指位置。
 *
 * 为什么要"数字 + 点"两行:
 *   数字精确(告诉你是第几品), 点直观(告诉你有几根手指)。
 *   远距离下数字可能糊, 但点的数量和位置还能看出轮廓。 */
void ui_draw_fingering(ui_canvas_t *c, int x, int y, int w, int h,
                       const int *fingering, int fret_min, int fret_max) {
    (void)fret_max;
    if (!c || !fingering || w < 60 || h < 30) return;

    int cw = w / 6;                 /* 每弦占的宽度 */
    int dot_r = (h - 30) / 2;
    if (dot_r > 9) dot_r = 9;
    if (dot_r < 4) dot_r = 4;
    int dot_y = y + 26 + dot_r;

    /* 弦间距均匀铺开 */
    for (int i = 0; i < 6; i++) {
        int cx = x + cw * i + cw / 2;
        int f = fingering[i];

        char buf[8];
        uint16_t col;
        if (f < 0) {
            copy_str(buf, sizeof(buf), "x");
            col = C_RED;            /* 不弹, 红色最醒目 */
        } else if (f == 0) {
            copy_str(buf, sizeof(buf), "0");
            col = C_GREEN;          /* 空弦, 绿色 */
        } else {
            snprintf(buf, sizeof(buf), "%d", f > 99 ? 99 : f);
            col = C_WHITE;          /* 按弦, 白色 */
        }

        /* 品位数字, 居中 */
        int tw = ui_text_width(buf, UI_FONT_MED);
        ui_text(c, cx - tw / 2, y + 2, buf, UI_FONT_MED, col);

        /* 手指圆点 */
        if (f > 0) {
            ui_fill_circle(c, cx, dot_y, dot_r, col);
        } else if (f == 0) {
            /* 空弦: 空心圆 */
            ui_rect_border(c, cx - dot_r, dot_y - dot_r,
                           dot_r * 2 + 1, dot_r * 2 + 1, col, 2);
        } else {
            /* 不弹: 短横线 */
            ui_fill_rect(c, cx - dot_r, dot_y - 1, dot_r * 2 + 1, 3, col);
        }

        /* 弦号(最下面, 小字) */
        snprintf(buf, sizeof(buf), "%d", 6 - i);
        tw = ui_text_width(buf, UI_FONT_SMALL);
        ui_text(c, cx - tw / 2, dot_y + dot_r + 3, buf, UI_FONT_SMALL, C_MIDGRAY);

        /* 弦分隔线 */
        if (i > 0) ui_line_v(c, x + cw * i, y + 2, y + h - 4, C_DARKGRAY);
    }
    (void)fret_min;
}

/* ---------------- 演奏页 ---------------- */

void ui_draw_play(ui_canvas_t *c, const ui_state_t *st) {
    if (!c || !st) return;

    /* 顶部: 快捷槽位 */
    ui_draw_quick_bar(c, st, 0, QUICK_BAR_H);

    int idx = st->active;
    if (idx < 0 || idx >= st->quick_count) {
        ui_fill_rect(c, 0, NAME_Y, UI_W, NAME_H, C_BLACK);
        ui_text_center(c, UI_W / 2, NAME_Y + 20, "- -", UI_FONT_BIG, C_MIDGRAY);
        ui_text_cn_center(c, UI_W / 2, CN_Y + 4, "请选择和弦",
                          UI_FONT_CN16, C_MIDGRAY);
        ui_fill_rect(c, 0, STATUS_Y, UI_W, STATUS_H, C_BLACK);
        ui_line_h(c, 0, UI_W - 1, STATUS_Y, C_MIDGRAY);
        ui_text_cn_center(c, UI_W / 2, STATUS_Y + 6, st->status ? st->status : "",
                          UI_FONT_CN16, C_WHITE);
        return;
    }

    const ui_quick_chord_t *q = &st->quick[idx];
    uint16_t cc = chord_color(q->is_minor);

    /* ---- 和弦名超大字 ----
     * 这是整屏最重要的元素。为了让它尽可能大, 做法是:
     *   用 32px 字库渲染 + 整数倍放大(像素复制), 得到等效 64/96px。
     * 为什么不直接生成 64px 字库: 字库会大 4 倍(几十 KB -> 上百 KB),
     *   而放大只是遍历像素时多写几次, 零额外存储。对单色点阵字,
     *   Nx 放大的视觉效果和原生大字几乎一样 —— 点阵放大本来就是方块变大。
     * 另外放大后边缘是"硬"的, 在小屏上反而比抗锯齿更清晰。 */
    ui_fill_rect(c, 0, NAME_Y, UI_W, NAME_H, C_BLACK);
    {
        int len = (int)strlen(q->name);
        /* 字符少就放得更大。1~2 字符(C/G)用 3x, 3 字符以上(Am7)用 2x。 */
        int scale = (len <= 2) ? 3 : 2;
        int tw = ui_text_width(q->name, UI_FONT_BIG);
        while (tw * scale > UI_W - 12 && scale > 1) scale--;

        int th = 32 * scale;
        int ox = (UI_W - tw * scale) / 2;
        int oy = NAME_Y + (NAME_H - th) / 2;
        if (oy < NAME_Y) oy = NAME_Y;

        ui_text_scaled(c, ox, oy, q->name, UI_FONT_BIG, cc, scale);
    }

    /* ---- 中文名 ---- */
    ui_fill_rect(c, 0, CN_Y, UI_W, CN_H, C_BLACK);
    if (q->name_cn[0]) {
        ui_text_cn_center(c, UI_W / 2, CN_Y + 4, q->name_cn,
                          UI_FONT_CN16, C_WHITE);
    }

    /* ---- 指法图 ---- */
    ui_fill_rect(c, 0, FING_Y, UI_W, FING_H, C_BLACK);
    ui_line_h(c, 0, UI_W - 1, FING_Y - 1, C_DARKGRAY);
    ui_line_h(c, 0, UI_W - 1, FING_Y + FING_H, C_DARKGRAY);
    ui_draw_fingering(c, 4, FING_Y, UI_W - 8, FING_H,
                      q->fingering, q->fret_min, q->fret_max);

    /* ---- 底部状态 ---- */
    ui_fill_rect(c, 0, STATUS_Y, UI_W, STATUS_H, C_BLACK);

    /* 机械状态用色块 + 文字双重表达(远距离色块先被看到) */
    uint16_t sc = C_MIDGRAY;
    if (st->mech_moving)      sc = C_ORANGE;
    else if (st->mech_ready)  sc = C_GREEN;

    ui_fill_rect(c, 6, STATUS_Y + 7, 8, 16, sc);
    if (st->status && st->status[0]) {
        ui_text_cn(c, 22, STATUS_Y + 6, st->status, UI_FONT_CN16, sc);
    }

    /* 右上角: 机构就绪指示灯 */
    if (st->mech_ready) {
        ui_fill_circle(c, UI_W - 12, STATUS_Y + 15, 6, C_GREEN);
    }
}

/* ---------------- 调音页 ----------------
 * 调音页同样按"远距离"设计: 音名超大字 + 一根粗指针条。
 * 不用数字表达音分 —— 远距离读不到小数。 */

void ui_draw_tuner(ui_canvas_t *c, const ui_state_t *st) {
    if (!c || !st) return;

    ui_fill_rect(c, 0, 0, UI_W, UI_H, C_BLACK);

    if (!st->note_name[0] || st->freq_hz <= 0.0) {
        ui_text_center(c, UI_W / 2, 70, "- -", UI_FONT_BIG, C_MIDGRAY);
        ui_text_cn_center(c, UI_W / 2, 130, "等待拨弦", UI_FONT_CN16, C_MIDGRAY);
        return;
    }

    uint16_t nc = C_WHITE;
    if (st->tune_state == UI_TUNE_OK)        nc = C_GREEN;
    else if (st->tune_state == UI_TUNE_LOW)  nc = C_RED;
    else if (st->tune_state == UI_TUNE_HIGH) nc = C_ORANGE;

    /* 音名 3x 放大 */
    int tw = ui_text_width(st->note_name, UI_FONT_BIG);
    int scale = 3;
    while (tw * scale > UI_W - 20 && scale > 1) scale--;
    ui_text_scaled(c, (UI_W - tw * scale) / 2, 40,
                   st->note_name, UI_FONT_BIG, nc, scale);

    /* 偏差条: 一条粗横条 + 中央标记, 远距离只看"指针在左还是右" */
    int bx = 16, bw = UI_W - 32, by = 150, bh = 24;
    ui_fill_rect(c, bx, by, bw, bh, C_DARKGRAY);
    int cx = bx + bw / 2;
    /* 中央 ±5 音分的容差区 */
    ui_fill_rect(c, cx - 5, by, 10, bh, C_DARKGREEN);

    double t = (st->cents + 50.0) / 100.0;
    if (t < 0) t = 0;
    if (t > 1) t = 1;
    int px = bx + (int)(t * (bw - 8) + 0.5);
    if (px < bx) px = bx;
    if (px > bx + bw - 8) px = bx + bw - 8;
    ui_fill_rect(c, px, by - 6, 8, bh + 12, nc);

    /* 底部状态 */
    ui_fill_rect(c, 0, STATUS_Y, UI_W, STATUS_H, C_BLACK);
    ui_line_h(c, 0, UI_W - 1, STATUS_Y, C_MIDGRAY);
    ui_text_cn_center(c, UI_W / 2, STATUS_Y + 6,
                      st->status ? st->status : "", UI_FONT_CN16, nc);
}

/* ---------------- 渲染入口 ---------------- */

int ui_screen_render(ui_screen_t *s) {
    if (!s || !s->dirty) return 0;

    ui_canvas_t *c = &s->canvas;
    ui_canvas_invalidate(c);
    ui_canvas_clear(c, C_BLACK);

    switch (s->state.mode) {
        case UI_MODE_PLAY:  ui_draw_play(c, &s->state);  break;
        case UI_MODE_TUNER: ui_draw_tuner(c, &s->state); break;
        default: break;
    }

    s->dirty = 0;
    return 1;
}
