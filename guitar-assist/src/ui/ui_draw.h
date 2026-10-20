/*
 * ui_draw.h - 轻量绘图原语(不依赖 LVGL)
 *
 * 为什么要自己写这个:
 *   1. LVGL 是几十万行的库, 编译进项目要十几分钟, 而且配置麻烦;
 *   2. 我们只需要画几种固定元素(大字、矩形条、圆点、格栅),
 *      用不着 LVGL 的完整控件系统;
 *   3. 【最重要的】: 自己画能让屏在【接上线的那一刻】就亮起来,
 *      不用先花一天时间折腾 LVGL 编译。快速验证硬件比什么都重要。
 *
 * 如果以后需要更复杂的界面(滑动列表、动画、多页面), 再引入 LVGL。
 * 当前的界面需求(调音+和弦), 自己画完全够用, 而且性能更好。
 *
 * 颜色用 RGB565(和屏幕原生格式一致), 避免运行时转换。
 */
#ifndef GA_UI_DRAW_H
#define GA_UI_DRAW_H

#include <stdint.h>
#include <stddef.h>

/* ---------- RGB565 颜色定义 ----------
 * 取色原则(无障碍): 黑底 + 高亮度前景, 对比度最大化。
 * 不用中间灰, 因为小屏 + 户外光下灰底看不清。 */
#define C_BLACK     0x0000
#define C_WHITE     0xFFFF
#define C_RED       0xF800   /* 偏低/警示 */
#define C_GREEN     0x07E0   /* 准了/成功 */
#define C_BLUE      0x001F
#define C_YELLOW    0xFFE0   /* 高亮/当前选中 */
#define C_ORANGE    0xFD20
#define C_CYAN      0x07FF
#define C_DARKGRAY  0x18E3   /* 未激活元素 */
#define C_MIDGRAY   0x4208   /* 次要文字 */
#define C_DARKGREEN 0x0320   /* 准了的背景块 */

/* 屏幕尺寸。1.54寸 240x240。
 * 如果换成别的尺寸, 只改这里 —— 所有布局都是按比例算的。 */
#define UI_W  240
#define UI_H  240

/* ---------- 画布 ----------
 * 所有绘图都先画到这块内存, 最后一整块刷到屏幕。
 * 这是标准的双缓冲思路: 避免"画一半就刷屏"导致的闪烁。 */
typedef struct {
    uint16_t px[UI_W * UI_H];
    /* 脏矩形: 记录本次实际改动的范围, 只刷这一块, 省 SPI 带宽。
     * 对 SPI 屏特别重要 —— 全屏 115KB, 局部可能只有几 KB。 */
    int dirty_x0, dirty_y0, dirty_x1, dirty_y1;
    int dirty;
} ui_canvas_t;

void ui_canvas_init(ui_canvas_t *c);
void ui_canvas_clear(ui_canvas_t *c, uint16_t color);

/* 标记/查询脏矩形 */
void ui_canvas_invalidate(ui_canvas_t *c);
void ui_canvas_mark(ui_canvas_t *c, int x, int y, int w, int h);
void ui_canvas_get_dirty(const ui_canvas_t *c, int *x, int *y, int *w, int *h);

/* ---------- 基础图形 ---------- */

void ui_fill_rect(ui_canvas_t *c, int x, int y, int w, int h, uint16_t color);
void ui_rect_border(ui_canvas_t *c, int x, int y, int w, int h,
                    uint16_t color, int thickness);
void ui_fill_circle(ui_canvas_t *c, int cx, int cy, int r, uint16_t color);
void ui_line_h(ui_canvas_t *c, int x0, int x1, int y, uint16_t color);
void ui_line_v(ui_canvas_t *c, int x, int y0, int y1, uint16_t color);

/* ---------- 文字 ----------
 * 内置点阵字库, 不依赖 FreeType(省几 MB 依赖)。
 *
 * 为什么用点阵而不是矢量:
 *   小屏上点阵字反而更清晰(矢量字在小字号下会糊),
 *   而且点阵渲染是纯查表, 零计算开销。
 *
 * 字体 ID。数值仅作索引, 与 ui_draw.c 里的字库描述表 g_fonts[] 一一对应 ——
 * 加字库时两边同时加, 顺序不能错。
 *
 *   UI_FONT_SMALL  10px ASCII —— 辅助信息(频率、置信度、音分)
 *   UI_FONT_MED    16px ASCII —— 和弦名、状态小字
 *   UI_FONT_BIG    32px ASCII —— 音名大字(占屏 1/3 高度)
 *   UI_FONT_CN12   12px 汉字 —— 小号中文
 *   UI_FONT_CN16   16px 汉字 —— 常用中文标签
 *
 * 汉字支持: 只内置界面用到的百余个字, 不做全字库(全字库要几 MB)。
 *          需要更多汉字时, 用 tools/gen_font.py 重新生成(见 DEPLOY.md)。 */
#define UI_FONT_SMALL  0
#define UI_FONT_MED    1
#define UI_FONT_BIG    2
#define UI_FONT_CN12   3
#define UI_FONT_CN16   4

/* 绘制 ASCII 文本。返回绘制后的 x 坐标(便于连续绘制)。 */
int ui_text(ui_canvas_t *c, int x, int y, const char *text,
            int font, uint16_t color);

/* 居中绘制 */
void ui_text_center(ui_canvas_t *c, int cx, int y, const char *text,
                    int font, uint16_t color);

/* 测量文本宽度 */
int ui_text_width(const char *text, int font);

/* 带整数倍放大的文本绘制。
 *
 * 为什么需要它: 远距离观看需要超大字号(等效 64~96px), 但直接生成
 * 那么大的点阵字库会让字库体积膨胀 4~9 倍。而点阵字体做整数倍放大
 * 是"无损"的 —— 每个源像素复制成 scale x scale 个, 视觉上和原生大字
 * 几乎一致, 且边缘更"硬"(小屏上反而更清晰), 存储开销为零。
 *
 * scale=1 时等价于 ui_text()。scale<=0 时按 1 处理。
 * 返回绘制结束的 x 坐标。 */
int ui_text_scaled(ui_canvas_t *c, int x, int y, const char *text,
                   int font, uint16_t color, int scale);

/* 带缩放的居中绘制 */
void ui_text_scaled_center(ui_canvas_t *c, int cx, int y, const char *text,
                           int font, uint16_t color, int scale);

/* 绘制中文字符串(UTF-8)。ASCII 自动分流到对应 ASCII 字库, 可混排。
 * 仅支持内置字表里有的字, 缺字显示为空心方块。 */
int ui_text_cn(ui_canvas_t *c, int x, int y, const char *utf8,
               int font, uint16_t color);

/* 测量中文/混排字符串宽度(像素) */
int ui_text_cn_width(const char *utf8, int font);

void ui_text_cn_center(ui_canvas_t *c, int cx, int y, const char *utf8,
                       int font, uint16_t color);

#endif /* GA_UI_DRAW_H */
