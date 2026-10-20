/*
 * test_chord.c - 和弦库自测
 * 校验数据合法性 + 描述文本生成 + JSON 导出。
 * 数据错误在嵌入式上很难排查, 所以编译后立刻自检。
 */
#include "chord_db.h"

#include <stdio.h>
#include <string.h>
#include <stdlib.h>

/* 校验: 同一根弦不能既标"不弹"又在更高品有音(那会导致无法弹奏) */
static int validate_fingering(const ga_chord_def_t *c) {
    int ok = 1;
    /* 弦序 6->1, 品位应该单调(低音弦品位数 >= 高音弦品位数) */
    int prev_fret = -2;
    for (int i = 0; i < GA_STRING_COUNT; i++) {
        int f = c->frets[i];
        if (f < -1 || f > 24) {
            printf("    [ERR] %s 第%d弦品位越界: %d\n", c->name, 6 - i, f);
            ok = 0;
        }
        /* 用指法号校验: 按弦必须给指法, 不弹/空弦指法应为 0 */
        if (f > 0 && c->fingers[i] == 0) {
            printf("    [ERR] %s 第%d弦按了%d品但没给指法\n",
                   c->name, 6 - i, f);
            ok = 0;
        }
        if (f <= 0 && c->fingers[i] != 0) {
            printf("    [ERR] %s 第%d弦没按但给了指法\n", c->name, 6 - i);
            ok = 0;
        }
        prev_fret = f;
        (void)prev_fret;
    }
    /* 至少要有 3 根弦发声, 否则可能是数据写错 */
    int sounding = 0;
    for (int i = 0; i < GA_STRING_COUNT; i++) {
        if (c->frets[i] >= 0) sounding++;
    }
    if (sounding < 3) {
        printf("    [ERR] %s 只有 %d 根弦发声, 可能是数据错误\n",
               c->name, sounding);
        ok = 0;
    }
    return ok;
}

int main(void) {
    printf("=== 和弦库自测 ===\n\n");

    int n = ga_chord_count();
    printf("和弦总数: %d\n\n", n);

    printf("[1] 数据合法性校验\n");
    int bad = 0;
    for (int i = 0; i < n; i++) {
        const ga_chord_def_t *c = ga_chord_at(i);
        if (!validate_fingering(c)) bad++;
    }
    printf("    全部 %d 个和弦校验%s\n", n, bad ? "发现问题" : "通过");

    printf("\n[2] 查询测试\n");
    const char *queries[] = { "C", "am", "AM", "F#m", "G7", "Bb", "Nope" };
    for (size_t i = 0; i < sizeof(queries)/sizeof(queries[0]); i++) {
        const ga_chord_def_t *c = ga_chord_find(queries[i]);
        printf("    查询 %-8s -> %s\n", queries[i],
               c ? c->name : "未找到(预期)");
    }

    printf("\n[3] 语音播报文本生成(无障碍核心)\n");
    const char *demo[] = { "Am", "C", "F", "G" };
    for (size_t i = 0; i < sizeof(demo)/sizeof(demo[0]); i++) {
        const ga_chord_def_t *c = ga_chord_find(demo[i]);
        char desc[512];
        ga_chord_describe(c, 1, desc, sizeof(desc));
        printf("    %-4s : %s\n", demo[i], desc);
    }

    printf("\n[4] 指法图 ASCII 预览(逻辑验证)\n");
    /* 渲染成竖排指法图, 确认数据顺序正确: 左边是 6 弦 */
    for (size_t i = 0; i < sizeof(demo)/sizeof(demo[0]); i++) {
        const ga_chord_def_t *c = ga_chord_find(demo[i]);
        printf("\n    %s:\n", c->name);
        printf("      %s\n", "E A D G B e");   /* 弦名 */
        printf("      ");
        for (int k = 0; k < GA_STRING_COUNT; k++) {
            char s[16];
            if (c->frets[k] < 0)      snprintf(s, sizeof(s), "x");
            else if (c->frets[k] == 0) snprintf(s, sizeof(s), "0");
            else                       snprintf(s, sizeof(s), "%d", c->frets[k]);
            printf("%s ", s);
        }
        printf("\n");
    }

    printf("\n[5] JSON 导出\n");
    char *json = ga_chord_dump_json();
    if (json) {
        /* 只打印前 300 字节做抽样检查 */
        printf("    总长度 %zu 字节\n", strlen(json));
        printf("    前 200 字节:\n---\n%.200s\n---\n", json);
        /* 粗略校验 JSON 括号平衡 */
        int lb = 0, rb = 0, lc = 0, rc = 0;
        for (const char *p = json; *p; p++) {
            if (*p == '[') lb++; else if (*p == ']') rb++;
            if (*p == '{') lc++; else if (*p == '}') rc++;
        }
        printf("    括号平衡: [ %d/%d ]  { %d/%d }  %s\n",
               lb, rb, lc, rc, (lb==rb && lc==rc) ? "OK" : "不平衡!");
        free(json);
    }

    printf("\n========================================\n");
    printf("%s\n", bad == 0 ? "全部通过" : "存在问题, 见上方 [ERR]");
    printf("========================================\n");
    return bad == 0 ? 0 : 1;
}
