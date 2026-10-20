/*
 * mech_driver.h - 机械机构驱动层: 把和弦/拨弦命令变成串口帧发给 ESP32
 *
 * 这是 guitar-assist 与档3(《拨云》)硬件之间的**桥**:
 *
 *   [和弦识别/按键选择] -> ga_mech_set_chord()
 *        -> chord_angles 算 18 路角度
 *        -> frame_codec 打成 NORMAL_SERVO_BATCH 帧
 *        -> 串口 /dev/ttyS3 @921600 -> ESP32 -> PCA9685/PWM -> 18 路舵机
 *
 * ── 与档3主程序共存 ──
 * 档3主程序(guitar.service)也独占 /dev/ttyS3。两个进程同时写会出交错帧。
 * 本层用 **flock(LOCK_EX|LOCK_NB)** 对串口做排他锁:
 *   - 档3的面板(servo_panel.py, 已加固)也用同一把文件锁 -> 互斥有效
 *   - 若串口已被别的进程锁住, ga_mech_open 返回失败并给出提示, 不静默乱写
 * 生产上二选一: 要么档3主程序停/切到别的串口, 要么本程序只读感知不驱动。
 *
 * ── PC 可用 ──
 * 非 Linux 或打不开串口时 fd = -1, 此时所有 API 仍然**正确构造帧**并记录到
 * last_frame, 只是不发出去 —— 便于在 PC 上跑单测/演示(见 tools/mech_selftest.c)。
 */
#ifndef GA_MECH_DRIVER_H
#define GA_MECH_DRIVER_H

#include <stdint.h>
#include <stddef.h>

#include "frame_codec.h"
#include "chord_angles.h"

#ifdef __cplusplus
extern "C" {
#endif

/* 与《拨云》一致: /dev/ttyS3 @ 921600 */
#define GA_MECH_DEFAULT_DEV   "/dev/ttyS3"
#define GA_MECH_DEFAULT_BAUD  921600

typedef struct {
    int  fd;                 /* 串口 fd, -1 = 未打开(仍可离线构造帧) */
    int  lock_held;          /* 1 = 已持有 flock 排他锁 */

    ga_mech_cfg_t cfg;       /* 校准参数 */

    /* 状态追踪: 只更新涉及的部分, 避免部分更新把别的通道冲掉 */
    int  chord12[GA_MECH_CHORD_CH];
    int  strum6[GA_MECH_STRUM_CH];
    int  flip[GA_MECH_STRUM_CH];
    int  enabled;

    /* 便于测试/调试: 最近一帧的内容 */
    uint8_t last_frame[GA_FRAME_MAX_TOTAL];
    size_t  last_frame_len;
    unsigned long tx_frames;
} ga_mech_t;

/* 打开机构。dev 传 NULL 用 GA_MECH_DEFAULT_DEV; baud<=0 用默认。
 * 串口打不开不算致命: 返回 0 但 m->fd=-1(离线模式), 可用 mech_selftest 看帧。 */
int  ga_mech_open(ga_mech_t *m, const char *dev, int baud);

void ga_mech_close(ga_mech_t *m);

/* 载入校准参数(直接覆盖 cfg)。若需从 CSV 载入, 见 ga_mech_load_csv。 */
void ga_mech_set_cfg(ga_mech_t *m, const ga_mech_cfg_t *cfg);

/* 从《拨云》servo_config.csv 载入 null_offset / press_offsets / strum_angles。
 * 返回 0 成功; 文件缺失或格式不符返回 -1(此时 cfg 保持默认)。 */
int  ga_mech_load_csv(ga_mech_t *m, const char *csv_path);

/* 设定和弦: 算角度 + 发 BATCH 帧。返回发送字节数(离线模式返回将要发的长度)。 */
int  ga_mech_set_chord(ga_mech_t *m, const int frets[GA_MECH_STRINGS]);

/* 拨弦: mask[x]!=0 表示拨该弦(上下交替)。 */
int  ga_mech_strum(ga_mech_t *m, const int mask[GA_MECH_STRUM_CH]);

/* 舵机电源: 使能/失能/复位归位 */
int  ga_mech_enable(ga_mech_t *m);
int  ga_mech_disable(ga_mech_t *m);
int  ga_mech_reset_idle(ga_mech_t *m);

/* 归位: 和弦 12 路走到 home_chord_angle 平角, 拨弦回各自 point_a。
 * (与档3"一键归位"同语义) */
int  ga_mech_home(ga_mech_t *m);

/* 切 ESP32 模式 */
int  ga_mech_set_mode(ga_mech_t *m, ga_esp_mode_t mode);

/* 把校准参数持久化到 ESP32 NVS */
int  ga_mech_save_cfg(ga_mech_t *m);

/* 底层: 发一帧(自动记录到 last_frame)。返回写入字节数。 */
int  ga_mech_send(ga_mech_t *m, const uint8_t *frame, size_t len);

#ifdef __cplusplus
}
#endif
#endif /* GA_MECH_DRIVER_H */
