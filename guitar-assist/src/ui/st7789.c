/*
 * st7789.c - ST7789 用户态 SPI 驱动实现
 *
 * 数据手册要点(ST7789V):
 *   - SPI 模式: 支持 mode 0 和 mode 3, 本驱动用 mode 0
 *   - 写时序: CS 拉低期间, DC=0 发命令字节, DC=1 发数据字节
 *   - 16bit 色: 每像素 2 字节, 高字节在前(RRRRRGGG GGGBBBBB, 大端发送)
 *   - 显存窗口: 0x2A(列地址) + 0x2B(行地址) + 0x2C(写 RAM)
 *
 * 关键实现细节(踩坑经验):
 *   1. SLPOUT 之后必须 delay >= 120ms 再发后续命令, 否则会初始化失败
 *   2. IPS 屏必须开反色(INVON), 否则黑白颠倒 —— 这块 1.54 寸 IPS 就要开
 *   3. RGB565 字节序: ST7789 要求大端, 而 Linux/LVGL 内部是小端,
 *      所以发送前要交换字节。如果颜色怪异(红蓝互换/花屏), 先查这里
 *   4. SPI 单次传输不是越大越好, 内核 spidev 每次 write 会整体走
 *      buf_size 上限(默认 4096, 设备树可调大), 分块发送最稳
 */
#include "st7789.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <sys/ioctl.h>
#include <linux/spi/spidev.h>
#include <linux/gpio.h>

/* ---------------- 命令定义(挑用到的) ---------------- */
#define ST_SWRESET  0x01   /* 软复位, 需 150ms */
#define ST_SLPOUT   0x11   /* 退出休眠, 需 120ms */
#define ST_NORON    0x13   /* 正常显示模式 */
#define ST_INVOFF   0x20
#define ST_INVON    0x21   /* 反色开(IPS 屏必须) */
#define ST_DISPOFF  0x28
#define ST_DISPON   0x29   /* 开显示, 需 50ms */
#define ST_CASET    0x2A   /* 列地址窗口 */
#define ST_RASET    0x2B   /* 行地址窗口 */
#define ST_RAMWR    0x2C   /* 写显存 */
#define ST_MADCTL   0x36   /* 显存访问控制(方向/RGB顺序) */
#define ST_COLMOD   0x3A   /* 像素格式 */
#define ST_PORCTRL  0xB2
#define ST_GCTRL    0xB7
#define ST_VCOMS    0xBB
#define ST_LCMCTRL  0xC0
#define ST_VDVVRHEN 0xC2
#define ST_VRHS     0xC3
#define ST_VDVS     0xC4
#define ST_FRCTRL2  0xC6
#define ST_PWCTRL1  0xD0
#define ST_GAMMA1   0xE0
#define ST_GAMMA2   0xE1

/* MADCTL 各位: MY MX MV ML RGB MH x x
 * 默认 0x00: 正常方向, RGB 顺序。
 * 如果装屏方向不对, 改这个值:
 *   0x00 竖屏(USB 在下)   0x60 横屏(旋转90°)
 *   0xC0 竖屏倒装         0xA0 横屏(旋转270°)
 *   |0x08 则红蓝互换(某些屏需要) */
#define ST_MADCTL_DEFAULT  0x00

/* ---------------- GPIO(用 libgpiod 的字符设备接口) ----------------
 * 内核 >= 4.8 推荐 /dev/gpiochipN 字符设备, sysfs 已废弃。
 * 这里直接用 ioctl, 不依赖 libgpiod 库, 交叉编译少一个依赖。 */

typedef struct {
    int chip_fd;
    int line_num;
    struct gpiohandle_request req;
    int valid;
} ga_gpio_t;

/* 找到某个全局 gpio 编号所在的 chip 并申请为输出。
 * 全局编号到 chip/line 的映射: 依次累加每个 chip 的 ngpio。
 * 例如 chip0 有 32 个脚(0-31), chip1 从 32 开始。 */
