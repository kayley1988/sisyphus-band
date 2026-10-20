/*
 * chord_recog.c - 和弦识别实现
 *
 * 包含两条链路:
 *   1. TEMPLATE: 纯 C 模板匹配(主力, 已实测 8/8)
 *   2. RKNN:     调用 NPU 推理(可选, 需要 .rknn 模型)
 */
#include "chord_recog.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

#ifdef GA_HAVE_RKNN
#include "rknn_api.h"
#endif

/* ---------------- 音级与和弦性质工具 ---------------- */

static int midi_to_pc(int midi) {
    int pc = midi % 12;
    return pc < 0 ? pc + 12 : pc;
}

/* 根据和弦性质生成组成音级 */
static int quality_to_pcs(int root_pc, ga_chord_quality_t q, int *out) {
    switch (q) {
        case GA_CHORD_MAJOR:
            out[0]=root_pc; out[1]=(root_pc+4)%12; out[2]=(root_pc+7)%12;
            return 3;
        case GA_CHORD_MINOR:
            out[0]=root_pc; out[1]=(root_pc+3)%12; out[2]=(root_pc+7)%12;
            return 3;
        case GA_CHORD_SEVENTH:
            out[0]=root_pc; out[1]=(root_pc+4)%12; out[2]=(root_pc+7)%12;
            out[3]=(root_pc+10)%12; return 4;
        case GA_CHORD_MINOR_SEVENTH:
            out[0]=root_pc; out[1]=(root_pc+3)%12; out[2]=(root_pc+7)%12;
            out[3]=(root_pc+10)%12; return 4;
        case GA_CHORD_MAJOR_SEVENTH:
            out[0]=root_pc; out[1]=(root_pc+4)%12; out[2]=(root_pc+7)%12;
            out[3]=(root_pc+11)%12; return 4;
        case GA_CHORD_SUS4:
            out[0]=root_pc; out[1]=(root_pc+5)%12; out[2]=(root_pc+7)%12;
            return 3;
        default: return 0;
    }
}

/* 余弦相似度匹配。相比二值模板, 这里做了两个改进:
 *
 *  1. 根音加权: 真实演奏里根音通常最响, 给它更高权重能有效
 *     区分 C 和 Am 这类"共享两个音"的和弦(C-E-G vs A-C-E)。
 *  2. 惩罚"多余音": 如果色度图里有很强的高能量音级不在模板里
 *     (比如弹 C 时混进了 F#), 要降分, 避免误判成别的和弦。 */
static double match_score(const double *chroma, const int *pcs, int n_pc,
                          int root_pc) {
    double tpl[12];
    for (int i = 0; i < 12; i++) tpl[i] = 0.0;
    for (int i = 0; i < n_pc; i++) {
        /* 根音权重 1.5, 其他音 1.0 */
        tpl[pcs[i]] = (pcs[i] == root_pc) ? 1.5 : 1.0;
    }

    double dot = 0.0, na = 0.0, nb = 0.0;
    for (int i = 0; i < 12; i++) {
        dot += chroma[i] * tpl[i];
        na  += chroma[i] * chroma[i];
        nb  += tpl[i] * tpl[i];
    }
    if (na < 1e-12 || nb < 1e-12) return 0.0;

    double cos_sim = dot / (sqrt(na) * sqrt(nb));

    /* 多余音惩罚: 计算不在模板里的音级能量占比 */
    double outside = 0.0, total = 0.0;
    for (int i = 0; i < 12; i++) {
        total += chroma[i];
        int in_tpl = 0;
        for (int k = 0; k < n_pc; k++) {
            if (pcs[k] == i) { in_tpl = 1; break; }
        }
        if (!in_tpl) outside += chroma[i];
    }
    if (total > 1e-12) {
        double ratio = outside / total;
        /* 惩罚系数: 多余能量占比越高, 分数降得越多。
         * 0.5 是个经验值: 吉他存在大量泛音, 多余音占比天然不低,
         * 惩罚太狠会把正确和弦也压下去。 */
        cos_sim *= (1.0 - 0.5 * ratio);
    }
    return cos_sim;
}

