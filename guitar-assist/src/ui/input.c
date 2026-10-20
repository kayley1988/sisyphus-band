/*
 * input.c - 四键输入实现
 *
 * 用 Linux GPIO 字符设备接口(/dev/gpiochipN + GPIO_V2_GET_LINE_IOCTL)
 * 还是老的 sysfs(/sys/class/gpio)? 
 *
 * 选择: 优先 sysfs 的 value 文件, 因为它【兼容性最好】。
 *   泰山派的 4.19/5.10 内核两种都支持, 但 sysfs 写法简单几十行搞定,
 *   而且用户态读 value 文件是个普通 read(), 调试时也能手动 echo 测试。
 *   新内核(sysfs 被标记 deprecated)也仍然保留, 所以没有风险。
 *
 * 如果哪天 sysfs 真的被移除了, 换成 gpio 字符设备只要替换
 * gpio_open_line() / gpio_read_line() 两个函数, 其余逻辑不动。
 */
#include "input.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#include <time.h>

/* ---------------- 时间 ---------------- */

static long long now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (long long)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

/* ---------------- GPIO sysfs ---------------- */

/* 把 GPIO 导出并配成输入、上拉。
 * 返回 value 文件的 fd, 失败 -1。 */
static int gpio_open_line(int gpio, int active_low) {
    if (gpio < 0) return -1;

    char path[64];
    int fd;

    /* 1. export。如果已经导出过, 会返回 EBUSY, 这不是错误。 */
    fd = open("/sys/class/gpio/export", O_WRONLY);
    if (fd >= 0) {
        char buf[16];
        int n = snprintf(buf, sizeof(buf), "%d", gpio);
        if (write(fd, buf, (size_t)n) < 0 && errno != EBUSY) {
            /* 已导出时内核返回 EBUSY; 其他错误就继续试, 后面 open 会失败 */
        }
        close(fd);
    }

    /* 2. 方向设成输入 */
    snprintf(path, sizeof(path), "/sys/class/gpio/gpio%d/direction", gpio);
    fd = open(path, O_WRONLY);
    if (fd >= 0) {
        if (write(fd, "in", 2) < 0) { /* 失败不致命, 继续 */ }
        close(fd);
    }

    /* 3. 上拉。没接外部上拉时必须靠这个, 否则引脚悬空读数乱跳。
     * 注意: 有些内核/SOC 不支持上拉配置, 这时会失败, 属正常。 */
    snprintf(path, sizeof(path), "/sys/class/gpio/gpio%d/active_low", gpio);
    fd = open(path, O_WRONLY);
    if (fd >= 0) {
        if (write(fd, active_low ? "1" : "0", 1) < 0) { /* 忽略 */ }
        close(fd);
    }

    /* 4. 打开 value 文件读数据。
     * 注意: 这里用 O_RDONLY, 每次 read 前要 lseek 到 0,
     * 因为 sysfs 的 value 文件读完会到 EOF。 */
    snprintf(path, sizeof(path), "/sys/class/gpio/gpio%d/value", gpio);
    fd = open(path, O_RDONLY);
    if (fd < 0) return -1;
    return fd;
}

/* 读一行 GPIO 电平。返回 0/1, 失败返回 -1。 */
static int gpio_read_line(int fd) {
    if (fd < 0) return -1;
    char buf[4];
    if (lseek(fd, 0, SEEK_SET) < 0) return -1;
    ssize_t n = read(fd, buf, sizeof(buf) - 1);
    if (n <= 0) return -1;
    buf[n] = '\0';
    if (buf[0] == '0') return 0;
    if (buf[0] == '1') return 1;
    return -1;
}

/* ---------------- 打开/关闭 ---------------- */