static int gpio_open_output(int global_num, ga_gpio_t *out) {
    memset(out, 0, sizeof(*out));
    out->valid = 0;
    if (global_num < 0) return -1;

    /* RK3566 通常 gpiochip0/1/2/3/4, 每颗 32 脚。
     * 遍历 gpiochip0..7, 按累加 ngpio 定位。 */
    int base = 0;
    for (int c = 0; c < 8; c++) {
        char path[32];
        snprintf(path, sizeof(path), "/dev/gpiochip%d", c);

        int fd = open(path, O_RDWR);
        if (fd < 0) continue;

        struct gpiochip_info info;
        memset(&info, 0, sizeof(info));
        if (ioctl(fd, GPIO_GET_CHIPINFO_IOCTL, &info) != 0) {
            close(fd);
            continue;
        }

        if (global_num < base + (int)info.lines) {
            memset(&out->req, 0, sizeof(out->req));
            out->req.lineoffsets[0] = (unsigned int)(global_num - base);
            out->req.lines = 1;
            out->req.flags = GPIOHANDLE_REQUEST_OUTPUT;
            /* 默认拉高(背光/复位无效电平), 避免初始化时误触发 */
            out->req.default_values[0] = 1;
            if (ioctl(fd, GPIO_GET_LINEHANDLE_IOCTL, &out->req) != 0) {
                fprintf(stderr, "[st7789] 申请 GPIO%d 失败: %s\n",
                        global_num, strerror(errno));
                close(fd);
                return -1;
            }
            close(fd);   /* 句柄已通过 req.fd 拿到, chip 句柄可关 */
            out->chip_fd = out->req.fd;
            out->line_num = global_num;
            out->valid = 1;
            return 0;
        }
        base += (int)info.lines;
        close(fd);
    }

    fprintf(stderr, "[st7789] 找不到 GPIO%d, 检查 gpioinfo 确认编号\n",
            global_num);
    return -1;
}

static void gpio_set(const ga_gpio_t *g, int value) {
    if (!g || !g->valid) return;
    struct gpiohandle_data dat;
    memset(&dat, 0, sizeof(dat));
    dat.values[0] = (unsigned char)(value ? 1 : 0);
    ioctl(g->req.fd, GPIOHANDLE_SET_LINE_VALUES_IOCTL, &dat);
}

static void gpio_close(ga_gpio_t *g) {
    if (g && g->valid && g->req.fd >= 0) close(g->req.fd);
    g->valid = 0;
}

/* ---------------- SPI 基础操作 ---------------- */

/* 命令/数据发送: DC 引脚区分命令(0)和数据(1)。
 * 注意 SPI 是全双工, spidev 用 half-duplex write 就行(没有 MISO)。 */
static ga_gpio_t g_dc, g_rst, g_blk;

/* 显存偏移: 240x240 屏的显存实际是 240x320, 竖向有 80 行冗余,
 * 靠偏移把画面挪到可视区。1.54 寸通常 (0,0), 位置不对就调这两个。 */
static int st_x_off = 0;
static int st_y_off = 0;

static void st_send(ga_display_t *d, const uint8_t *data, size_t len, int is_data) {
    gpio_set(&g_dc, is_data);
    /* SPI 传输。没有 CS 控制: 让内核 spidev 的 CS 自动片选
     * (每次 write 期间 CS 自动拉低, 结束拉高), 命令和数据
     * 分两次 write 发送, CS 会在中间短暂拉高 —— 这对 ST7789
     * 是安全的, 因为它按字节解析, 不要求 CS 连续覆盖整帧。 */
    size_t off = 0;
    while (off < len) {
        size_t chunk = len - off;
        if (chunk > 4096) chunk = 4096;
        ssize_t n = write(d->spi_fd, data + off, chunk);
        if (n < 0) {
            fprintf(stderr, "[st7789] SPI write 失败: %s\n", strerror(errno));
            return;
        }
        off += (size_t)n;
    }
}

static void st_cmd(ga_display_t *d, uint8_t cmd,
                   const uint8_t *data, size_t len) {
    gpio_set(&g_dc, 0);
    ssize_t n = write(d->spi_fd, &cmd, 1);
    (void)n;
    if (len > 0 && data) {
        st_send(d, data, len, 1);
    }
    /* DC 置回数据态, 方便后续连续像素发送 */
    gpio_set(&g_dc, 1);
}

/* ---------------- 初始化序列 ---------------- */

/* 1.54 寸 IPS 240x240 的标准初始化。
 * 来源: ST7789V datasheet + 常见开源驱动(Adafruit/Waveshare)交叉验证。
 * 逐条注明用途, 调试花屏时按顺序排查。 */