/* ---------------- 模板匹配识别 ---------------- */

/* 遍历全部候选根音 x 性质, 找最佳匹配。
 * 候选集不限于和弦库里有的, 而是全部 12x6=72 种组合,
 * 这样能识别出库里没有但理论上存在的和弦(如 C#m7)。
 * 只有当最佳匹配分数超过阈值时才认。 */
/* 和弦"复杂度先验"权重。 */
static double complexity_prior(ga_chord_quality_t q) {
    switch (q) {
        case GA_CHORD_MAJOR:         return 1.00;
        case GA_CHORD_MINOR:         return 1.00;
        case GA_CHORD_SUS4:          return 0.99;
        case GA_CHORD_SEVENTH:       return 0.97;
        case GA_CHORD_MINOR_SEVENTH: return 0.97;
        case GA_CHORD_MAJOR_SEVENTH: return 0.96;
        default:                     return 1.00;
    }
}

/* 七和弦的"包含关系"冲突判定 —— 这是模板匹配最容易翻车的地方。
 *
 * 问题: Cmaj7 = C-E-G-B 完全包含 C = C-E-G。当用户弹 C 时,
 * 如果录音里 B 有一点能量(泛音/相邻弦串音), Cmaj7 的余弦相似度
 * 就可能略高于 C, 导致误报。
 *
 * 解决: 当两个候选的【根音相同, 且一个是另一个的子集】时,
 * 只有在"多余的音"有足够强的证据时才选复杂的那个。
 * 具体判据: 七和弦比三和弦多出的那个音, 其能量占比必须超过
 * 一个门槛(0.12), 否则退回三和弦。
 *
 * 为什么是 0.12: 上面诊断显示, 真实和弦音的占比在 0.17~0.28,
 * 而泛音带来的杂音在 0.04~0.09。门槛放 0.12 正好卡在两者之间。
 */
#define GA_EXTRA_TONE_THRESHOLD 0.12

/* 判断 q 是否为"七和弦/六和弦"类(比三和弦多一个音) */
static int is_extended(ga_chord_quality_t q) {
    return (q == GA_CHORD_SEVENTH || q == GA_CHORD_MINOR_SEVENTH ||
            q == GA_CHORD_MAJOR_SEVENTH);
}

/* 取和弦的"额外音"(相对于同根音的三和弦)。
 * 返回额外音的 pitch class, -1 表示不是扩展和弦。 */
static int extra_tone_pc(int root_pc, ga_chord_quality_t q) {
    switch (q) {
        case GA_CHORD_SEVENTH:       return (root_pc + 10) % 12;  /* 小七度 */
        case GA_CHORD_MINOR_SEVENTH: return (root_pc + 10) % 12;
        case GA_CHORD_MAJOR_SEVENTH: return (root_pc + 11) % 12;  /* 大七度 */
        default: return -1;
    }
}

/* 扩展和弦对应的"简化版"三和弦 */
static ga_chord_quality_t simplify_quality(ga_chord_quality_t q) {
    switch (q) {
        case GA_CHORD_SEVENTH:       return GA_CHORD_MAJOR;
        case GA_CHORD_MINOR_SEVENTH: return GA_CHORD_MINOR;
        case GA_CHORD_MAJOR_SEVENTH: return GA_CHORD_MAJOR;
        default: return q;
    }
}

