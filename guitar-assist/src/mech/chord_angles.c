/*
 * chord_angles.c - 和弦指法 -> 18 路角度 (对齐《拨云》servo_controller.py)
 *
 * 一致性由 scripts/verify_boyun_parity.py 对拍保证。改这里先跑对拍。
 */
#include "chord_angles.h"

#include <math.h>

/* ── 取整: 必须复刻 Python round() 的"四舍六入五取偶" ──
 *
 * 这是最容易踩的坑: C 的 lround() 是"五入"(round half away from zero),
 * Python 3 的 round() 是"五取偶"(round half to even)。两者在恰好 .5 时
 * 会差 1 度, 于是每一路角度都可能和拨云/固件对不上, 机构整体偏一品。
 *
 * 校准值(offset)常带小数, 恰好落在 .5 并不罕见, 所以必须严格对齐。 */
static int ga_round_half_even(double v)
{
    double fl = floor(v);
    double d  = v - fl;
    if (d > 0.5) return (int)(fl + 1.0);
    if (d < 0.5) return (int)fl;
    /* 恰好 .5: 取最近的偶数 */
    long long n = (long long)fl;
    return (int)((n % 2 == 0) ? n : n + 1);
}

static int clamp180(int a)
{
    if (a < 0)   return 0;
    if (a > 180) return 180;
    return a;
}

/* ════════════════════════════════════════════════
 * FRET_OFFSETS: fret -> (ch0方向, ch0压深索引, ch1方向, ch1压深索引)
 * 方向 0 表示该通道无偏移; 索引 -1 表示不读 press_offsets。
 * 与 servo_controller._FRET_OFFSETS 逐项一致。
 * ════════════════════════════════════════════════ */
static const int FRET_OFF[5][4] = {
    /* fret 0 */ {  0, -1,  0, -1 },
    /* fret 1 */ { +1,  0,  0, -1 },
    /* fret 2 */ { -1,  1,  0, -1 },
    /* fret 3 */ {  0, -1, +1,  2 },
    /* fret 4 */ {  0, -1, -1,  3 },
};

/* 弦4/5/6(x>=3) 机械反转: 1<->2 品、3<->4 品 (完整替换四元组, 保留 ch0/ch1 归属) */
static const int SWAP456[5][4] = {
    /* 0 */ {  0,  0,  0,  0 },        /* 不会用到 */
    /* 1 */ { -1,  1,  0, -1 },
    /* 2 */ { +1,  0,  0, -1 },
    /* 3 */ {  0, -1, -1,  3 },
    /* 4 */ {  0, -1, +1,  2 },
};

/* 每根弦的 ch0 索引; ch1 = k+1。 */
static const int K[GA_MECH_STRINGS] = { 0, 2, 4, 6, 8, 10 };

/* ════════════════════════════════════════════════ */

void ga_mech_cfg_default(ga_mech_cfg_t *cfg)
{
    if (!cfg) return;

    /* 与 servo_controller._load_config 的默认值一致 */
    static const float d_null[GA_MECH_CHORD_CH] = {
        0, 8, 0, -2, 0, -2, 0, -5, 0, -12, 5, -5
    };
    static const float d_press[GA_MECH_STRINGS][4] = {
        {  45,  50,  55,  60 },
        {  42,  48,  53,  58 },
        {  40,  46,  51,  56 },
        { -38, -44, -49, -54 },
        { -35, -41, -47, -52 },
        { -32, -38, -44, -50 },
    };
    static const float d_strum[GA_MECH_STRUM_CH][2] = {
        { 70, 130 }, { 60, 120 }, { 55, 130 },
        { 77, 145 }, { 30,  95 }, { 75, 155 },
    };

    for (int i = 0; i < GA_MECH_CHORD_CH; i++) cfg->null_offset[i] = d_null[i];
    for (int s = 0; s < GA_MECH_STRINGS; s++)
        for (int f = 0; f < 4; f++) cfg->press_offsets[s][f] = d_press[s][f];
    for (int i = 0; i < GA_MECH_STRUM_CH; i++) {
        cfg->strum_angles[i][0] = d_strum[i][0];
        cfg->strum_angles[i][1] = d_strum[i][1];
    }
    cfg->home_chord_angle  = 90.0f;
    cfg->home_strum_offset = 0.0f;
}

