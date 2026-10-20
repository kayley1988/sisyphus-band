/*
 * display_drv.c - SPI 小屏显示驱动实现
 */
#include "display_drv.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#include <sys/mman.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <linux/fb.h>

#ifdef GA_HAVE_SPI
#include <linux/spi/spidev.h>
#endif

/* ================= framebuffer 后端 ================= */

/* 探测 /dev/fb0 ~ /dev/fb5, 找到第一个可用的。
 * 注意: fb0 常是 HDMI 输出, SPI 小屏可能是 fb1 或 fb2。
 * 所以不能写死 fb0, 要遍历。
 *
 * 更精确的判别: 用 FBIOGET_VSCREENINFO 看分辨率,
 * 小屏的分辨率明显低于 HDMI(通常 240x240 / 320x240)。 */
static int fb_probe(ga_display_t *d, int want_w, int want_h) {
    char path[32];
    int best_fd = -1;
    int best_score = -1;
    struct fb_var_screeninfo best_vi;
    memset(&best_vi, 0, sizeof(best_vi));

    for (int i = 0; i < 6; i++) {
        snprintf(path, sizeof(path), "/dev/fb%d", i);
        int fd = open(path, O_RDWR);
        if (fd < 0) continue;

        struct fb_var_screeninfo vi;
        if (ioctl(fd, FBIOGET_VSCREENINFO, &vi) != 0) {
            close(fd);
            continue;
        }

        printf("[disp] %s: %ux%u %ubpp\n", path,
               vi.xres, vi.yres, vi.bits_per_pixel);

        /* 打分: 分辨率越接近期望值越好。
         * 这样能自动避开 HDMI 的 1920x1080。 */
        int score = 0;
        if ((int)vi.xres == want_w && (int)vi.yres == want_h) score = 100;
        else if ((int)vi.xres <= 480 && (int)vi.yres <= 480) score = 50;
        else score = 1;

        if (score > best_score) {
            if (best_fd >= 0) close(best_fd);
            best_fd = fd;
            best_score = score;
            best_vi = vi;
            d->fb_fd = fd;
            snprintf(path, sizeof(path), "/dev/fb%d", i);
        } else {
            close(fd);
        }
    }

    if (best_fd < 0) return -1;

    /* 读固定信息拿 line_length(fb 的一行可能因为对齐 > width*bpp) */
    struct fb_fix_screeninfo fi;
    if (ioctl(best_fd, FBIOGET_FSCREENINFO, &fi) != 0) {
        fprintf(stderr, "[disp] FBIOGET_FSCREENINFO 失败\n");
        close(best_fd);
        return -1;
    }

    d->fb_width    = (int)best_vi.xres;
    d->fb_height   = (int)best_vi.yres;
    d->fb_bpp      = (int)best_vi.bits_per_pixel;
    d->fb_line_len = (int)fi.line_length;
    d->fb_size     = fi.smem_len;
    d->width       = d->fb_width;
    d->height      = d->fb_height;

    /* 直接 mmap 显存。注意这是"立即生效"的刷新方式,
     * 没有双缓冲 —— 对小屏来说够用, 但可能有轻微撕裂。
     * 如果屏驱动支持 FBIOPAN_DISPLAY, 可以改成双缓冲消除撕裂。 */
    d->fb_mem = (uint8_t *)mmap(NULL, d->fb_size,
                                PROT_READ | PROT_WRITE, MAP_SHARED,
                                best_fd, 0);
    if (d->fb_mem == MAP_FAILED) {
        fprintf(stderr, "[disp] mmap 失败: %s\n", strerror(errno));
        d->fb_mem = NULL;
        close(best_fd);
        return -1;
    }

    d->backend = GA_DISP_BACKEND_FB;
    return 0;
}

/* ================= SPI 后端(框架) ================= */

#ifdef GA_HAVE_SPI
/* 通过 GPIO sysfs 写一个引脚。
 * 需要先 export: echo 25 > /sys/class/gpio/export
 *               echo out > /sys/class/gpio/gpio25/direction
 *
 * 这部分代码是框架, 具体引脚号和初始化序列必须按你的屏 datasheet 填。
 * 我在这里留了完整结构, 但没填具体值 —— 因为不同屏差异太大,
 * 瞎填反而误导。 */