static void st7789_init_sequence(ga_display_t *d) {
    /* 复位脉冲: RES 拉低 10ms -> 拉高 -> 等 120ms */
    gpio_set(&g_rst, 1); usleep(10000);
    gpio_set(&g_rst, 0); usleep(10000);   /* 低 > 10us 即可, 10ms 保险 */
    gpio_set(&g_rst, 1); usleep(120000);

    st_cmd(d, ST_SWRESET, NULL, 0);
    usleep(150000);

    st_cmd(d, ST_SLPOUT, NULL, 0);        /* 退出休眠 */
    usleep(120000);

    /* 像素格式: 0x55 = 16bit/像素 (RGB565) */
    { uint8_t v = 0x55; st_cmd(d, ST_COLMOD, &v, 1); }

    /* 显存方向。MadCTL 值影响旋转方向, 见文件顶部注释 */
    { uint8_t v = ST_MADCTL_DEFAULT; st_cmd(d, ST_MADCTL, &v, 1); }

    /* IPS 屏必须反色, 否则黑白颠倒(白底变黑底)。
     * 如果你的屏显示反了, 注释掉这条试试。 */
    st_cmd(d, ST_INVON, NULL, 0);

    /* --- 以下为画质调优参数, 来自常见可用的 1.54寸 模组配置 ---
     * 一般不用改。花屏/条纹时先怀疑接线, 而不是这些值。 */
    {   /* Porch: 前后肩, 影响时序稳定性 */
        uint8_t p[5] = { 0x0C, 0x0C, 0x00, 0x33, 0x33 };
        st_cmd(d, ST_PORCTRL, p, 5);
    }
    {   /* Gate 控制: VGH=13.26V VGL=-10.43V */
        uint8_t v = 0x35; st_cmd(d, ST_GCTRL, &v, 1);
    }
    {   /* VCOM: 0x19 ≈ 0.9V, 影响对比度/闪烁 */
        uint8_t v = 0x19; st_cmd(d, ST_VCOMS, &v, 1);
    }
    {   uint8_t v = 0x21; st_cmd(d, ST_LCMCTRL, &v, 1); }
    {   /* VDV/VRH 使能 */
        uint8_t v = 0x01; st_cmd(d, ST_VDVVRHEN, &v, 1);
    }
    {   uint8_t v = 0x12; st_cmd(d, ST_VRHS, &v, 1); }   /* VRH = 4.45V */
    {   uint8_t v = 0x20; st_cmd(d, ST_VDVS, &v, 1); }
    {   /* 帧率: 0x0F = 60Hz */
        uint8_t v = 0x0F; st_cmd(d, ST_FRCTRL2, &v, 1);
    }
    {   /* 电源控制: AVDD=6.8V AVCL=-4.8V VDS=2.3V */
        uint8_t p[2] = { 0xA4, 0xA1 };
        st_cmd(d, ST_PWCTRL1, p, 2);
    }
    {   /* Gamma 正极性(灰阶曲线, 影响色渐变平滑度) */
        static const uint8_t g1[14] = {
            0xD0, 0x04, 0x0D, 0x11, 0x13, 0x2B, 0x3F,
            0x54, 0x4C, 0x18, 0x0D, 0x0B, 0x1F, 0x23
        };
        st_cmd(d, ST_GAMMA1, g1, 14);
    }
    {   /* Gamma 负极性(与正极性对称) */
        static const uint8_t g2[14] = {
            0xD0, 0x04, 0x0C, 0x11, 0x13, 0x2C, 0x3F,
            0x44, 0x51, 0x2F, 0x1F, 0x1F, 0x20, 0x23
        };
        st_cmd(d, ST_GAMMA2, g2, 14);
    }

    st_cmd(d, ST_NORON, NULL, 0);        /* 正常显示模式 */
    usleep(10000);
    st_cmd(d, ST_DISPON, NULL, 0);       /* 开显示 */
    usleep(50000);
}

/* ---------------- 窗口设置 + 刷屏 ---------------- */

static void st_set_window(ga_display_t *d, int x, int y, int w, int h) {
    uint8_t b[4];
    int x0 = x + st_x_off, x1 = x + w - 1 + st_x_off;
    int y0 = y + st_y_off, y1 = y + h - 1 + st_y_off;

    b[0] = (uint8_t)(x0 >> 8); b[1] = (uint8_t)(x0 & 0xFF);
    b[2] = (uint8_t)(x1 >> 8); b[3] = (uint8_t)(x1 & 0xFF);
    st_cmd(d, ST_CASET, b, 4);

    b[0] = (uint8_t)(y0 >> 8); b[1] = (uint8_t)(y0 & 0xFF);
    b[2] = (uint8_t)(y1 >> 8); b[3] = (uint8_t)(y1 & 0xFF);
    st_cmd(d, ST_RASET, b, 4);

    st_cmd(d, ST_RAMWR, NULL, 0);   /* 后续字节全部写入显存 */
}

/* RGB565 小端 -> 大端(屏要求高字节在前)。
 * 就地转换, 速度很快, 240x240 全屏约 57600 次 swap, A55 上 < 1ms。 */
static void swap_bytes(uint16_t *px, size_t n) {
    for (size_t i = 0; i < n; i++) {
        uint16_t v = px[i];
        px[i] = (uint16_t)((v >> 8) | (v << 8));
    }
}

/* ---------------- 对外接口 ---------------- */

