/*
 * frame_codec.c - V3 二进制帧编解码 (与《拨云》frame_codec.py 逐字节一致)
 *
 * 一致性由 scripts/verify_boyun_parity.py 对拍保证。改这里先跑对拍。
 */
#include "frame_codec.h"

#include <string.h>

/* ════════════════════════════════════════════════
 * CRC16-CCITT 查表法
 *
 * init = 0xFFFF, poly = 0x1021, 无输出反转, 大端输出。
 * 与 Python 端一致的关键: 每次移位都要 & 0xFFFF 模拟 uint16_t 截断。
 * ════════════════════════════════════════════════ */

static uint16_t s_crc_tab[256];
static int      s_crc_ready = 0;

static void crc16_build_table(void)
{
    for (int i = 0; i < 256; i++) {
        uint16_t crc = (uint16_t)(i << 8);
        for (int b = 0; b < 8; b++) {
            if (crc & 0x8000) {
                crc = (uint16_t)(((crc << 1) ^ GA_CRC16_POLY) & 0xFFFF);
            } else {
                crc = (uint16_t)((crc << 1) & 0xFFFF);
            }
        }
        s_crc_tab[i] = crc;
    }
    s_crc_ready = 1;
}

uint16_t ga_crc16(const uint8_t *data, size_t len)
{
    if (!s_crc_ready) {
        crc16_build_table();
    }
    uint16_t crc = 0xFFFF;
    for (size_t i = 0; i < len; i++) {
        crc = (uint16_t)(((crc << 8) ^ s_crc_tab[((crc >> 8) ^ data[i]) & 0xFF]) & 0xFFFF);
    }
    return crc;
}

/* ════════════════════════════════════════════════
 * 帧构造
 * ════════════════════════════════════════════════ */

size_t ga_frame_build(uint8_t type, const uint8_t *payload, size_t plen,
                      uint8_t *out, size_t outcap)
{
    if (!out || plen > GA_FRAME_MAX_PAYLOAD) {
        return 0;
    }
    size_t total = 4 + plen + 2;
    if (outcap < total) {
        return 0;
    }

    out[0] = GA_FRAME_SYNC0;
    out[1] = GA_FRAME_SYNC1;
    out[2] = (uint8_t)plen;
    out[3] = type;
    if (plen && payload) {
        memcpy(out + 4, payload, plen);
    }

    /* CRC 覆盖 TYPE + PAYLOAD —— 直接把 out[3..3+plen] 喂进去, 少一次拷贝。
     * out[3]=TYPE, 紧接着是 payload, 正好连续。 */
    uint16_t crc = ga_crc16(out + 3, plen + 1);
    out[4 + plen]     = (uint8_t)(crc >> 8);   /* 大端: 高字节在前 */
    out[4 + plen + 1] = (uint8_t)(crc & 0xFF);
    return total;
}

static uint8_t clamp_ang(int a)
{
    if (a < 0)   return 0;
    if (a > 180) return 180;
    return (uint8_t)a;
}

size_t ga_make_servo_batch(const int angles[18], uint8_t *out, size_t outcap)
{
    uint8_t payload[18];
    for (int i = 0; i < 18; i++) {
        payload[i] = clamp_ang(angles[i]);
    }
    return ga_frame_build((uint8_t)GA_FT_NORMAL_SERVO_BATCH, payload, 18, out, outcap);
}

size_t ga_make_servo_single(ga_servo_ch_t ch_type, int ch_index, int angle,
                            uint8_t *out, size_t outcap)
{
    /* struct.pack('BBB', ch_type, ch_index, angle) */
    uint8_t payload[3];
    payload[0] = (uint8_t)((int)ch_type & 0xFF);
    payload[1] = (uint8_t)(ch_index & 0xFF);
    payload[2] = clamp_ang(angle);
    return ga_frame_build((uint8_t)GA_FT_NORMAL_SERVO_SINGLE, payload, 3, out, outcap);
}

size_t ga_make_servo_ctrl(ga_servo_ctrl_t cmd, uint8_t *out, size_t outcap)
{
    uint8_t payload[1] = { (uint8_t)((int)cmd & 0xFF) };
    return ga_frame_build((uint8_t)GA_FT_NORMAL_SERVO_CTRL, payload, 1, out, outcap);
}

size_t ga_make_mode_switch(ga_esp_mode_t mode, uint8_t *out, size_t outcap)
{
    uint8_t payload[2] = { (uint8_t)((int)mode & 0xFF), 0x00 };
    return ga_frame_build((uint8_t)GA_FT_CTRL_MODE_SWITCH, payload, 2, out, outcap);
}

size_t ga_make_chord_def(const char *name, const int frets[6],
                         uint8_t *out, size_t outcap)
{
    /* name.encode('utf-8')[:16].ljust(16, b'\0') + frets(6) */
    uint8_t payload[22];
    memset(payload, 0, sizeof(payload));

    if (name) {
        size_t n = strlen(name);          /* UTF-8 字符串: strlen 即字节数 */
        if (n > 16) n = 16;
        memcpy(payload, name, n);
        /* 若正好取到多字节字符中间被截断, 不额外处理(与原实现一致: 原实现也只切字节) */
    }
    for (int i = 0; i < 6; i++) {
        int f = frets ? frets[i] : 0;
        if (f < -128) f = -128;
        if (f > 127)  f = 127;
        payload[16 + i] = (uint8_t)(f & 0xFF);   /* 补码, 与 bytes(...&0xFF) 一致 */
    }
    return ga_frame_build((uint8_t)GA_FT_NORMAL_CHORD_DEF, payload, 22, out, outcap);
}

size_t ga_make_config_save(uint8_t *out, size_t outcap)
{
    return ga_frame_build((uint8_t)GA_FT_NORMAL_CONFIG_SAVE, NULL, 0, out, outcap);
}
