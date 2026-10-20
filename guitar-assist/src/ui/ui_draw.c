/*
 * ui_draw.c - 轻量绘图实现
 *
 * 关于字库管理的说明:
 *   5 个字库头文件(f10/f16/f32/cn12/cn16)各自定义 static 数组。
 *   如果直接 include 全部, 会因为 static 变量在各编译单元内可见,
 *   但类型 ga_glyph_t 需要保护(GA_GLYPH_T_DEFINED), 符号前缀各不相同,
 *   所以可以安全地在同一个 .c 里 include。
 *
 *   访问方式: 通过下面 fb_desc[] 描述表按 font_id 索引,
 *   拿到 (blob, table, count, 行高) 四元组, 统一走一份渲染代码。
 *   这样新增字库只要往表里加一行, 不用改渲染逻辑。
 */
#include "ui_draw.h"

#include <stdio.h>
#include <string.h>

/* 引入生成的字库(各文件符号前缀唯一: f10_/f16_/f32_/cn12_/cn16_) */
#include "font/font_cn12.h"
#include "font/font_cn16.h"
#include "font/font_num10.h"
#include "font/font_num16.h"
#include "font/font_num32.h"

/* ---------------- 画布 ---------------- */

void ui_canvas_init(ui_canvas_t *c) {
    if (!c) return;
    memset(c->px, 0, sizeof(c->px));
    ui_canvas_invalidate(c);
}

void ui_canvas_clear(ui_canvas_t *c, uint16_t color) {
    if (!c) return;
    for (int i = 0; i < UI_W * UI_H; i++) c->px[i] = color;
    ui_canvas_mark(c, 0, 0, UI_W, UI_H);
}

void ui_canvas_invalidate(ui_canvas_t *c) {
    if (!c) return;
    c->dirty = 0;
    c->dirty_x0 = UI_W; c->dirty_y0 = UI_H;
    c->dirty_x1 = -1;   c->dirty_y1 = -1;
}

void ui_canvas_mark(ui_canvas_t *c, int x, int y, int w, int h) {
    if (!c) return;
    int x1 = x + w, y1 = y + h;
    if (c->dirty) {
        if (x  < c->dirty_x0) c->dirty_x0 = x;
        if (y  < c->dirty_y0) c->dirty_y0 = y;
        if (x1 > c->dirty_x1) c->dirty_x1 = x1;
        if (y1 > c->dirty_y1) c->dirty_y1 = y1;
    } else {
        c->dirty = 1;
        c->dirty_x0 = x;  c->dirty_y0 = y;
        c->dirty_x1 = x1; c->dirty_y1 = y1;
    }
}

void ui_canvas_get_dirty(const ui_canvas_t *c, int *x, int *y, int *w, int *h) {
    if (!c || !c->dirty) {
        if (x) *x = 0;
        if (y) *y = 0;
        if (w) *w = 0;
        if (h) *h = 0;
        return;
    }
    /* 裁剪到屏幕范围 */
    int x0 = c->dirty_x0 < 0 ? 0 : c->dirty_x0;
    int y0 = c->dirty_y0 < 0 ? 0 : c->dirty_y0;
    int x1 = c->dirty_x1 > UI_W ? UI_W : c->dirty_x1;
    int y1 = c->dirty_y1 > UI_H ? UI_H : c->dirty_y1;
    if (x) *x = x0;
    if (y) *y = y0;
    if (w) *w = (x1 > x0) ? x1 - x0 : 0;
    if (h) *h = (y1 > y0) ? y1 - y0 : 0;
}

/* ---------------- 基础图形 ---------------- */

void ui_fill_rect(ui_canvas_t *c, int x, int y, int w, int h, uint16_t color) {
    if (!c || w <= 0 || h <= 0) return;
    /* 裁剪: 负坐标要夹到 0, 越界要夹到屏宽/屏高 */
    int x0 = x < 0 ? 0 : x;
    int y0 = y < 0 ? 0 : y;
    int x1 = x + w; if (x1 > UI_W) x1 = UI_W;
    int y1 = y + h; if (y1 > UI_H) y1 = UI_H;
    if (x0 >= x1 || y0 >= y1) return;

    for (int yy = y0; yy < y1; yy++) {
        uint16_t *row = c->px + yy * UI_W;
        for (int xx = x0; xx < x1; xx++) row[xx] = color;
    }
    ui_canvas_mark(c, x0, y0, x1 - x0, y1 - y0);
}