int ga_input_open(ga_input_t *in, const ga_input_cfg_t *cfg) {
    if (!in) return -1;
    memset(in, 0, sizeof(*in));

    /* 默认配置 */
    int gpios[GA_KEY_COUNT] = { -1, -1, -1, -1 };
    int debounce = 20, long_ms = 500, repeat_ms = 120, active_low = 1;

    if (cfg) {
        gpios[GA_KEY_UP]   = cfg->gpio_up;
        gpios[GA_KEY_DOWN] = cfg->gpio_down;
        gpios[GA_KEY_SET]  = cfg->gpio_set;
        gpios[GA_KEY_ENT]  = cfg->gpio_ent;
        if (cfg->debounce_ms > 0) debounce = cfg->debounce_ms;
        if (cfg->long_ms > 0)     long_ms = cfg->long_ms;
        if (cfg->repeat_ms > 0)   repeat_ms = cfg->repeat_ms;
        active_low = cfg->active_low ? 1 : 0;
    }

    in->cfg_debounce_ms = debounce;
    in->cfg_long_ms     = long_ms;
    in->cfg_repeat_ms   = repeat_ms;
    in->cfg_active_low  = active_low;

    for (int i = 0; i < GA_KEY_COUNT; i++) {
        in->fd[i] = -1;
        in->st[i].stable = 0;
        in->st[i].raw_last = -1;
        in->st[i].raw_change_ms = now_ms();
        in->st[i].press_ms = 0;
        in->st[i].long_fired = 0;
        in->st[i].last_repeat_ms = 0;
    }

    int opened = 0;
    for (int i = GA_KEY_UP; i <= GA_KEY_ENT; i++) {
        if (gpios[i] < 0) continue;
        /* ★ 这里恒定传 0(不交给内核反转), 由 read_pressed() 在用户态统一判定。
         * 原因: sysfs 的 active_low=1 会让 value 文件【读数本身】被内核翻转;
         * 若代码里再翻转一次 = 双重反转, 表现为"按键完全没反应"。
         * 把极性判断收在一处, 才不会出现两套语义打架。 */
        int fd = gpio_open_line(gpios[i], 0);
        if (fd < 0) {
            fprintf(stderr, "[input] GPIO %d (%s) 打开失败: %s\n",
                    gpios[i], ga_key_name((ga_key_t)i), strerror(errno));
            continue;
        }
        in->fd[i] = fd;
        snprintf(in->path[i], sizeof(in->path[i]),
                 "/sys/class/gpio/gpio%d/value", gpios[i]);
        /* 读一次当前电平作为初值, 避免上电瞬间误判为"刚按下" */
        int v = gpio_read_line(fd);
        in->st[i].raw_last = (v == 1);
        in->st[i].stable = (v == 1);
        opened++;
    }

    in->ok = (opened > 0);
    return in->ok ? 0 : -1;
}

void ga_input_close(ga_input_t *in) {
    if (!in) return;
    for (int i = 0; i < GA_KEY_COUNT; i++) {
        if (in->fd[i] >= 0) {
            close(in->fd[i]);
            in->fd[i] = -1;
        }
    }
    in->ok = 0;
}

/* ---------------- 轮询 ---------------- */

/* 按键按下 = 电平为 "active_low ? 0 : 1"。
 * 这里统一成"1 = 按下"。 */
static int read_pressed(ga_input_t *in, int i) {
    int v = gpio_read_line(in->fd[i]);
    if (v < 0) return in->st[i].stable;   /* 读失败就用上次的值 */
    /* active_low 时: 读到 0 → 按下(1); 读到 1 → 松开(0) */
    return in->cfg_active_low ? (v == 0) : (v == 1);
}

ga_key_event_t ga_input_poll(ga_input_t *in) {
    ga_key_event_t ev;
    ev.type = GA_KEY_EV_NONE;
    ev.key = GA_KEY_NONE;
    if (!in || !in->ok) return ev;

    long long t = now_ms();
    const int db = in->cfg_debounce_ms;

    /* 固定从 UP 扫到 ENT, 保证同一时刻多个键按下时行为可预测 */
    for (int i = GA_KEY_UP; i <= GA_KEY_ENT; i++) {
        if (in->fd[i] < 0) continue;

        int raw = read_pressed(in, i);

        /* —— 消抖 ——
         * 原始值变了就记时间。只有持续稳定超过 db 毫秒, 才认它。 */
        if (raw != in->st[i].raw_last) {
            in->st[i].raw_last = raw;
            in->st[i].raw_change_ms = t;
            continue;   /* 还在抖, 本轮不产生事件 */
        }
        if ((t - in->st[i].raw_change_ms) < db) continue;  /* 还没稳够 */

        int prev_stable = in->st[i].stable;
        if (raw == prev_stable) {
            /* 稳定值没变。若处于"按住"状态, 处理长按与重复。 */
            if (prev_stable && in->st[i].press_ms > 0) {
                long long held = t - in->st[i].press_ms;
                if (!in->st[i].long_fired && held >= in->cfg_long_ms) {
                    in->st[i].long_fired = 1;
                    in->st[i].last_repeat_ms = t;
                    ev.type = GA_KEY_EV_LONG;
                    ev.key = (ga_key_t)i;
                    return ev;
                }
                if (in->st[i].long_fired &&
                    (t - in->st[i].last_repeat_ms) >= in->cfg_repeat_ms) {
                    in->st[i].last_repeat_ms = t;
                    ev.type = GA_KEY_EV_REPEAT;
                    ev.key = (ga_key_t)i;
                    return ev;
                }
            }
            continue;
        }

        /* —— 稳定值发生变化: 这是一个真实的按下或松开 —— */
        in->st[i].stable = raw;

        if (raw) {
            /* 按下 */
            in->st[i].press_ms = t;
            in->st[i].long_fired = 0;
            in->st[i].last_repeat_ms = t;
            ev.type = GA_KEY_EV_PRESS;
            ev.key = (ga_key_t)i;
            return ev;
        } else {
            /* 松开: 如果没触发过长按, 这就是一个"短按" */
            long long held = t - in->st[i].press_ms;
            in->st[i].press_ms = 0;
            if (!in->st[i].long_fired && held >= 0) {
                ev.type = GA_KEY_EV_SHORT;
                ev.key = (ga_key_t)i;
                return ev;
            }
            /* 长按后松开: 不产生短按事件(否则会误触发) */
        }
    }
    return ev;
}