static int gpio_export(int pin) {
    char buf[64];
    int fd = open("/sys/class/gpio/export", O_WRONLY);
    if (fd >= 0) {
        snprintf(buf, sizeof(buf), "%d", pin);
        ssize_t n = write(fd, buf, strlen(buf));
        (void)n;
        close(fd);
    }
    /* export 已存在时会报错, 不致命, 忽略 */

    snprintf(buf, sizeof(buf), "/sys/class/gpio/gpio%d/direction", pin);
    fd = open(buf, O_WRONLY);
    if (fd >= 0) {
        ssize_t n = write(fd, "out", 3);
        (void)n;
        close(fd);
    }
    snprintf(buf, sizeof(buf), "/sys/class/gpio/gpio%d/value", pin);
    return open(buf, O_WRONLY);
}

static int spi_probe(ga_display_t *d, int want_w, int want_h,
                     int spi_bus, int spi_cs, int dc_pin) {
    char path[32];
    snprintf(path, sizeof(path), "/dev/spidev%d.%d", spi_bus, spi_cs);

    int fd = open(path, O_RDWR);
    if (fd < 0) {
        fprintf(stderr, "[disp] 打不开 %s: %s\n", path, strerror(errno));
        return -1;
    }

    /* SPI 模式 0 和时钟频率。这两个值要按屏的 datasheet 填。
     * ST7789 通常支持 mode 0 + 40~80MHz。 */
    uint8_t mode = 0;
    uint32_t speed = 40000000;   /* 40MHz */
    uint8_t bits = 8;

    if (ioctl(fd, SPI_IOC_WR_MODE, &mode) < 0 ||
        ioctl(fd, SPI_IOC_WR_MAX_SPEED_HZ, &speed) < 0 ||
        ioctl(fd, SPI_IOC_WR_BITS_PER_WORD, &bits) < 0) {
        fprintf(stderr, "[disp] SPI ioctl 配置失败: %s\n", strerror(errno));
        fprintf(stderr, "[disp] 检查是否有权限访问 spi 设备(需要 root 或 spi 组)\n");
        close(fd);
        return -1;
    }

    d->spi_fd = fd;
    d->dc_gpio_fd = gpio_export(dc_pin);

    /* !! 这里需要插入屏幕初始化序列 !!
     *
     * 不同驱动的序列完全不同, 举例 ST7789 大致是:
     *   write_cmd(0x01); delay(150);        // software reset
     *   write_cmd(0x11); delay(120);        // sleep out
     *   write_cmd(0x3A); write_data(0x55);  // 16bit/pixel
     *   write_cmd(0x36); write_data(0x00);  // MADCTL: 方向
     *   write_cmd(0x21);                    // inversion on
     *   write_cmd(0x13);                    // normal display
     *   write_cmd(0x29);                    // display on
     *
     * 具体参数请查阅你的屏幕模组 datasheet 或厂家示例代码。
     * 我不在这里编造数值, 因为写错了会导致花屏且难排查。 */

    fprintf(stderr, "[disp] !! SPI 后端是框架代码, 需要填屏幕初始化序列 !!\n");
    fprintf(stderr, "[disp] 建议先检查 /dev/fb* 是否已由内核驱动创建\n");

    d->width  = want_w;
    d->height = want_h;
    d->backend = GA_DISP_BACKEND_SPI;
    return 0;
}
#endif

/* ================= 对外接口 ================= */

int ga_display_open(ga_display_t *d, int width, int height) {
    if (!d) return -1;
    memset(d, 0, sizeof(*d));
    d->fb_fd = -1;
    d->spi_fd = -1;
    d->dc_gpio_fd = -1;
    d->rst_gpio_fd = -1;

    /* 优先 framebuffer: 如果内核已经驱动了屏幕, 这是最省事也最快的路径 */
    if (fb_probe(d, width, height) == 0) {
        return 0;
    }

    fprintf(stderr, "[disp] 没有找到 framebuffer 设备。\n");
    fprintf(stderr, "[disp] 排查步骤:\n");
    fprintf(stderr, "  1. ls /dev/fb*        看有没有 framebuffer\n");
    fprintf(stderr, "  2. cat /proc/fb       看内核注册了哪些 fb\n");
    fprintf(stderr, "  3. dmesg | grep -i -E 'fb|spi|lcd|panel'\n");
    fprintf(stderr, "     看屏幕驱动是否加载成功\n");
    fprintf(stderr, "  4. 如果是 SPI 屏且没有 fb, 需要:\n");
    fprintf(stderr, "     - 设备树里加 panel 节点(推荐), 或\n");
    fprintf(stderr, "     - 用 -DGA_HAVE_SPI 编译并填初始化序列\n");
    return -1;
}

