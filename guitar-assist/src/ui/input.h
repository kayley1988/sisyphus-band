/*
 * input.h - 四键输入(Up / Down / Set / ENT)
 *
 * ============ 为什么用"焦点导航"而不是"触摸" ============
 *
 * 这是无障碍项目的关键决策, 值得写清楚:
 *
 * 【触摸屏的问题】
 *   触摸的本质是"用位置指定目标"。视障用户看不见屏幕, 也就不知道
 *   有什么东西、在哪里 —— 触摸对他们是【不可用】的, 不是"不好用"。
 *   所以本项目不做触摸。
 *
 * 【四键 + 焦点的优势】
 *   焦点导航把"位置"换成"顺序": 你不需要看见, 只需要知道
 *   "当前选中的是什么"(靠语音播报)和"按一下会切到哪个"。
 *   学习成本极低, 而且对盲人是【完全可用】的。
 *
 * 【核心交互约定】
 *   每按一次键 = 一次语音播报 + 一次振动。
 *   用户靠"按一下、听一下"就能操作, 全程不需要看屏。
 *   屏只服务于陪同者(老师/家长)和听力障碍用户。
 *
 * ============ 按键接法(独立 GPIO) ============
 *
 *   按键一端接 GPIO, 另一端接 GND。GPIO 内部/外部上拉。
 *   按下 = 读到低电平(0), 松开 = 高电平(1)。
 *
 *   为什么不用中断(edge trigger):
 *     按键抖动(机械触点弹跳)会产生几十个假边沿, 中断里处理很麻烦。
 *     poll() + 20ms 消抖简单可靠, 且我们的响应要求只有几十毫秒,
 *     轮询完全够用, 还省了一个线程。
 *
 * ============ 长按 ============
 *
 *   短按和长按区分(见 input_key_t):
 *     短按 = 走焦点 / 确认
 *     长按 = 连续快进(按住 Up 快速翻列表)、或触发"重读"
 *   LVGL 里这靠 lv_indev 的 long_press 实现, 我们自己在 poll 里做。
 */
#ifndef GA_INPUT_H
#define GA_INPUT_H

#include <stdint.h>

/* 四个物理键 */
typedef enum {
    GA_KEY_NONE = 0,
    GA_KEY_UP,
    GA_KEY_DOWN,
    GA_KEY_SET,
    GA_KEY_ENT,
    GA_KEY_COUNT
} ga_key_t;

/* 按键事件类型。长按会在持续期反复发 REPEAT。 */
typedef enum {
    GA_KEY_EV_NONE = 0,
    GA_KEY_EV_PRESS,     /* 刚按下(短按判定前就会发, 用于即时反馈) */
    GA_KEY_EV_SHORT,     /* 短按(松开时判定) */
    GA_KEY_EV_LONG,      /* 长按达到阈值(500ms), 只发一次 */
    GA_KEY_EV_REPEAT,    /* 长按后的连续重复(每 120ms 一次) */
} ga_key_event_type_t;

typedef struct {
    ga_key_event_type_t type;
    ga_key_t            key;
} ga_key_event_t;

/* 按键配置: GPIO 编号。用内核全局 GPIO 编号(和 st7789.h 里同一套编号规则)。
 * 不用的键传 -1。 */
typedef struct {
    int gpio_up;
    int gpio_down;
    int gpio_set;
    int gpio_ent;

    /* 消抖时间(ms)。机械按键 15~25ms 比较合适。 */
    int debounce_ms;
    /* 长按判定阈值(ms) */
    int long_ms;
    /* 长按后的重复间隔(ms) */
    int repeat_ms;
    /* 按下时的有效电平(0=低电平有效, 1=高电平有效)。
     * 按键接 GND + 上拉 → 低电平有效 → 填 0。 */
    int active_low;
} ga_input_cfg_t;

/* 运行时状态(内部用, 调用者不用管) */
typedef struct {
    int  fd[GA_KEY_COUNT];      /* 每个键一个 GPIO value fd, -1 表示未用 */
    char path[GA_KEY_COUNT][64];

    int  cfg_debounce_ms, cfg_long_ms, cfg_repeat_ms, cfg_active_low;

    /* 每个键的状态机 */
    struct {
        int  stable;            /* 消抖后的稳定电平(1=按下) */
        int  raw_last;          /* 上次读到的原始值 */
        long long raw_change_ms;/* 原始值最后一次变化的时间 */
        long long press_ms;     /* 本次按下的时刻 */
        int  long_fired;        /* 长按事件已发过 */
        long long last_repeat_ms;
    } st[GA_KEY_COUNT];

    int  ok;                    /* 是否有至少一个键可用 */
} ga_input_t;