void ga_chord_angles(const ga_mech_cfg_t *cfg, const int frets[GA_MECH_STRINGS],
                     int out12[GA_MECH_CHORD_CH])
{
    double ang[GA_MECH_CHORD_CH];

    for (int x = 0; x < GA_MECH_STRINGS; x++) {
        int f = frets ? frets[x] : 0;
        if (f < 0) f = 0;          /* -1 不弹 -> 按空弦处理 (与原实现 max(0,min(4,..)) 一致) */
        if (f > 4) f = 4;

        int s0 = FRET_OFF[f][0], i0 = FRET_OFF[f][1];
        int s1 = FRET_OFF[f][2], i1 = FRET_OFF[f][3];

        /* 弦4-6 机械反转 */
        if (x >= 3 && f >= 1) {
            s0 = SWAP456[f][0]; i0 = SWAP456[f][1];
            s1 = SWAP456[f][2]; i1 = SWAP456[f][3];
        }

        double base0 = GA_PRESS_ZERO + cfg->null_offset[K[x]];
        double base1 = GA_PRESS_ZERO + cfg->null_offset[K[x] + 1];

        ang[K[x]]     = base0 + (i0 >= 0 ? (double)s0 * cfg->press_offsets[x][i0] : 0.0);
        ang[K[x] + 1] = base1 + (i1 >= 0 ? (double)s1 * cfg->press_offsets[x][i1] : 0.0);
    }

    for (int i = 0; i < GA_MECH_CHORD_CH; i++) {
        out12[i] = clamp180(ga_round_half_even(ang[i]));
    }
}

void ga_strum_angles(const ga_mech_cfg_t *cfg, const int strums[GA_MECH_STRUM_CH],
                     int flip[GA_MECH_STRUM_CH], int out6[GA_MECH_STRUM_CH])
{
    for (int x = 0; x < GA_MECH_STRUM_CH; x++) {
        out6[x] = 0;
        if (strums && strums[x]) {
            double a = cfg->strum_angles[x][flip[x] & 1];
            int v = ga_round_half_even(a);
            /* 钳位到 [MIN, MAX]: 注意顺序是先 round 后钳位, 与原实现一致 */
            if ((double)v < GA_STRUM_ANGLE_MIN) v = (int)GA_STRUM_ANGLE_MIN;
            if ((double)v > GA_STRUM_ANGLE_MAX) v = (int)GA_STRUM_ANGLE_MAX;
            out6[x] = v;
            flip[x] ^= 1;
        }
    }
}

void ga_mech_batch18(const ga_mech_cfg_t *cfg, const int frets[GA_MECH_STRINGS],
                     const int cur_strum6[GA_MECH_STRUM_CH],
                     int out18[GA_MECH_TOTAL_CH])
{
    int chord12[GA_MECH_CHORD_CH];
    ga_chord_angles(cfg, frets, chord12);

    for (int i = 0; i < GA_MECH_CHORD_CH; i++) {
        out18[i] = chord12[i];
    }

    /* 拨弦通道: 用当前追踪值; 未定位(<=0)时用各弦 point_a 填充。
     * 与 servo_controller._update_chord_channels 的 BATCH 构建逻辑一致 ——
     * 这样和弦更新不会把已定位的拨弦通道冲成 0。 */
    for (int i = 0; i < GA_MECH_STRUM_CH; i++) {
        int v;
        if (cur_strum6 && cur_strum6[i] > 0) {
            v = cur_strum6[i];
        } else {
            /* ★ 顺序严格照 servo_controller._update_chord_channels:
             *     fallback = clamp(point_a)  然后  int(round(fallback))
             *   即"先钳位后取整"(与 _compute_strum_angles_only 的
             *   "先取整后钳位"顺序相反, 两者都不能调换)。 */
            double fa = cfg->strum_angles[i][0];
            double cl = fa;
            if (cl < GA_STRUM_ANGLE_MIN) cl = GA_STRUM_ANGLE_MIN;
            if (cl > GA_STRUM_ANGLE_MAX) cl = GA_STRUM_ANGLE_MAX;
            v = ga_round_half_even(cl);
        }
        out18[GA_MECH_CHORD_CH + i] = clamp180(v);
    }
}