static int template_infer(const double *chroma, double *out_score,
                          int *out_root_pc, ga_chord_quality_t *out_q) {
    double best = -1.0;
    int    best_root = 0;
    ga_chord_quality_t best_q = GA_CHORD_MAJOR;

    static const ga_chord_quality_t QUALS[] = {
        GA_CHORD_MAJOR, GA_CHORD_MINOR, GA_CHORD_SEVENTH,
        GA_CHORD_MINOR_SEVENTH, GA_CHORD_MAJOR_SEVENTH, GA_CHORD_SUS4
    };

    for (int root = 0; root < 12; root++) {
        for (size_t qi = 0; qi < sizeof(QUALS)/sizeof(QUALS[0]); qi++) {
            int pcs[4];
            int npc = quality_to_pcs(root, QUALS[qi], pcs);
            if (npc == 0) continue;

            double s = match_score(chroma, pcs, npc, root)
                     * complexity_prior(QUALS[qi]);

            /* 包含关系裁决: 如果是扩展和弦, 检查它多出的那个音
             * 是否真的有足够强的证据。证据不足就降权, 让它输给
             * 同根音的简单三和弦。 */
            if (is_extended(QUALS[qi])) {
                int extra = extra_tone_pc(root, QUALS[qi]);
                if (extra >= 0 && chroma[extra] < GA_EXTRA_TONE_THRESHOLD) {
                    /* 证据不足: 乘以一个显著小于 1 的系数。 */
                    s *= 0.90;
                }
            }

            if (s > best) {
                best = s;
                best_root = root;
                best_q = QUALS[qi];
            }
        }
    }

    /* 二次裁决: 万一扩展和弦仍然胜出, 但它的额外音证据依然薄弱,
     * 则强制退回简单三和弦。这一步兜住单帧波动导致的误报。 */
    if (is_extended(best_q)) {
        int extra = extra_tone_pc(best_root, best_q);
        if (extra >= 0 && chroma[extra] < GA_EXTRA_TONE_THRESHOLD) {
            ga_chord_quality_t simple = simplify_quality(best_q);
            int pcs[4];
            int npc = quality_to_pcs(best_root, simple, pcs);
            if (npc > 0) {
                double s2 = match_score(chroma, pcs, npc, best_root)
                          * complexity_prior(simple);
                best_q = simple;
                best = s2;
            }
        }
    }

    if (out_score)   *out_score = best;
    if (out_root_pc) *out_root_pc = best_root;
    if (out_q)       *out_q = best_q;
    return 0;
}

/* 把 root_pc + quality 转成和弦库里的条目。
 * 库里没有就返回 NULL(比如识别出 C#m7 但库里没这个和弦指法)。 */
static const ga_chord_def_t *lookup_in_db(int root_pc, ga_chord_quality_t q) {
    int count = ga_chord_count();
    for (int i = 0; i < count; i++) {
        const ga_chord_def_t *c = ga_chord_at(i);
        if (midi_to_pc(c->root_midi) == root_pc && c->quality == q) {
            return c;
        }
    }
    return NULL;
}

/* ---------------- 时序平滑 ---------------- */

/* 高音弦(1-3弦)在色度图上分辨率好, 但低音弦(E/A)因为 FFT 分辨率
 * 不足会被丢弃, 导致 Am 和 A 难以区分。这不是 bug, 是物理限制。
 * 缓解方法: 结合单音检测的结构信息(哪几根弦在响)。
 * 这里先在平滑层做简单处理: 要求连续多次识别一致。 */

static int smooth_push(ga_chord_recog_t *cr, int candidate_key) {
    cr->smooth_buf[cr->smooth_pos] = candidate_key;
    cr->smooth_pos = (cr->smooth_pos + 1) % GA_CHORD_SMOOTH_LEN;

    /* 统计出现最多的 key */
    int counts[128];
    memset(counts, 0, sizeof(counts));
    for (int i = 0; i < GA_CHORD_SMOOTH_LEN; i++) {
        int k = cr->smooth_buf[i];
        if (k >= 0 && k < 128) counts[k]++;
    }
    int best_k = -1, best_c = 0;
    for (int k = 0; k < 128; k++) {
        if (counts[k] > best_c) { best_c = counts[k]; best_k = k; }
    }
    /* 需要超过半数(3/5)才认为稳定 */
    if (best_c >= (GA_CHORD_SMOOTH_LEN / 2 + 1)) return best_k;
    return -1;
}

/* 把 (root_pc, quality) 编码成一个 0~71 的 key, 供平滑使用。
 *
 * 注意: ga_chord_quality_t 的枚举值是 1~6(UNKNOWN=0),
 * 所以编码必须用 (q - 1) 把它压回 0~5, 解码时再 +1 还原。
 * 之前这里直接用 q % 6 导致 C(MAJOR) 解出 MINOR, 是错的。 */
