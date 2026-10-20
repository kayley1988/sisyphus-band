/*
 * chord_db.c - 常用和弦指法数据
 *
 * 指法数组顺序: 索引 0 = 6弦(最粗) ... 索引 5 = 1弦(最细)
 * 值: -1 = 不弹(x), 0 = 空弦, n = 按第 n 品
 *
 * 数据来源为公开的标准吉他教程常用指法(开放把位优先, 便于初学者)。
 * 无障碍场景下我优先收录【开放把位和弦】, 因为不需要大横按,
 * 对手部力量弱的用户/初学用户更友好, 这点在选曲目时要考虑。
 */
#include "chord_db.h"
#include "pitch_map.h"   /* ga_midi_to_name */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>

#define X (-1)

static const ga_chord_def_t DB[] = {
    /* ---- C 大调常用 ---- */
    { "C",    48, GA_CHORD_MAJOR,          { X, 3, 2, 0, 1, 0 },
      { 0, 3, 2, 0, 1, 0 }, 1, "chord_C.wav" },
    { "Cmaj7",48, GA_CHORD_MAJOR_SEVENTH,  { X, 3, 2, 0, 0, 0 },
      { 0, 3, 2, 0, 0, 0 }, 1, "chord_Cmaj7.wav" },
    { "Dm",   50, GA_CHORD_MINOR,          { X, X, 0, 2, 3, 1 },
      { 0, 0, 0, 2, 3, 1 }, 1, "chord_Dm.wav" },
    { "Em",   52, GA_CHORD_MINOR,          { 0, 2, 2, 0, 0, 0 },
      { 0, 2, 3, 0, 0, 0 }, 1, "chord_Em.wav" },
    { "F",    53, GA_CHORD_MAJOR,          { 1, 3, 3, 2, 1, 1 },
      { 1, 3, 4, 2, 1, 1 }, 1, "chord_F.wav" },   /* 小横按, 比大横按省力 */
    { "G",    55, GA_CHORD_MAJOR,          { 3, 2, 0, 0, 0, 3 },
      { 2, 1, 0, 0, 0, 3 }, 1, "chord_G.wav" },
    { "Am",   57, GA_CHORD_MINOR,          { X, 0, 2, 2, 1, 0 },
      { 0, 0, 2, 3, 1, 0 }, 1, "chord_Am.wav" },

    /* ---- 常用七和弦 ---- */
    { "Am7",  57, GA_CHORD_MINOR_SEVENTH,  { X, 0, 2, 0, 1, 0 },
      { 0, 0, 2, 0, 1, 0 }, 1, "chord_Am7.wav" },
    { "Dm7",  50, GA_CHORD_MINOR_SEVENTH,  { X, X, 0, 2, 1, 1 },
      { 0, 0, 0, 2, 1, 1 }, 1, "chord_Dm7.wav" },
    { "G7",   55, GA_CHORD_SEVENTH,        { 3, 2, 0, 0, 0, 1 },
      { 3, 2, 0, 0, 0, 1 }, 1, "chord_G7.wav" },
    { "C7",   48, GA_CHORD_SEVENTH,        { X, 3, 2, 3, 1, 0 },
      { 0, 3, 2, 4, 1, 0 }, 1, "chord_C7.wav" },
    { "E7",   52, GA_CHORD_SEVENTH,        { 0, 2, 0, 1, 0, 0 },
      { 0, 2, 0, 1, 0, 0 }, 1, "chord_E7.wav" },
    { "A7",   57, GA_CHORD_SEVENTH,        { X, 0, 2, 0, 2, 0 },
      { 0, 0, 2, 0, 3, 0 }, 1, "chord_A7.wav" },
    { "D7",   50, GA_CHORD_SEVENTH,        { X, X, 0, 2, 1, 2 },
      { 0, 0, 0, 2, 1, 3 }, 1, "chord_D7.wav" },
    { "B7",   59, GA_CHORD_SEVENTH,        { X, 2, 1, 2, 0, 2 },
      { 0, 2, 1, 3, 0, 4 }, 1, "chord_B7.wav" },
    { "Fmaj7",53, GA_CHORD_MAJOR_SEVENTH,  { X, X, 3, 2, 1, 0 },
      { 0, 0, 3, 2, 1, 0 }, 1, "chord_Fmaj7.wav" },

    /* ---- D 调 / 常用变化和弦 ---- */
    { "D",    50, GA_CHORD_MAJOR,          { X, X, 0, 2, 3, 2 },
      { 0, 0, 0, 1, 3, 2 }, 1, "chord_D.wav" },
    { "A",    57, GA_CHORD_MAJOR,          { X, 0, 2, 2, 2, 0 },
      { 0, 0, 1, 2, 3, 0 }, 1, "chord_A.wav" },
    { "E",    52, GA_CHORD_MAJOR,          { 0, 2, 2, 1, 0, 0 },
      { 0, 2, 3, 1, 0, 0 }, 1, "chord_E.wav" },
    { "Bm",   59, GA_CHORD_MINOR,          { X, 2, 4, 4, 3, 2 },
      { 0, 1, 3, 4, 2, 1 }, 1, "chord_Bm.wav" },

    /* ---- 带升号/降号的常用和弦(流行歌转调时高频出现) ---- */
    { "F#m",  54, GA_CHORD_MINOR,          { 2, 4, 4, 2, 2, 2 },
      { 1, 3, 4, 1, 1, 1 }, 1, "chord_Fsm.wav" },  /* 大横按, 进阶和弦 */
    { "Gm",   55, GA_CHORD_MINOR,          { 3, 5, 5, 3, 3, 3 },
      { 1, 3, 4, 1, 1, 1 }, 3, "chord_Gm.wav" },   /* 三品横按 */
    { "Cm",   48, GA_CHORD_MINOR,          { X, 3, 5, 5, 4, 3 },
      { 0, 1, 3, 4, 2, 1 }, 3, "chord_Cm.wav" },
    { "Bb",   58, GA_CHORD_MAJOR,          { X, 1, 3, 3, 3, 1 },
      { 0, 1, 2, 3, 4, 1 }, 1, "chord_Bb.wav" },   /* 仅根音在5弦, 相对好按 */
    { "F#",   54, GA_CHORD_MAJOR,          { 2, 4, 4, 3, 2, 2 },
      { 1, 3, 4, 2, 1, 1 }, 1, "chord_Fs.wav" },

    /* ---- sus / 挂留和弦(流行歌常用, 听觉上"悬而未决") ---- */
    { "Dsus4",50, GA_CHORD_SUS4,           { X, X, 0, 2, 3, 3 },
      { 0, 0, 0, 1, 2, 3 }, 1, "chord_Dsus4.wav" },
    { "Asus4",57, GA_CHORD_SUS4,           { X, 0, 2, 2, 3, 0 },
      { 0, 0, 1, 2, 3, 0 }, 1, "chord_Asus4.wav" },
    { "Esus4",52, GA_CHORD_SUS4,           { 0, 2, 2, 2, 0, 0 },
      { 0, 1, 2, 3, 0, 0 }, 1, "chord_Esus4.wav" },
};

