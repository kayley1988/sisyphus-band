/*
 * chord_angles.h - 和弦指法 -> 18 路舵机角度 (C 版, 对齐《拨云》)
 *
 * 依据: 拨云 src/guitarist/servo_controller.py
 *   _compute_chord_angles_only() / _compute_strum_angles_only()
 * 以及 ESP32 固件 chord_engine.cpp 的 compute_angles()/compute_strum()。
 *
 * ★ 三处口径必须一致, 否则机构会压错品/压不实:
 *   1. 按压零点 zero = 90 (硬件口径, 不是"装机角")
 *   2. FRET_OFFSETS 表 + 弦4-6(x>=3) 机械反转 SWAP
 *   3. angle = base + dir * press_offset, base = zero + null_offset[ch]
 *
 * 一致性由 scripts/verify_boyun_parity.py 对拍保证。
 * 角度计算纯逻辑, 不依赖串口/硬件, 可在 PC 上单测。
 */
#ifndef GA_MECH_CHORD_ANGLES_H
#define GA_MECH_CHORD_ANGLES_H

#ifdef __cplusplus
extern "C" {
#endif

/* 按压零点: 硬件真值, 与固件/播放侧一致。
 * 注意: 这不是"装机角 home_chord_angle"——后者只用于归位姿态。 */
#define GA_PRESS_ZERO        90.0f
/* 拨弦安全限位: 超出会被 ESP32 静默拒绝, 所以主机先钳位。 */
#define GA_STRUM_ANGLE_MIN   55.0f
#define GA_STRUM_ANGLE_MAX   125.0f

/* 6 弦标准调弦索引: 0=6弦(低音E) ... 5=1弦(高音E), 与 ui_quick_chord_t.fingering 同序 */
#define GA_MECH_STRINGS      6
#define GA_MECH_CHORD_CH     12    /* 和弦舵机 12 路 */
#define GA_MECH_STRUM_CH     6     /* 拨弦舵机 6 路  */
#define GA_MECH_TOTAL_CH     18    /* 合计 18 路       */

/* 校准参数 (对应 servo_config.csv)。默认值 = 拨云出厂默认, 板子首次运行即可用。 */
typedef struct {
    float null_offset[GA_MECH_CHORD_CH];        /* 12 路零点偏移 */
    float press_offsets[GA_MECH_STRINGS][4];    /* 每弦 4 个压深 (1..4 品方向) */
    float strum_angles[GA_MECH_STRUM_CH][2];    /* 每路拨弦 [point_a, point_b] */
    float home_chord_angle;                     /* 归位姿态角(仅 homing 用) */
    float home_strum_offset;                    /* 拨弦归位统一偏移 */
} ga_mech_cfg_t;

/* 填入拨云出厂默认参数 (可用; 真实校准值应从 servo_config.csv 载入) */
void ga_mech_cfg_default(ga_mech_cfg_t *cfg);

/* 和弦指法 -> 12 路角度。
 * frets[6]: 各弦品位, 0=空弦, 1..4=品位, -1=不弹(按空弦处理, 与原实现一致)。
 * out12 每项被钳位到 0~180 并四舍五入。 */
void ga_chord_angles(const ga_mech_cfg_t *cfg, const int frets[GA_MECH_STRINGS],
                     int out12[GA_MECH_CHORD_CH]);

/* 拨弦 -> 6 路角度。strums[x]!=0 表示拨该弦; flip[x] 每次拨动翻转(上下交替)。
 * out6 会被钳位到 [GA_STRUM_ANGLE_MIN, GA_STRUM_ANGLE_MAX]; 不拨的弦记 0。 */
void ga_strum_angles(const ga_mech_cfg_t *cfg, const int strums[GA_MECH_STRUM_CH],
                     int flip[GA_MECH_STRUM_CH], int out6[GA_MECH_STRUM_CH]);

/* 组装一帧 BATCH 用的 18 路角度: [和弦0..11, 拨弦0..5]。
 * cur_strum6 为当前拨弦位置(来自上一次), 未定位的通道用各弦 point_a 填充 ——
 * 这样和弦更新时不会把已定位的拨弦通道冲成 0(0 会被 ESP32 跳过导致位置丢失)。 */
void ga_mech_batch18(const ga_mech_cfg_t *cfg, const int frets[GA_MECH_STRINGS],
                     const int cur_strum6[GA_MECH_STRUM_CH],
                     int out18[GA_MECH_TOTAL_CH]);

#ifdef __cplusplus
}
#endif
#endif /* GA_MECH_CHORD_ANGLES_H */
