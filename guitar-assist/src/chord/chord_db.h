/*
 * chord_db.h - 和弦库
 *
 * 设计取舍(重要):
 *   这里存的是【指法数据】, 不是音频识别。指法显示是纯静态数据 + 渲染,
 *   零算力压力, 对无障碍场景价值最高(大字指法图 + 语音播报)。
 *   实时和弦识别是另一条链路, 见 src/chord/chord_recog.*。
 *
 * 数据结构选择:
 *   用内置静态数组而不是运行时解析 JSON。原因:
 *   1. RK3566 上不想为几百 KB 数据引入 cJSON 依赖;
 *   2. 静态数组在编译期就能做越界检查, 嵌入式更安全;
 *   3. 需要增删和弦时改这个文件重新编译即可, 比维护外部资源文件简单。
 *   同时提供 chord_db_dump_json(), 需要给上层 Web/服务用时能导出。
 */
#ifndef GA_CHORD_DB_H
#define GA_CHORD_DB_H

#include "guitar_common.h"

/* 按名称查询, 如 "Am" "C" "F#m7"。找不到返回 NULL。大小写不敏感。
 * 支持省略 "m", 如 "Aminor" 也认。 */
const ga_chord_def_t *ga_chord_find(const char *name);

/* 遍历全部和弦 */
int ga_chord_count(void);
const ga_chord_def_t *ga_chord_at(int index);

/* 生成一段人能读的指法描述, 用于语音播报/TTS 文本。
 * 例: "A 小和弦, 从六弦到一弦: 不弹, 空弦, 二品, 二品, 一品, 空弦"
 * out 至少 256 字节。 */
void ga_chord_describe(const ga_chord_def_t *c, int string_first /*1=从6弦开始*/,
                       char *out, size_t out_len);

/* 导出为 JSON 字符串(内部 malloc), 调用者负责 free。
 * 用于把和弦库交给上层 UI / 网络服务。 */
char *ga_chord_dump_json(void);

#endif /* GA_CHORD_DB_H */