#define DB_COUNT ((int)(sizeof(DB) / sizeof(DB[0])))

static int str_ieq(const char *a, const char *b) {
    while (*a && *b) {
        if (tolower((unsigned char)*a) != tolower((unsigned char)*b)) return 0;
        a++; b++;
    }
    return *a == '\0' && *b == '\0';
}

const ga_chord_def_t *ga_chord_find(const char *name) {
    if (!name) return NULL;
    for (int i = 0; i < DB_COUNT; i++) {
        if (str_ieq(DB[i].name, name)) return &DB[i];
    }
    return NULL;
}

int ga_chord_count(void) { return DB_COUNT; }

const ga_chord_def_t *ga_chord_at(int index) {
    if (index < 0 || index >= DB_COUNT) return NULL;
    return &DB[index];
}

/* 六弦 -> 一弦的人话描述 */
void ga_chord_describe(const ga_chord_def_t *c, int string_first,
                       char *out, size_t out_len) {
    if (!c || !out || out_len == 0) return;
    out[0] = '\0';

    if (string_first == 0) string_first = 1;  /* 默认从 6 弦开始 */

    /* 和弦性质的中文说法, 给 TTS 用 */
    const char *q = "";
    switch (c->quality) {
        case GA_CHORD_MAJOR:          q = "大";     break;
        case GA_CHORD_MINOR:          q = "小";     break;
        case GA_CHORD_SEVENTH:        q = "属七";   break;
        case GA_CHORD_MINOR_SEVENTH:  q = "小七";   break;
        case GA_CHORD_MAJOR_SEVENTH:  q = "大七";   break;
        case GA_CHORD_SUS4:           q = "挂四";   break;
        default:                      q = "";       break;
    }
    /* 根音名 */
    char root[8];
    ga_midi_to_name(c->root_midi, root, sizeof(root));
    /* 去掉八度数字, 语音播报不需要 */
    for (char *p = root; *p; p++) {
        if (*p >= '0' && *p <= '9') { *p = '\0'; break; }
    }

    /* 音名里的 # 朗读成 "升" */
    char root_spoken[16];
    size_t ri = 0;
    for (const char *p = root; *p && ri + 2 < sizeof(root_spoken); p++) {
        if (*p == '#') {
            root_spoken[ri++] = (char)0xE5; /* UTF-8 '升' 简化处理 */
            root_spoken[ri++] = (char)0x8D;
        } else {
            root_spoken[ri++] = *p;
        }
    }
    root_spoken[ri] = '\0';

    int written = snprintf(out, out_len, "%s%s和弦, ", root_spoken, q);
    if (written < 0) return;

    /* 逐弦描述 */
    static const char *STR_NAME[6] = { "六弦", "五弦", "四弦",
                                       "三弦", "二弦", "一弦" };
    for (int k = 0; k < GA_STRING_COUNT; k++) {
        int i = string_first ? k : (GA_STRING_COUNT - 1 - k);
        size_t used = strlen(out);
        if (used >= out_len - 1) break;

        char seg[48];
        if (c->frets[i] < 0) {
            snprintf(seg, sizeof(seg), "%s不弹", STR_NAME[i]);
        } else if (c->frets[i] == 0) {
            snprintf(seg, sizeof(seg), "%s空弦", STR_NAME[i]);
        } else {
            /* 中文数字, 避免 TTS 把 "3品" 读成 "three pin" */
            static const char *CN_NUM[] = { "零","一","二","三","四",
                                            "五","六","七","八","九","十" };
            const char *num = (c->frets[i] < 10) ? CN_NUM[c->frets[i]]
                                                 : "多";
            snprintf(seg, sizeof(seg), "%s%s品", STR_NAME[i], num);
        }
        strncat(out, seg, out_len - strlen(out) - 1);
        if (k < GA_STRING_COUNT - 1 && strlen(out) < out_len - 2) {
            strncat(out, ", ", out_len - strlen(out) - 1);
        }
    }
}

