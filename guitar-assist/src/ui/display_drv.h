/*
 * display_drv.h - SPI 小屏显示驱动适配层
 *
 * 这一层的作用: 把 LVGL 和具体的屏幕硬件隔离开。
 * LVGL 只要求你实现一个 "flush_cb"(把指定区域的像素刷到屏幕),
 * 具体怎么刷(SPI/GPIO/framebuffer)由这一层决定。
 *
 * ============ 关键前置知识: SPI 屏怎么在 Linux 上驱动 ============
 *
 * 三种常见情况, 决定了这里怎么改:
 *
 * 【情况 A】屏幕有内核驱动, 暴露成 framebuffer(/dev/fb1)
 *   - 泰山派/Mainline 内核里如果带了 fbtft 或 st7789 驱动,
 *     插上屏就有 /dev/fbX
 *   - 检查: ls /dev/fb*  ; cat /proc/fb
 *   - 这是最省事的, 直接 mmap 写内存即可
 *   - 本文件的 fb_flush() 处理这种情况
 *
 * 【情况 B】屏幕没有驱动, 只能自己通过 SPI 发像素
 *   - 需要 spidev (/dev/spidevX.Y) + 一个 GPIO 做 DC 引脚(数据/命令选择)
 *   - 检查: ls /dev/spidev*
 *   - 需要自己写 ST7789/ILI9341 的初始化序列
 *   - 本文件的 spi_flush() 是框架, 具体初始化序列要按屏的 datasheet 填
 *
 * 【情况 C】屏幕厂提供了预编译的 .so 或示例代码
 *   - 直接把它的刷新函数接到 flush_cb 里, 最简单
 *
 * ============ 性能预期 ============
 *
 * SPI 屏的带宽是瓶颈, 你要有心理准备:
 *   - SPI 时钟常见 40~80 MHz
 *   - 240x240 x 16bit = 115KB 全屏数据
 *   - 在 40MHz SPI 上, 理论 23ms, 实际约 30~50ms
 *
 * 这意味着: 【全屏刷新约 30~50ms, 也就是 20~30 fps】。
 * 对调音界面够用(指针动 20fps 不卡), 但要注意:
 *   1. 尽量只刷新变化的区域(LVGL 的 flush_cb 天然就是局部刷新)
 *   2. 用 16bit RGB565 而不是 24bit, 带宽省 1/3
 *   3. 如果屏支持, 用 DMA 传送(内核驱动通常已开)
 */
#ifndef GA_DISPLAY_DRV_H
#define GA_DISPLAY_DRV_H

#include <stdint.h>
#include <stddef.h>

typedef enum {
    GA_DISP_BACKEND_NONE = 0,
    GA_DISP_BACKEND_FB,      /* /dev/fbX framebuffer */
    GA_DISP_BACKEND_SPI,     /* spidev + GPIO */
    GA_DISP_BACKEND_SDL,     /* PC 上模拟(方便开发调界面) */
} ga_disp_backend_t;

typedef struct {
    ga_disp_backend_t backend;

    /* —— framebuffer 后端 —— */
    int      fb_fd;
    uint8_t *fb_mem;         /* mmap 后的显存 */
    size_t   fb_size;
    int      fb_width;
    int      fb_height;
    int      fb_line_len;    /* 一行字节数, 注意可能 > width*bpp */
    int      fb_bpp;         /* 16(RGB565) / 32(ARGB8888) */

    /* —— SPI 后端 —— */
    int      spi_fd;
    int      dc_gpio_fd;     /* data/command 选择引脚 */
    int      rst_gpio_fd;    /* reset 引脚 */

    /* 屏幕参数(LVGL 需要知道) */
    int      width;
    int      height;

    /* 统计(调试用): SPI 屏带宽有限, 监控实际刷新量很有用 */
    uint64_t bytes_flushed;
    uint64_t flush_count;
} ga_display_t;

/* 自动探测并打开显示设备。
 * 探测顺序: framebuffer -> spidev -> 失败
 * 传入的 width/height 是期望分辨率(和实际不符时不报错, 以实际为准)。 */
int  ga_display_open(ga_display_t *d, int width, int height);

/* 打开 SDL 模拟窗口(仅 PC, 需要编译时 -DGA_HAVE_SDL) */
int  ga_display_open_sdl(ga_display_t *d, int width, int height);

void ga_display_close(ga_display_t *d);

/* 刷新一块矩形区域。这是给 LVGL 的 flush_cb 调用的核心函数。
 * px_map 是紧凑排列的像素, 每像素 2 字节(RGB565, little-endian)。
 * 注意: LVGL 可能分多次调用(它把屏幕切成若干块), 每次都要正确处理。 */
void ga_display_flush(ga_display_t *d, int x, int y, int w, int h,
                      const uint16_t *px_map);

/* 色深转换: RGB565 的内部字节序不同屏可能不一样。
 * 有些屏要求大端, 有些小端。如果显示颜色是"红蓝互换", 改这里。 */
static inline uint16_t ga_swap_rb(uint16_t c) {
    return (uint16_t)(((c & 0x1F) << 11) | (c & 0x07E0) | ((c & 0xF800) >> 11));
}

/* 打印当前后端信息, 供启动日志用 */
void ga_display_info(const ga_display_t *d);

#endif /* GA_DISPLAY_DRV_H */
