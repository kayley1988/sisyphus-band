/*
 * st7789.h - ST7789 SPI 屏幕驱动 (用户态, 走 spidev)
 *
 * 适用: 1.54 寸 IPS 240x240 (你手上的这块), 也兼容 1.3/2.0 寸同 IC 模组
 *
 * 引脚对照(模组丝印 -> 接法):
 *   BLK  背光   -> 任意 GPIO 或直接接 3.3V(常亮); 接 PWM 可调亮度
 *   CS   片选   -> SPI CS(GPIO 片选或硬件 CS 均可)
 *   DC   数据/命令 -> 任意 GPIO (0=命令 1=数据) ★必须 GPIO, 不能省
 *   RES  复位   -> 任意 GPIO (也可不接, 上拉 10K, 但强烈建议接)
 *   SDA  MOSI   -> SPI MOSI
 *   SCL  SCLK   -> SPI CLK
 *   VCC  电源   -> 3.3V ★不要接 5V, ST7789 是 3.3V 逻辑
 *   GND  地     -> GND
 *
 * 没有 MISO, 屏幕只写不读 —— 这意味着无法读 ID 校验, 只能盲写。
 */
#ifndef GA_ST7789_H
#define GA_ST7789_H

#include "display_drv.h"

/* 打开并初始化屏幕。
 * spi_dev  如 "/dev/spidev1.0"
 * spi_hz   SPI 时钟, 建议 32000000~62500000, 先用 40MHz 稳妥
 * dc_pin / rst_pin / blk_pin  GPIO 编号(内核 gpiochip 编号)
 *          不接的引脚传 -1
 * x_off/y_off  显存偏移。1.54寸 240x240 通常 (0,0);
 *          若画面整体偏移, 1.3寸常见 y_off=0, 某些 2.0 寸是 (0,0)。
 *          显示位置不对就调这两个值, 不用改代码逻辑。 */
int  st7789_open(ga_display_t *d, const char *spi_dev, int spi_hz,
                 int dc_pin, int rst_pin, int blk_pin,
                 int x_off, int y_off);

/* 背光开关/亮度(0~100, 仅 blk 接了 PWM 时亮度有效, 否则只有开/关) */
void st7789_backlight(ga_display_t *d, int percent);

/* 休眠/唤醒(省电用) */
void st7789_sleep(ga_display_t *d, int on);

/* 刷屏(LVGL 的 flush_cb 接到这里)。
 * 注意: px_map 会被【就地修改】(字节序转换), LVGL 允许这么做。 */
void st7789_flush(ga_display_t *d, int x, int y, int w, int h,
                  uint16_t *px_map);

/* 关闭并释放 GPIO/SPI 资源 */
void st7789_close(ga_display_t *d);

#endif /* GA_ST7789_H */