void ui_rect_border(ui_canvas_t *c, int x, int y, int w, int h,
                    uint16_t color, int thickness) {
    if (thickness < 1) thickness = 1;
    ui_fill_rect(c, x, y, w, thickness, color);                      /* 上 */
    ui_fill_rect(c, x, y + h - thickness, w, thickness, color);      /* 下 */
    ui_fill_rect(c, x, y, thickness, h, color);                      /* 左 */
    ui_fill_rect(c, x + w - thickness, y, thickness, h, color);      /* 右 */
}

void ui_fill_circle(ui_canvas_t *c, int cx, int cy, int r, uint16_t color) {
    if (!c || r <= 0) return;
    int r2 = r * r;
    for (int dy = -r; dy <= r; dy++) {
        int yy = cy + dy;
        if (yy < 0 || yy >= UI_H) continue;
        /* 用勾股定理求这一行的半宽, 得到正圆 */
        int dx = 0;
        while ((dx + 1) * (dx + 1) + dy * dy <= r2) dx++;
        int x0 = cx - dx, x1 = cx + dx;
        if (x1 < 0 || x0 >= UI_W) continue;
        if (x0 < 0) x0 = 0;
        if (x1 >= UI_W) x1 = UI_W - 1;
        uint16_t *row = c->px + yy * UI_W;
        for (int xx = x0; xx <= x1; xx++) row[xx] = color;
    }
    ui_canvas_mark(c, cx - r, cy - r, r * 2 + 1, r * 2 + 1);
}

void ui_line_h(ui_canvas_t *c, int x0, int x1, int y, uint16_t color) {
    if (x0 > x1) { int t = x0; x0 = x1; x1 = t; }
    ui_fill_rect(c, x0, y, x1 - x0 + 1, 1, color);
}

void ui_line_v(ui_canvas_t *c, int x, int y0, int y1, uint16_t color) {
    if (y0 > y1) { int t = y0; y0 = y1; y1 = t; }
    ui_fill_rect(c, x, y0, 1, y1 - y0 + 1, color);
}

/* ---------------- 字库描述表 ----------------
 * 每个字库 = (位图数组, 字形表, 字形数, 标称行高, 缺字占位宽度)。
 * 行高用于多行排版和垂直居中; 缺字宽度用于画占位方块。 */
typedef struct {
    const uint8_t    *blob;
    const ga_glyph_t *table;
    int               count;
    int               nominal_h;
} ui_font_desc_t;

static const ui_font_desc_t g_fonts[] = {
    /* UI_FONT_SMALL = 0 */ { f10_blob,  f10_table,  f10_count,  10 },
    /* UI_FONT_MED   = 1 */ { f16_blob,  f16_table,  f16_count,  16 },
    /* UI_FONT_BIG   = 2 */ { f32_blob,  f32_table,  f32_count,  32 },
    /* UI_FONT_CN12  = 3 */ { cn12_blob, cn12_table, cn12_count, 12 },
    /* UI_FONT_CN16  = 4 */ { cn16_blob, cn16_table, cn16_count, 16 },
};

#define UI_FONT_COUNT ((int)(sizeof(g_fonts) / sizeof(g_fonts[0])))

static const ui_font_desc_t *font_get(int font_id) {
    if (font_id < 0 || font_id >= UI_FONT_COUNT) font_id = UI_FONT_MED;
    return &g_fonts[font_id];
}

/* 在字库里查一个码点。找到返回字形指针, 否则 NULL。 */
static const ga_glyph_t *glyph_find(const ui_font_desc_t *f, uint32_t code) {
    if (!f) return NULL;
    /* 线性查找。字库只有几十~一百多个字形, 一次文本绘制几十次比较,
     * 开销可忽略; 若以后字库上千, 这里再改成二分(表是按 code 升序生成的)。 */
    for (int i = 0; i < f->count; i++) {
        if (f->table[i].code == code) return &f->table[i];
    }
    return NULL;
}

