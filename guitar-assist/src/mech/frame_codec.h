/*
 * frame_codec.h - 与档3(《拨云》)ESP32 通信的 V3 二进制帧协议 (C 版)
 *
 * ★ 本文件的每一处字节布局都与《拨云》上位机的
 *   src/guitarist/frame_codec.py + frame_types.py **逐字节一致**。
 *   一致性由 scripts/verify_boyun_parity.py 对拍保证(22/22),
 *   改这里之前先跑那个脚本。
 *
 * 帧格式(wire):
 *   SYNC0(1B=0xAA) SYNC1(1B=0x55) LENGTH(1B) TYPE(1B) PAYLOAD(0~247B) CRC16(2B, 大端)
 *   - LENGTH = PAYLOAD 字节数(不含头/CRC)
 *   - CRC16-CCITT: poly=0x1021, init=0xFFFF, 覆盖 TYPE + PAYLOAD, **大端输出**
 *   - UART: 921600 8N1
 *
 * TYPE 编码: 高 4bit = 模式域, 低 4bit = 子类型
 *   0x0=NORMAL 0x1=PLAY 0x2=OTA 0xF=CONTROL(跨模式)
 *
 * 本模块只做"帧的构造", 不碰串口; 串口在 mech_driver.c。
 */
#ifndef GA_MECH_FRAME_CODEC_H
#define GA_MECH_FRAME_CODEC_H

#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ── 协议常量 ── */
#define GA_FRAME_SYNC0        0xAA
#define GA_FRAME_SYNC1        0x55
#define GA_FRAME_MAX_PAYLOAD  247
#define GA_CRC16_POLY         0x1021

/* 帧类型 (与 frame_types.py:FrameType 一致, 仅列本项目用到的) */
typedef enum {
    GA_FT_NORMAL_SERVO_BATCH  = 0x02,  /* HOST→ESP32: 18 通道角度批量 */
    GA_FT_NORMAL_SERVO_SINGLE = 0x03,  /* HOST→ESP32: 单通道角度 */
    GA_FT_NORMAL_CHORD_DEF    = 0x06,  /* HOST→ESP32: 和弦定义(名称+品位) */
    GA_FT_NORMAL_CONFIG_SAVE  = 0x07,  /* HOST→ESP32: 触发 NVS 保存校准 */
    GA_FT_NORMAL_SERVO_CTRL   = 0x08,  /* HOST→ESP32: 舵机电源控制 */
    GA_FT_CTRL_MODE_SWITCH    = 0xF0,  /* HOST→ESP32: 模式切换请求 */
} ga_frame_type_t;

/* SERVO_SINGLE 的通道类型 (frame_types.py:ServoChType) */
typedef enum {
    GA_SERVO_CH_PCA = 0,
    GA_SERVO_CH_PWM = 1,
} ga_servo_ch_t;

/* SERVO_CTRL 子命令 (frame_types.py:ServoCtrlType) */
typedef enum {
    GA_SERVO_CTRL_ENABLE         = 0x01,
    GA_SERVO_CTRL_DISABLE        = 0x02,
    GA_SERVO_CTRL_RESET_AND_IDLE = 0x03,
} ga_servo_ctrl_t;

/* ESP32 运行模式 (frame_types.py:EspMode) */
typedef enum {
    GA_ESP_MODE_NORMAL = 0,
    GA_ESP_MODE_PLAY   = 1,
    GA_ESP_MODE_OTA    = 2,
} ga_esp_mode_t;

/* 单帧最大字节数 (4 头 + 247 载荷 + 2 CRC) = 253 */
#define GA_FRAME_MAX_TOTAL (4 + GA_FRAME_MAX_PAYLOAD + 2)

/* ── CRC16-CCITT (poly 0x1021, init 0xFFFF) ──
 * 与 ESP32 固件 frame_protocol.cpp / 拨云 frame_codec.crc16 完全一致。 */
uint16_t ga_crc16(const uint8_t *data, size_t len);

/* ── 通用帧构造 ──
 * 把 TYPE + payload 打包成完整帧写入 out。返回写入字节数;
 * 载荷超长或缓冲不足返回 0。 */
size_t ga_frame_build(uint8_t type, const uint8_t *payload, size_t plen,
                      uint8_t *out, size_t outcap);

/* ── 便捷工厂 (与 frame_codec.py 的 make_* 一一对应) ── */

/* 18 路角度 -> NORMAL_SERVO_BATCH。
 * angles 顺序 = [和弦0..11, 拨弦0..5] (物理通道映射由 ESP32 负责, 主机不管)。
 * 每个角度会被钳位到 0~180。 */
size_t ga_make_servo_batch(const int angles[18], uint8_t *out, size_t outcap);

/* 单通道 -> NORMAL_SERVO_SINGLE。ch_index: PCA 1~16 / PWM 0~1。 */
size_t ga_make_servo_single(ga_servo_ch_t ch_type, int ch_index, int angle,
                            uint8_t *out, size_t outcap);

/* 舵机电源控制 -> NORMAL_SERVO_CTRL */
size_t ga_make_servo_ctrl(ga_servo_ctrl_t cmd, uint8_t *out, size_t outcap);

/* 模式切换 -> CTRL_MODE_SWITCH (payload = [mode, 0]) */
size_t ga_make_mode_switch(ga_esp_mode_t mode, uint8_t *out, size_t outcap);

/* 和弦定义 -> NORMAL_CHORD_DEF (name 最多 15 字节 + \0 补齐到 16; frets 6 个)。 */
size_t ga_make_chord_def(const char *name, const int frets[6],
                         uint8_t *out, size_t outcap);

/* 触发配置持久化 -> NORMAL_CONFIG_SAVE (空载荷) */
size_t ga_make_config_save(uint8_t *out, size_t outcap);

#ifdef __cplusplus
}
#endif
#endif /* GA_MECH_FRAME_CODEC_H */
