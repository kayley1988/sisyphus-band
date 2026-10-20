/*
 * pitch_map.h / pitch_map.c - 频率 <-> 音名 / 音分 换算
 */
#ifndef GA_PITCH_MAP_H
#define GA_PITCH_MAP_H

#include "guitar_common.h"

/* 频率 -> MIDI 编号(带小数)。f<=0 返回 -1 */
double ga_freq_to_midi(double freq);

/* MIDI -> 频率 */
double ga_midi_to_freq(double midi);

/* MIDI -> 音名, 写入 out(至少 8 字节)。如 40 -> "E2" */
void ga_midi_to_name(double midi, char *out, size_t out_len);

/* 把检测结果填到最近的目标弦上。
 * 结果写入 out。nearest_string 可能为 -1(离所有弦都超过 100 音分)。 */
void ga_match_pitch(double freq, double confidence, ga_pitch_result_t *out);

/* 音分偏差: 100 * 12 * log2(f/ref) */
double ga_cents_diff(double freq, double ref_freq);

#endif /* GA_PITCH_MAP_H */