/* 把一个字形画到画布, 支持整数倍放大。
 * 返回字形绘制宽度(已含放大)。
 *
 * 放大实现: 每个源像素点亮时, 在目标位置写 scale x scale 个像素。
 * 这比"先画小字再整体放大"少一遍扫描, 而且是流式写入, 缓存友好。 */
static int glyph_draw_scaled(ui_canvas_t *c, const ui_font_desc_t *f,
                             const ga_glyph_t *g, int x, int y,
                             uint16_t color, int scale) {
    if (scale < 1) scale = 1;

    if (!g) {
        /* 缺字: 画一个空心方块占位, 方便一眼看出"这个字没进字库" */
        int w = f ? f->nominal_h : 8;
        int h = f ? f->nominal_h : 10;
        ui_rect_border(c, x, y, w * scale, h * scale, C_MIDGRAY, scale);
        return w * scale + scale;
    }

    /* 空白字形(空格等): 有宽度但无位图(h==0)。只推进光标, 不画东西。 */
    if (g->h == 0 || g->stride == 0) {
        return g->w * scale;
    }

    const uint8_t *bits = f->blob + g->offset;
    int gw = g->w, gh = g->h;
    int stride = (int)g->stride;

    for (int gy = 0; gy < gh; gy++) {
        for (int gx = 0; gx < gw; gx++) {
            uint8_t byte = bits[gy * stride + gx / 8];
            if (!(byte & (0x80 >> (gx & 7)))) continue;

            /* 该源像素被点亮 —— 在目标上铺 scale x scale 的方块 */
            int px0 = x + gx * scale;
            int py0 = y + gy * scale;
            for (int dy = 0; dy < scale; dy++) {
                int py = py0 + dy;
                if (py < 0 || py >= UI_H) continue;
                uint16_t *row = c->px + py * UI_W;
                for (int dx = 0; dx < scale; dx++) {
                    int px = px0 + dx;
                    if (px < 0 || px >= UI_W) continue;
                    row[px] = color;
                }
            }
        }
    }
    ui_canvas_mark(c, x, y, gw * scale, gh * scale);
    return gw * scale;
}

/* 1x 的便捷包装(旧代码路径都用它) */
static int glyph_draw(ui_canvas_t *c, const ui_font_desc_t *f,
                      const ga_glyph_t *g, int x, int y, uint16_t color) {
    return glyph_draw_scaled(c, f, g, x, y, color, 1);
}

/* ---------------- ASCII 文本 ---------------- */

int ui_text_width(const char *text, int font) {
    if (!text) return 0;
    const ui_font_desc_t *f = font_get(font);
    int w = 0;
    for (const char *p = text; *p; p++) {
        const ga_glyph_t *g = glyph_find(f, (uint8_t)*p);
        w += g ? g->w : (f->nominal_h / 2 + 1);
    }
    return w;
}

int ui_text(ui_canvas_t *c, int x, int y, const char *text,
            int font, uint16_t color) {
    if (!c || !text) return x;
    const ui_font_desc_t *f = font_get(font);
    int cx = x;
    for (const char *p = text; *p; p++) {
        const ga_glyph_t *g = glyph_find(f, (uint8_t)*p);
        int adv = glyph_draw(c, f, g, cx, y, color);
        cx += adv;
    }
    return cx;
}

void ui_text_center(ui_canvas_t *c, int cx, int y, const char *text,
                    int font, uint16_t color) {
    int w = ui_text_width(text, font);
    ui_text(c, cx - w / 2, y, text, font, color);
}

int ui_text_scaled(ui_canvas_t *c, int x, int y, const char *text,
                   int font, uint16_t color, int scale) {
    if (!c || !text) return x;
    if (scale < 1) scale = 1;
    const ui_font_desc_t *f = font_get(font);
    int cx = x;
    for (const char *p = text; *p; p++) {
        const ga_glyph_t *g = glyph_find(f, (uint8_t)*p);
        int adv = glyph_draw_scaled(c, f, g, cx, y, color, scale);
        cx += adv;
    }
    return cx;
}