/* ---------------- 左手 6 键(和弦一键直达) ---------------- */

int ga_leftkey_open(ga_leftkey_t *lk, const ga_leftkey_cfg_t *cfg) {
    if (!lk) return -1;
    memset(lk, 0, sizeof(*lk));

    int gpios[GA_LEFTKEY_COUNT];
    for (int i = 0; i < GA_LEFTKEY_COUNT; i++) gpios[i] = -1;
    int debounce = 20;
    /* ★ 默认 0 = 高电平有效, 对应 TTP223 触摸模块(松开 0 / 按下 1)。
     * 换成机械按键接地的场景, 显式传 active_low=1。 */
    int active_low = 0;

    if (cfg) {
        for (int i = 0; i < GA_LEFTKEY_COUNT; i++) gpios[i] = cfg->gpio[i];
        if (cfg->debounce_ms > 0) debounce = cfg->debounce_ms;
        active_low = cfg->active_low ? 1 : 0;
    }

    lk->debounce_ms = debounce;
    lk->active_low  = active_low;

    for (int i = 0; i < GA_LEFTKEY_COUNT; i++) {
        lk->fd[i] = -1;
        lk->stable[i] = 0;
        lk->raw_last[i] = -1;
        lk->raw_change_ms[i] = now_ms();
        lk->fired[i] = 0;
    }

    int opened = 0;
    for (int i = 0; i < GA_LEFTKEY_COUNT; i++) {
        if (gpios[i] < 0) continue;
        /* 同右手键: 不把极性交给内核(避免双重反转), 恒定传 0。 */
        int fd = gpio_open_line(gpios[i], 0);
        if (fd < 0) {
            fprintf(stderr, "[leftkey] GPIO %d (左手键%d) 打开失败: %s\n",
                    gpios[i], i + 1, strerror(errno));
            continue;
        }
        lk->fd[i] = fd;
        /* 读初值, 避免上电瞬间被当成"刚按下"(触摸模块上电可能有短脉冲) */
        int v = gpio_read_line(fd);
        int pressed = (v < 0) ? 0 : (active_low ? (v == 0) : (v == 1));
        lk->raw_last[i] = pressed;
        lk->stable[i]   = pressed;
        opened++;
    }

    lk->count = opened;
    lk->ok = (opened > 0);
    return lk->ok ? 0 : -1;
}

void ga_leftkey_close(ga_leftkey_t *lk) {
    if (!lk) return;
    for (int i = 0; i < GA_LEFTKEY_COUNT; i++) {
        if (lk->fd[i] >= 0) {
            close(lk->fd[i]);
            lk->fd[i] = -1;
        }
    }
    lk->ok = 0;
    lk->count = 0;
}

int ga_leftkey_poll(ga_leftkey_t *lk) {
    if (!lk || !lk->ok) return -1;

    long long t = now_ms();
    const int db = lk->debounce_ms;

    /* 固定 0..5 顺序扫描, 同一时刻多个键同时按下时行为可预测 */
    for (int i = 0; i < GA_LEFTKEY_COUNT; i++) {
        if (lk->fd[i] < 0) continue;

        int v = gpio_read_line(lk->fd[i]);
        if (v < 0) continue;   /* 读失败就跳过, 不改变状态 */
        int pressed = lk->active_low ? (v == 0) : (v == 1);

        if (pressed != lk->raw_last[i]) {
            lk->raw_last[i] = pressed;
            lk->raw_change_ms[i] = t;
            continue;          /* 还在抖, 本轮不判定 */
        }
        if ((t - lk->raw_change_ms[i]) < db) continue;   /* 没稳够 */

        if (lk->stable[i] == pressed) continue;          /* 稳定值没变 */

        lk->stable[i] = pressed;
        if (pressed) {
            if (!lk->fired[i]) {
                lk->fired[i] = 1;
                return i;      /* 按下沿 -> 上报一次 */
            }
        } else {
            lk->fired[i] = 0;  /* 松开 -> 重新武装 */
        }
    }
    return -1;
}

/* ---------------- 名字(调试用) ---------------- */

const char *ga_key_name(ga_key_t k) {
    switch (k) {
        case GA_KEY_UP:   return "Up";
        case GA_KEY_DOWN: return "Down";
        case GA_KEY_SET:  return "Set";
        case GA_KEY_ENT:  return "ENT";
        default:          return "NONE";
    }
}

const char *ga_key_event_name(ga_key_event_type_t t) {
    switch (t) {
        case GA_KEY_EV_PRESS:  return "按下";
        case GA_KEY_EV_SHORT:  return "短按";
        case GA_KEY_EV_LONG:   return "长按";
        case GA_KEY_EV_REPEAT: return "重复";
        default:               return "无";
    }
}
