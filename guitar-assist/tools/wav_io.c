/*
 * wav_io.c - 极简 WAV 读写
 */
#include "wav_io.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#pragma pack(push, 1)
typedef struct {
    char     riff[4];
    uint32_t size;
    char     wave[4];
} riff_hdr_t;

typedef struct {
    char     id[4];
    uint32_t size;
} chunk_hdr_t;

typedef struct {
    uint16_t audio_format;
    uint16_t channels;
    uint32_t sample_rate;
    uint32_t byte_rate;
    uint16_t block_align;
    uint16_t bits_per_sample;
} fmt_t;
#pragma pack(pop)

int ga_wav_load(const char *path, ga_wav_t *out) {
    if (!path || !out) return -1;
    memset(out, 0, sizeof(*out));

    FILE *f = fopen(path, "rb");
    if (!f) { fprintf(stderr, "[wav] 打不开 %s\n", path); return -1; }

    riff_hdr_t rh;
    if (fread(&rh, sizeof(rh), 1, f) != 1 ||
        memcmp(rh.riff, "RIFF", 4) != 0 || memcmp(rh.wave, "WAVE", 4) != 0) {
        fprintf(stderr, "[wav] 不是合法 WAV\n");
        fclose(f); return -1;
    }

    fmt_t fmt;
    memset(&fmt, 0, sizeof(fmt));
    int have_fmt = 0;
    long data_offset = -1;
    uint32_t data_size = 0;

    /* 遍历 chunk, 跳过 fmt 之前的其它块(如 LIST) */
    chunk_hdr_t ch;
    while (fread(&ch, sizeof(ch), 1, f) == 1) {
        if (memcmp(ch.id, "fmt ", 4) == 0) {
            if (ch.size < sizeof(fmt_t)) { fclose(f); return -1; }
            if (fread(&fmt, sizeof(fmt_t), 1, f) != 1) { fclose(f); return -1; }
            /* 跳过 fmt 里可能的扩展字节 */
            if (ch.size > sizeof(fmt_t)) {
                fseek(f, (long)(ch.size - sizeof(fmt_t)), SEEK_CUR);
            }
            have_fmt = 1;
        } else if (memcmp(ch.id, "data", 4) == 0) {
            data_offset = ftell(f);
            data_size   = ch.size;
            fseek(f, (long)ch.size, SEEK_CUR);
            if (have_fmt) break;   /* fmt 和 data 都拿到了 */
        } else {
            fseek(f, (long)ch.size + (ch.size & 1), SEEK_CUR);
        }
    }

    if (!have_fmt || data_offset < 0) {
        fprintf(stderr, "[wav] 缺少 fmt 或 data 块\n");
        fclose(f); return -1;
    }
    if (fmt.bits_per_sample != 16) {
        fprintf(stderr, "[wav] 只支持 16bit, 当前 %d bit\n",
                fmt.bits_per_sample);
        fclose(f); return -1;
    }

    int ch_n = fmt.channels;
    int bytes_per_frame = ch_n * 2;
    int frames = (int)(data_size / (uint32_t)bytes_per_frame);
    if (frames <= 0) { fclose(f); return -1; }

    int16_t *buf = (int16_t *)malloc((size_t)frames * (size_t)ch_n * sizeof(int16_t));
    if (!buf) { fclose(f); return -1; }

    fseek(f, data_offset, SEEK_SET);
    size_t got = fread(buf, sizeof(int16_t), (size_t)frames * (size_t)ch_n, f);
    fclose(f);

    frames = (int)(got / (size_t)ch_n);

    out->data        = buf;
    out->frames      = frames;
    out->channels    = ch_n;
    out->sample_rate = (int)fmt.sample_rate;
    return 0;
}

void ga_wav_free(ga_wav_t *w) {
    if (!w) return;
    free(w->data);
    w->data = NULL;
    w->frames = 0;
}

int16_t *ga_wav_to_mono(const ga_wav_t *w, int *out_frames) {
    if (!w || !w->data || w->frames <= 0) return NULL;
    int n = w->frames;
    int16_t *mono = (int16_t *)malloc((size_t)n * sizeof(int16_t));
    if (!mono) return NULL;

    if (w->channels == 1) {
        memcpy(mono, w->data, (size_t)n * sizeof(int16_t));
    } else {
        for (int i = 0; i < n; i++) {
            int32_t acc = 0;
            for (int c = 0; c < w->channels; c++) {
                acc += w->data[i * w->channels + c];
            }
            mono[i] = (int16_t)(acc / w->channels);
        }
    }
    if (out_frames) *out_frames = n;
    return mono;
}

int ga_wav_save_mono(const char *path, const int16_t *data, int frames,
                     int sample_rate) {
    FILE *f = fopen(path, "wb");
    if (!f) return -1;

    uint32_t data_bytes = (uint32_t)frames * 2;
    riff_hdr_t rh = { {'R','I','F','F'}, 36 + data_bytes, {'W','A','V','E'} };
    fwrite(&rh, sizeof(rh), 1, f);

    chunk_hdr_t fh = { {'f','m','t',' '}, 16 };
    fwrite(&fh, sizeof(fh), 1, f);

    fmt_t fmt = { 1, 1, (uint32_t)sample_rate,
                  (uint32_t)(sample_rate * 2), 2, 16 };
    fwrite(&fmt, sizeof(fmt), 1, f);

    chunk_hdr_t dh = { {'d','a','t','a'}, data_bytes };
    fwrite(&dh, sizeof(dh), 1, f);
    fwrite(data, 1, data_bytes, f);
    fclose(f);
    return 0;
}