int st7789_open(ga_display_t *d, const char *spi_dev, int spi_hz,
                int dc_pin, int rst_pin, int blk_pin,
                int x_off, int y_off) {
    if (!d || !spi_dev) return -1;

    memset(d, 0, sizeof(*d));
    d->spi_fd = -1;

    int fd = open(spi_dev, O_RDWR);
    if (fd < 0) {
        fprintf(stderr, "[st7789] 打不开 %s: %s\n", spi_dev, strerror(errno));
        fprintf(stderr, "[st7789] 排查: 1) 设备树是否使能 spidev\n"
                        "               2) ls -l /dev/spidev* 看有哪些\n"
                        "               3) 权限: 是否 root 或 dialout 组\n");
        return -1;
    }
    d->spi_fd = fd;

    uint8_t mode = SPI_MODE_0;
    uint8_t bits = 8;
    uint32_t speed = (spi_hz > 0) ? (uint32_t)spi_hz : 40000000;
    if (ioctl(fd, SPI_IOC_WR_MODE, &mode) < 0 ||
        ioctl(fd, SPI_IOC_WR_BITS_PER_WORD, &bits) < 0 ||
        ioctl(fd, SPI_IOC_WR_MAX_SPEED_HZ, &speed) < 0) {
        fprintf(stderr, "[st7789] SPI 配置失败: %s\n", strerror(errno));
        close(fd);
        return -1;
    }

    /* 申请 GPIO: DC 必须有, RST/BLK 可选 */
    if (dc_pin >= 0) {
        if (gpio_open_output(dc_pin, &g_dc) != 0) {
            fprintf(stderr, "[st7789] DC 引脚 GPIO%d 申请失败(必须)\n", dc_pin);
            close(fd);
            return -1;
        }
    } else {
        fprintf(stderr, "[st7789] 错误: DC 引脚必须指定, 否则无法区分命令/数据\n");
        close(fd);
        return -1;
    }
    if (rst_pin >= 0) gpio_open_output(rst_pin, &g_rst);
    if (blk_pin >= 0) {
        gpio_open_output(blk_pin, &g_blk);
        gpio_set(&g_blk, 1);   /* 默认开背光 */
    }

    st_x_off = x_off;
    st_y_off = y_off;

    d->width  = 240;
    d->height = 240;
    d->backend = GA_DISP_BACKEND_SPI;

    st7789_init_sequence(d);

    printf("[st7789] 初始化完成: %s @ %u Hz, DC=GPIO%d RST=GPIO%d BLK=GPIO%d"
           " 偏移(%d,%d)\n",
           spi_dev, speed, dc_pin, rst_pin, blk_pin, x_off, y_off);
    return 0;
}

void st7789_backlight(ga_display_t *d, int percent) {
    (void)d;
    if (!g_blk.valid) return;
    gpio_set(&g_blk, percent > 0);
    /* 注意: 普通 GPIO 只能开关。要调亮度需把 BLK 接到 PWM 引脚,
     * 然后走 /sys/class/pwm/ 或硬件 PWM 接口 —— 需要时再加。 */
}

void st7789_sleep(ga_display_t *d, int on) {
    if (on) {
        st_cmd(d, ST_DISPOFF, NULL, 0);
        usleep(20000);
        st_cmd(d, 0x10 /* SLPIN */, NULL, 0);
        usleep(120000);
    } else {
        st_cmd(d, ST_SLPOUT, NULL, 0);
        usleep(120000);
        st_cmd(d, ST_DISPON, NULL, 0);
        usleep(50000);
    }
}

/* 提供给 display_drv.c 的刷屏实现。
 * LVGL 每次给一块矩形, 这里: 设窗口 -> 字节序转换 -> 连续写像素。 */
void st7789_flush(ga_display_t *d, int x, int y, int w, int h,
                  uint16_t *px_map /* 会就地修改! */) {
    if (!d || d->spi_fd < 0 || !px_map) return;

    st_set_window(d, x, y, w, h);

    size_t n = (size_t)w * (size_t)h;
    swap_bytes(px_map, n);

    /* 分块发送, 每块 <= 16KB(spidev 单次 write 的安全上限,
     * 内核默认 bufsiz 是 4096, 但一次 write 多次循环发送没问题) */
    st_send(d, (const uint8_t *)px_map, n * 2, 1);

    d->bytes_flushed += (uint64_t)(n * 2);
    d->flush_count++;
}

/* 释放 GPIO 资源。调用方关闭显示时应调用本函数,
 * 否则 GPIO 线会一直占用(下次启动可能申请失败)。 */
void st7789_close(ga_display_t *d) {
    if (d && d->spi_fd >= 0) {
        close(d->spi_fd);
        d->spi_fd = -1;
    }
    gpio_close(&g_dc);
    gpio_close(&g_rst);
    gpio_close(&g_blk);
}