/* 打开按键。cfg 传 NULL 用默认配置(全 -1, 即没有键可用)。
 * 返回 0 成功(至少一个键可用), -1 表示一个键都打不开。 */
int  ga_input_open(ga_input_t *in, const ga_input_cfg_t *cfg);
void ga_input_close(ga_input_t *in);

/* 轮询一次, 取一个事件。没有事件返回 GA_KEY_EV_NONE。
 * 调用频率建议 5~20ms 一次(主循环里调)。
 * 一次调用最多返回一个事件, 保证事件不丢(内部按固定顺序扫描)。 */
ga_key_event_t ga_input_poll(ga_input_t *in);

/* 把按键事件翻译成"人类可读"的中文, 仅用于调试日志 */
const char *ga_key_name(ga_key_t k);
const char *ga_key_event_name(ga_key_event_type_t t);

/* ============ 左手 6 键(和弦一键直达) ============
 *
 * 交互模型和右手 4 键【本质不同】, 所以单独一套 API, 不硬塞进 ga_key_t:
 *
 *   右手 4 键: 焦点导航 —— 用户要"知道当前选中什么"(靠屏幕/语音),
 *              再按键移动焦点。适合滚动列表、进菜单。
 *   左手 6 键: 一键直达 —— 一个键 = 一个和弦, 按下去就是"要弹这个"。
 *              不需要任何当前状态提示, 学习成本为零。
 *
 * 这是本项目无障碍设计的关键一环: 演奏时左手在琴颈上, 不能"先看当前
 * 是哪个和弦、再按两下移动过去"。一个键直达一个和弦, 才是可用的。
 *
 * ★ 物理差异(必须区分, 否则按键完全不响应):
 *   机械按键(右手): 一端接 GPIO、一端接 GND, 内部上拉
 *                   -> 松开 = 高电平, 按下 = 低电平  => active_low = 1
 *   TTP223 触摸(左手): 模块 OUT 直出
 *                   -> 松开 = 低电平, 按下 = 高电平  => active_low = 0
 *   两套的 active_low【默认值相反】, 这是实测结论(见 29 号验收笔记)。
 *
 * 语义: 【按下沿】触发一次(消抖稳定后 0->1), 松开重新武装。
 *   不做长按/连发 —— 选和弦不需要连发, 连发还会连续压弦, 危险。
 */
#define GA_LEFTKEY_COUNT 6

typedef struct {
    int gpio[GA_LEFTKEY_COUNT];   /* 每个键的 GPIO 全局编号, -1 = 该键不用 */
    int debounce_ms;              /* <=0 用 20ms */
    int active_low;               /* 1=低电平有效(机械键); 0=高电平有效(TTP223) */
} ga_leftkey_cfg_t;

typedef struct {
    int  fd[GA_LEFTKEY_COUNT];          /* value 文件 fd, -1 = 未用 */
    int  stable[GA_LEFTKEY_COUNT];      /* 消抖后的状态(1=按下) */
    int  raw_last[GA_LEFTKEY_COUNT];    /* 上次原始状态 */
    long long raw_change_ms[GA_LEFTKEY_COUNT];
    int  fired[GA_LEFTKEY_COUNT];       /* 本次按下是否已上报(松开才复位) */

    int  debounce_ms, active_low;
    int  ok;                            /* 至少一个键可用 */
    int  count;                         /* 实际打开的键数 */
} ga_leftkey_t;

/* 打开左手键。cfg 传 NULL 用默认(全 -1 -> 没有键可用)。
 * 返回 0 成功(至少一个键可用), -1 表示一个都打不开。 */
int  ga_leftkey_open(ga_leftkey_t *lk, const ga_leftkey_cfg_t *cfg);
void ga_leftkey_close(ga_leftkey_t *lk);

/* 轮询一次。返回【本次新按下】的键索引 0..5, 没有则 -1。
 * 建议和主循环帧率一致(20~30ms 一次)。 */
int  ga_leftkey_poll(ga_leftkey_t *lk);

#endif /* GA_INPUT_H */