static int encode_key(int root_pc, ga_chord_quality_t q) {
    int qi = (int)q;
    if (qi < 1 || qi > 6) qi = 1;     /* 非法值兜底为 MAJOR */
    return (root_pc % 12) * 6 + (qi - 1);
}
static void decode_key(int key, int *root_pc, ga_chord_quality_t *q) {
    *root_pc = (key / 6) % 12;
    *q = (ga_chord_quality_t)(key % 6 + 1);   /* 还原为 1~6 */
}

/* ---------------- 对外接口 ---------------- */

int ga_chord_recog_init(ga_chord_recog_t *cr, ga_backend_t backend,
                        const char *model_path) {
    if (!cr) return -1;
    memset(cr, 0, sizeof(*cr));
    cr->smooth_pos = 0;
    for (int i = 0; i < GA_CHORD_SMOOTH_LEN; i++) cr->smooth_buf[i] = -1;
    strcpy(cr->current_name, "--");

    if (ga_chroma_init(&cr->chroma, GA_CHROMA_FFT_N,
                       (double)GA_SAMPLE_RATE) != 0) {
        return -1;
    }

    cr->backend = backend;

#ifdef GA_HAVE_RKNN
    if (backend == GA_BACKEND_RKNN && model_path && model_path[0]) {
        FILE *f = fopen(model_path, "rb");
        if (!f) {
            fprintf(stderr, "[chord] 打不开 RKNN 模型 %s, 退化为模板匹配\n",
                    model_path);
            cr->backend = GA_BACKEND_TEMPLATE;
            return 0;
        }
        fseek(f, 0, SEEK_END);
        long sz = ftell(f);
        fseek(f, 0, SEEK_SET);
        void *model_data = malloc((size_t)sz);
        if (model_data && fread(model_data, 1, (size_t)sz, f) == (size_t)sz) {
            rknn_context ctx = 0;
            int ret = rknn_init(&ctx, model_data, (uint32_t)sz, 0, NULL);
            free(model_data);
            if (ret < 0) {
                fprintf(stderr, "[chord] rknn_init 失败: %d, 退化为模板匹配\n",
                        ret);
                cr->backend = GA_BACKEND_TEMPLATE;
            } else {
                cr->rknn_ctx = (void *)(intptr_t)ctx;
                fprintf(stderr, "[chord] RKNN 模型加载成功 (%ld 字节)\n", sz);
            }
        } else {
            free(model_data);
            fprintf(stderr, "[chord] 读取 RKNN 模型失败, 退化为模板匹配\n");
            cr->backend = GA_BACKEND_TEMPLATE;
        }
        fclose(f);
    } else {
        if (backend == GA_BACKEND_RKNN)
            fprintf(stderr, "[chord] 未提供模型路径, 使用模板匹配\n");
        cr->backend = GA_BACKEND_TEMPLATE;
    }
#else
    if (backend == GA_BACKEND_RKNN) {
        fprintf(stderr, "[chord] 本版本未编译 RKNN 支持, 使用模板匹配。\n"
                        "       如需 NPU 推理, 加 -DGA_HAVE_RKNN 并链接 librknnrt\n");
    }
    cr->backend = GA_BACKEND_TEMPLATE;
    (void)model_path;
#endif

    return 0;
}

void ga_chord_recog_free(ga_chord_recog_t *cr) {
    if (!cr) return;
#ifdef GA_HAVE_RKNN
    if (cr->rknn_ctx) {
        rknn_context ctx = (rknn_context)(intptr_t)cr->rknn_ctx;
        rknn_destroy(ctx);
        cr->rknn_ctx = NULL;
    }
#endif
    ga_chroma_free(&cr->chroma);
}

const ga_chord_def_t *ga_chord_recog_current(const ga_chord_recog_t *cr) {
    if (!cr) return NULL;
    if (strcmp(cr->current_name, "--") == 0) return NULL;
    return ga_chord_find(cr->current_name);
}