char *ga_chord_dump_json(void) {
    /* 估算大小: 每个和弦约 200 字节 */
    size_t cap = (size_t)DB_COUNT * 256 + 64;
    char *buf = (char *)malloc(cap);
    if (!buf) return NULL;

    size_t pos = 0;
    pos += (size_t)snprintf(buf + pos, cap - pos, "[\n");
    for (int i = 0; i < DB_COUNT; i++) {
        const ga_chord_def_t *c = &DB[i];
        char root[8];
        ga_midi_to_name(c->root_midi, root, sizeof(root));
        pos += (size_t)snprintf(buf + pos, cap - pos,
            "  {\"name\":\"%s\",\"root\":\"%s\",\"quality\":%d,"
            "\"base_fret\":%d,\"frets\":[",
            c->name, root, (int)c->quality, c->base_fret);
        for (int k = 0; k < GA_STRING_COUNT; k++) {
            pos += (size_t)snprintf(buf + pos, cap - pos, "%s%d",
                     k ? "," : "", c->frets[k]);
        }
        pos += (size_t)snprintf(buf + pos, cap - pos, "],\"fingers\":[");
        for (int k = 0; k < GA_STRING_COUNT; k++) {
            pos += (size_t)snprintf(buf + pos, cap - pos, "%s%d",
                     k ? "," : "", c->fingers[k]);
        }
        pos += (size_t)snprintf(buf + pos, cap - pos,
                 "],\"voicing\":\"%s\"}%s\n",
                 c->voicing_name, (i == DB_COUNT - 1) ? "" : ",");
    }
    pos += (size_t)snprintf(buf + pos, cap - pos, "]\n");
    return buf;
}