void ui_text_scaled_center(ui_canvas_t *c, int cx, int y, const char *text,
                           int font, uint16_t color, int scale) {
    if (scale < 1) scale = 1;
    int w = ui_text_width(text, font) * scale;
    ui_text_scaled(c, cx - w / 2, y, text, font, color, scale);
}

/* ---------------- UTF-8 / 中文文本 ---------------- */

/* 解码一个 UTF-8 字符。返回消耗字节数(>=1), 并把码点写入 *code。
 * 非法序列按单字节 ASCII 处理, 保证不会死循环。 */
static int utf8_decode(const uint8_t *p, uint32_t *code) {
    if (p[0] < 0x80) {
        *code = p[0];
        return 1;
    }
    if ((p[0] & 0xE0) == 0xC0 && (p[1] & 0xC0) == 0x80) {
        *code = ((uint32_t)(p[0] & 0x1F) << 6) | (p[1] & 0x3F);
        return 2;
    }
    if ((p[0] & 0xF0) == 0xE0 && (p[1] & 0xC0) == 0x80 && (p[2] & 0xC0) == 0x80) {
        *code = ((uint32_t)(p[0] & 0x0F) << 12)
              | ((uint32_t)(p[1] & 0x3F) << 6)
              | (p[2] & 0x3F);
        return 3;
    }
    if ((p[0] & 0xF8) == 0xF0 && (p[1] & 0xC0) == 0x80
        && (p[2] & 0xC0) == 0x80 && (p[3] & 0xC0) == 0x80) {
        *code = ((uint32_t)(p[0] & 0x07) << 18)
              | ((uint32_t)(p[1] & 0x3F) << 12)
              | ((uint32_t)(p[2] & 0x3F) << 6)
              | (p[3] & 0x3F);
        return 4;
    }
    *code = 0xFFFD;   /* 替换字符 */
    return 1;
}

/* 在中文字库与 ASCII 字库之间自动选: 码点 <0x80 用 ASCII 字库,
 * 否则用中文字库。这样同一句 "C 和弦" 能正确混排。 */
static const ga_glyph_t *glyph_find_any(const ui_font_desc_t **out_f,
                                        uint32_t code, int font) {
    const ui_font_desc_t *f = font_get(font);
    if (code < 0x80) {
        /* 数字/字母: 若当前是中文字库, 退回到同字号的 ASCII 字库 */
        const ui_font_desc_t *af = f;
        if (font == UI_FONT_CN12) af = &g_fonts[UI_FONT_SMALL];
        else if (font == UI_FONT_CN16) af = &g_fonts[UI_FONT_MED];
        const ga_glyph_t *g = glyph_find(af, code);
        if (g) { *out_f = af; return g; }
    }
    const ga_glyph_t *g = glyph_find(f, code);
    if (g) { *out_f = f; return g; }
    *out_f = f;
    return NULL;
}

int ui_text_cn(ui_canvas_t *c, int x, int y, const char *utf8,
               int font, uint16_t color) {
    if (!c || !utf8) return x;
    const uint8_t *p = (const uint8_t *)utf8;
    int cx = x;

    while (*p) {
        uint32_t code;
        int len = utf8_decode(p, &code);
        p += len;

        const ui_font_desc_t *f = NULL;
        const ga_glyph_t *g = glyph_find_any(&f, code, font);
        int adv = glyph_draw(c, f, g, cx, y, color);
        cx += adv + 1;   /* 字间留 1px */
    }
    return cx;
}

int ui_text_cn_width(const char *utf8, int font) {
    if (!utf8) return 0;
    const uint8_t *p = (const uint8_t *)utf8;
    int w = 0;
    while (*p) {
        uint32_t code;
        int len = utf8_decode(p, &code);
        p += len;
        const ui_font_desc_t *f = NULL;
        const ga_glyph_t *g = glyph_find_any(&f, code, font);
        w += (g ? g->w : 8) + 1;
    }
    return w > 0 ? w - 1 : 0;
}

void ui_text_cn_center(ui_canvas_t *c, int cx, int y, const char *utf8,
                       int font, uint16_t color) {
    int w = ui_text_cn_width(utf8, font);
    ui_text_cn(c, cx - w / 2, y, utf8, font, color);
}
