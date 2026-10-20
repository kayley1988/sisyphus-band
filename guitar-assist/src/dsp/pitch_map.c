/*
 * pitch_map.c - 频率/音名/音分 换算实现
 */
#include "pitch_map.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

const ga_string_spec_t GA_STANDARD_TUNING[GA_STRING_COUNT] = {
    /* 从 6 弦(最粗,低音) 到 1 弦(最细,高音) */
    { "6弦 低音E", "E2", 82.4069,  40 },
    { "5弦 A",     "A2", 110.0000, 45 },
    { "4弦 D",     "D3", 146.8324, 50 },
    { "3弦 G",     "G3", 195.9977, 55 },
    { "2弦 B",     "B3", 246.9417, 59 },
    { "1弦 高音E", "E4", 329.6276, 64 },
};

double ga_freq_to_midi(double freq) {
    if (freq <= 0.0) return -1.0;
    /* MIDI 69 = A4 = 440Hz */
    return 69.0 + 12.0 * log2(freq / 440.0);
}

double ga_midi_to_freq(double midi) {
    return 440.0 * pow(2.0, (midi - 69.0) / 12.0);
}

void ga_midi_to_name(double midi, char *out, size_t out_len) {
    static const char *NAMES[12] = {
        "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"
    };
    if (!out || out_len == 0) return;
    if (midi < 0 || midi > 127) { snprintf(out, out_len, "?"); return; }
    int m = (int)lround(midi);
    int pc = m % 12;               /* pitch class */
    int octave = m / 12 - 1;       /* MIDI 60 = C4 */
    snprintf(out, out_len, "%s%d", NAMES[pc], octave);
}

double ga_cents_diff(double freq, double ref_freq) {
    if (freq <= 0.0 || ref_freq <= 0.0) return 0.0;
    return 100.0 * 12.0 * log2(freq / ref_freq);
}

void ga_match_pitch(double freq, double confidence, ga_pitch_result_t *out) {
    if (!out) return;
    memset(out, 0, sizeof(*out));
    out->freq_hz        = freq;
    out->confidence     = confidence;
    out->nearest_string = -1;
    out->nearest_midi   = -1;
    strcpy(out->nearest_note, "--");

    if (freq <= 0.0) return;

    /* 先算最近的标准音(MIDI 体系里, 与目标弦无关)。
     * 这样用户弹了一个非空弦音, 也能告诉他"这是 C#4, 偏高 12 音分"。 */
    double midi_f = ga_freq_to_midi(freq);
    int    midi_r = (int)lround(midi_f);
    out->nearest_midi = midi_r;
    ga_midi_to_name(midi_r, out->nearest_note, sizeof(out->nearest_note));

    /* 再找最近的【空弦】。调音场景下用户通常从某根弦开始,
     * 所以同时给出"离哪根弦最近"。 */
    int    best_str = -1;
    double best_abs  = 1e9;
    for (int i = 0; i < GA_STRING_COUNT; i++) {
        double c = fabs(ga_cents_diff(freq, GA_STANDARD_TUNING[i].freq));
        if (c < best_abs) { best_abs = c; best_str = i; }
    }
    /* 超过 150 音分(1.5 个半音)就不认为是这根弦了, 避免误导向 */
    if (best_str >= 0 && best_abs <= 150.0) {
        out->nearest_string = best_str;
        out->cents_offset   = ga_cents_diff(freq,
                                GA_STANDARD_TUNING[best_str].freq);
    } else {
        out->nearest_string = -1;
        out->cents_offset   = ga_cents_diff(freq,
                                ga_midi_to_freq((double)midi_r));
    }
}