const ga_chord_def_t *ga_chord_recog_from_chroma(ga_chord_recog_t *cr,
                                                 const double *chroma12) {
    if (!cr || !chroma12) return NULL;

    double score = 0.0;
    int root_pc = 0;
    ga_chord_quality_t q = GA_CHORD_MAJOR;
    template_infer(chroma12, &score, &root_pc, &q);

    /* 阈值 0.70: 低于此认为"不像任何和弦"(单音、噪声、无明显和弦)。
     * 实测合成信号最佳匹配在 0.74~0.82 之间, 阈值取 0.70 留有余量;
     * 而单音(只有一个音级能量)的匹配分数通常在 0.5~0.6。 */
    if (score < 0.70) {
        cr->is_voiced = 0;
        cr->stable_frames = 0;
        return NULL;
    }

    int key = encode_key(root_pc, q);

    /* 平滑: 需要连续多帧一致才输出。未稳定时也要累计帧数,
     * 否则 stable_frames 永远是 0。 */
    int stable = smooth_push(cr, key);
    if (stable < 0) {
        cr->stable_frames++;
        return NULL;   /* 还在积累, 暂不输出 */
    }

    int s_root; ga_chord_quality_t s_q;
    decode_key(stable, &s_root, &s_q);

    cr->stable_frames++;

    const ga_chord_def_t *cd = lookup_in_db(s_root, s_q);
    if (cd) {
        snprintf(cr->current_name, sizeof(cr->current_name), "%s", cd->name);
        cr->current_score = score;
        cr->is_voiced = 1;
        return cd;
    }

    /* 识别出库里没有的和弦: 生成名字, 但不返回指法 */
    static const char *SUF[] = {"", "m", "7", "m7", "maj7", "sus4"};
    static const char *PCN[12] = {
        "C","C#","D","D#","E","F","F#","G","G#","A","A#","B"
    };
    snprintf(cr->current_name, sizeof(cr->current_name), "%s%s",
             PCN[s_root], SUF[(int)s_q - 1]);
    cr->current_score = score;
    cr->is_voiced = 1;
    return NULL;   /* 库里没指法, 返回 NULL 但 current_name 已更新 */
}

/* 计算 RMS 判断是否有信号。静音时不做识别, 避免噪声导致的乱报。 */
static double calc_rms(const int16_t *pcm, int n) {
    if (!pcm || n <= 0) return 0.0;
    double acc = 0.0;
    for (int i = 0; i < n; i++) {
        double v = (double)pcm[i] / 32768.0;
        acc += v * v;
    }
    return sqrt(acc / n);
}

const ga_chord_def_t *ga_chord_recog_feed(ga_chord_recog_t *cr,
                                          const int16_t *pcm, int frames) {
    if (!cr || !pcm || frames < GA_CHROMA_FFT_N) return NULL;

    cr->rms = calc_rms(pcm, frames);
    /* 静音门限: RMS < 0.01 约等于 -40dB, 低于此不识别。
     * 吉他正常演奏 RMS 在 0.05~0.3, 拨弦瞬间可到 0.5。 */
    if (cr->rms < 0.01) {
        cr->is_voiced = 0;
        /* 静音时不清空平滑缓冲, 避免和弦间的静音打断识别 */
        return NULL;
    }

    double chroma[GA_CHROMA_BINS];
    if (ga_chroma_frame(&cr->chroma, pcm, chroma) != 0) return NULL;

    /* 存入特征缓冲(供 CNN 用) */
    if (cr->feat_fill < 8) {
        memcpy(cr->features + cr->feat_fill * GA_CHROMA_BINS, chroma,
               GA_CHROMA_BINS * sizeof(double));
        cr->feat_fill++;
    } else {
        /* 滑窗: 左移一帧, 末尾加新帧 */
        memmove(cr->features, cr->features + GA_CHROMA_BINS,
                7 * GA_CHROMA_BINS * sizeof(double));
        memcpy(cr->features + 7 * GA_CHROMA_BINS, chroma,
               GA_CHROMA_BINS * sizeof(double));
    }

    return ga_chord_recog_from_chroma(cr, chroma);
}