void ga_display_close(ga_display_t *d) {
    if (!d) return;
    if (d->fb_mem && d->fb_mem != MAP_FAILED) {
        munmap(d->fb_mem, d->fb_size);
        d->fb_mem = NULL;
    }
    if (d->fb_fd >= 0) { close(d->fb_fd); d->fb_fd = -1; }
    if (d->spi_fd >= 0) { close(d->spi_fd); d->spi_fd = -1; }
    if (d->dc_gpio_fd >= 0) { close(d->dc_gpio_fd); d->dc_gpio_fd = -1; }
    if (d->rst_gpio_fd >= 0) { close(d->rst_gpio_fd); d->rst_gpio_fd = -1; }
    d->backend = GA_DISP_BACKEND_NONE;
}

void ga_display_flush(ga_display_t *d, int x, int y, int w, int h,
                      const uint16_t *px_map) {
    if (!d || !px_map || w <= 0 || h <= 0) return;
    if (x < 0) { px_map -= (size_t)x; w += x; x = 0; }
    if (y < 0) { px_map -= (size_t)y * (size_t)w; h += y; y = 0; }
    if (x + w > d->width)  w = d->width - x;
    if (y + h > d->height) h = d->height - y;
    if (w <= 0 || h <= 0) return;

    switch (d->backend) {
    case GA_DISP_BACKEND_FB: {
        /* framebuffer 刷新: 按行拷贝。
         * 关键点: fb 的 line_length 可能大于 width*bpp(内存对齐),
         * 所以不能整块 memcpy, 必须逐行算偏移。 */
        if (d->fb_bpp == 16) {
            for (int row = 0; row < h; row++) {
                size_t dst_off = (size_t)(y + row) * (size_t)d->fb_line_len
                               + (size_t)x * 2;
                memcpy(d->fb_mem + dst_off,
                       px_map + (size_t)row * (size_t)w,
                       (size_t)w * 2);
            }
        } else if (d->fb_bpp == 32) {
            /* 32bpp 屏(XRGB8888): 需要把 RGB565 展开成 32 位。
             * 展开公式: R5->R8 用 (r<<3)|(r>>2) 保留亮度。 */
            uint32_t *dst = (uint32_t *)d->fb_mem;
            int stride32 = d->fb_line_len / 4;
            for (int row = 0; row < h; row++) {
                for (int col = 0; col < w; col++) {
                    uint16_t c = px_map[(size_t)row * (size_t)w + col];
                    uint32_t r = (c >> 11) & 0x1F;
                    uint32_t g = (c >> 5)  & 0x3F;
                    uint32_t b =  c        & 0x1F;
                    uint32_t r8 = (r << 3) | (r >> 2);
                    uint32_t g8 = (g << 2) | (g >> 4);
                    uint32_t b8 = (b << 3) | (b >> 2);
                    dst[(size_t)(y + row) * (size_t)stride32 + (x + col)] =
                        0xFF000000u | (r8 << 16) | (g8 << 8) | b8;
                }
            }
        }
        d->bytes_flushed += (uint64_t)w * (uint64_t)h * (uint64_t)(d->fb_bpp / 8);
        d->flush_count++;
        break;
    }
    case GA_DISP_BACKEND_SPI: {
#ifdef GA_HAVE_SPI
        /* SPI 刷新: 需要先设窗口(set address window), 再连续写像素。
         * 这里给出结构, 具体命令字按屏填。 */
        fprintf(stderr, "[disp] SPI 刷新未实现(需要填命令序列)\n");
#else
        (void)px_map;
#endif
        d->bytes_flushed += (uint64_t)w * (uint64_t)h * 2;
        d->flush_count++;
        break;
    }
    default:
        break;
    }
}

void ga_display_info(const ga_display_t *d) {
    if (!d) return;
    const char *name = "无";
    switch (d->backend) {
        case GA_DISP_BACKEND_FB:  name = "framebuffer"; break;
        case GA_DISP_BACKEND_SPI: name = "spidev";      break;
        case GA_DISP_BACKEND_SDL: name = "SDL 模拟";    break;
        default: break;
    }
    printf("[disp] 后端: %s\n", name);
    printf("[disp] 分辨率: %dx%d\n", d->width, d->height);
    if (d->backend == GA_DISP_BACKEND_FB) {
        printf("[disp] 色深: %d bpp, 行字节数: %d\n",
               d->fb_bpp, d->fb_line_len);
    }
    if (d->flush_count > 0) {
        printf("[disp] 已刷新 %llu 次, 累计 %llu 字节 (%.1f KB)\n",
               (unsigned long long)d->flush_count,
               (unsigned long long)d->bytes_flushed,
               (double)d->bytes_flushed / 1024.0);
    }
}
