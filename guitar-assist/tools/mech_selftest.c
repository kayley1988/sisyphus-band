/*
 * mech_selftest.c - 机构驱动层自检 (板端可跑, 不需要屏/触摸键)
 *
 * 用途: 上板第一步, 在没有任何按键/屏幕的情况下, 验证
 *   [1] 角度计算正确 (与《拨云》已知答案对拍)
 *   [2] 帧字节正确 (AA 55 ... / CRC16)
 *   [3] 串口真能发出去 (往 /dev/ttyS3 写一帧 SERVO_CTRL + 一帧 BATCH)
 *
 * 用法:
 *   ./mech_selftest                # 只做 [1][2], 不碰硬件
 *   ./mech_selftest /dev/ttyS3     # 额外做 [3], 真发一帧
 *   ./mech_selftest /dev/ttyS3 cfg # 从 config/servo_config.csv 载入参数(路径见下)
 *
 * 退出码 0 = 全过。
 */
#include "mech_driver.h"
#include "chord_angles.h"
#include "frame_codec.h"

#include <stdio.h>
#include <string.h>

static int g_fail = 0;

static void check(const char *name, int ok)
{
    printf("  [%s] %s\n", ok ? "OK" : "XX", name);
    if (!ok) g_fail++;
}

static void print_hex(const uint8_t *b, size_t n)
{
    for (size_t i = 0; i < n; i++) printf("%02X ", b[i]);
    printf("\n");
}

/* 已知答案: 出厂默认参数下, C 和弦 [0,1,0,2,3,0] 的 12 路角度。
 * 出自 拨云 _dryrun_protocol 实测, 也由 verify_boyun_parity.py 复核。 */
static const int C_ANGLES[12] = { 90, 98, 132, 88, 90, 88, 52, 85, 90, 130, 95, 85 };

int main(int argc, char **argv)
{
    printf("==== guitar-assist 机构驱动层自检 ====\n\n");

    ga_mech_t m;
    ga_mech_open(&m, NULL, 0);           /* 先离线, 只算不发 */

    if (argc >= 3) {
        if (ga_mech_load_csv(&m, argv[2]) == 0)
            printf("[cfg] 已从 %s 载入\n", argv[2]);
        else
            printf("[cfg] 载入 %s 失败, 用默认参数\n", argv[2]);
    }

    /* ── [1] 角度计算 ── */
    printf("[1] 角度计算\n");
    int a12[12];
    int frets_c[6] = { 0, 1, 0, 2, 3, 0 };
    ga_chord_angles(&m.cfg, frets_c, a12);
    printf("    C 和弦 12 路: ");
    for (int i = 0; i < 12; i++) printf("%d ", a12[i]);
    printf("\n");
    check("C 和弦角度 == 拨云已知答案", memcmp(a12, C_ANGLES, sizeof(C_ANGLES)) == 0);
    printf("    期望:        ");
    for (int i = 0; i < 12; i++) printf("%d ", C_ANGLES[i]);
    printf("\n");

    /* 空弦: 12 路都应是 90 + null_offset */
    int f_open[6] = { 0, 0, 0, 0, 0, 0 };
    ga_chord_angles(&m.cfg, f_open, a12);
    int open_ok = 1;
    for (int i = 0; i < 12; i++) {
        int want = (int)(90.0f + m.cfg.null_offset[i]);
        if (a12[i] != want) open_ok = 0;
    }
    check("空弦 = 90 + null_offset", open_ok);

    /* ── [2] 帧字节 ── */
    printf("\n[2] 帧字节\n");
    uint8_t frame[GA_FRAME_MAX_TOTAL];

    int chord12[12];
    ga_chord_angles(&m.cfg, frets_c, chord12);
    int batch18[18];
    for (int i = 0; i < 12; i++) batch18[i] = chord12[i];
    int cur6[6] = { 70, 60, 55, 77, 55, 75 };   /* point_a 钳位后 */
    for (int i = 0; i < 6; i++) batch18[12 + i] = cur6[i];

    size_t n = ga_make_servo_batch(batch18, frame, sizeof(frame));
    printf("    SERVO_BATCH %zuB: ", n);
    print_hex(frame, n);
    check("BATCH 帧头 = AA 55 12 02", n == 24 && frame[0] == 0xAA && frame[1] == 0x55
          && frame[2] == 0x12 && frame[3] == 0x02);
    check("BATCH 帧长 24B", n == 24);

    n = ga_make_servo_ctrl(GA_SERVO_CTRL_ENABLE, frame, sizeof(frame));
    printf("    SERVO_CTRL %zuB: ", n);
    print_hex(frame, n);
    check("CTRL ENABLE = AA 55 01 08 01 84", n == 6
          && frame[0]==0xAA && frame[1]==0x55 && frame[2]==0x01 && frame[3]==0x08
          && frame[4]==0x01 && frame[5]==0x84);

    n = ga_make_mode_switch(GA_ESP_MODE_PLAY, frame, sizeof(frame));
    printf("    MODE_SWITCH %zuB: ", n);
    print_hex(frame, n);
    check("MODE_SWITCH PLAY = AA 55 02 F0 01 00", n == 6
          && frame[2]==0x02 && frame[3]==0xF0 && frame[4]==0x01 && frame[5]==0x00);

    /* ── [3] 真发一帧(可选) ── */
    if (argc >= 2) {
        printf("\n[3] 实发到 %s\n", argv[1]);
        ga_mech_close(&m);
        ga_mech_open(&m, argv[1], 0);
        if (m.fd < 0) {
            printf("    (串口未打开, 跳过)\n");
        } else {
            ga_mech_set_mode(&m, GA_ESP_MODE_NORMAL);
            ga_mech_enable(&m);
            ga_mech_set_chord(&m, frets_c);
            printf("    已发: MODE_SWITCH(NORMAL) + ENABLE + BATCH(C)\n");
            printf("    观察机构是否把 C 和弦压到位。\n");
        }
    }

    ga_mech_close(&m);

    printf("\n==== %s (%d 项失败) ====\n", g_fail ? "失败" : "全部通过", g_fail);
    return g_fail ? 1 : 0;
}
