#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""patch_wake_and_boot.py

① 唤醒词：小爱同学 → 小金鱼
   - models/kws/keywords.txt      （KWS 真正用的音素词表）
   - models/kws/keywords_raw.txt  （UI 可选词表）
   - ai/constants.py  load_wake_words() 里硬编码的小爱同学
   - ai/context.py / main.py / screen_functions.py 的默认值
   - config/ai_config.json 持久化
② 开机欢迎语：改为**离线神经 TTS 优先**（models/tts_piper_xiaoya 小雅），
   失败才回退 edge-tts（在线）→ espeak-ng（机械音）。

只做精确片段替换；所有被改文件先备份为 *.bak_<ts>_wake。
"""
import ast
import io
import json
import os
import shutil
import time

APP = "/root/guitarist_app"
SRC = os.path.join(APP, "src", "guitarist")
STAMP = time.strftime("%Y%m%d_%H%M")
NEW = "小金鱼"

report = []


def note(s):
    report.append(s)
    print(s, flush=True)


def backup(path):
    if os.path.exists(path):
        b = "%s.bak_%s_wake" % (path, STAMP)
        if not os.path.exists(b):
            shutil.copy2(path, b)
        return b
    return None


def edit(path, pairs):
    if not os.path.exists(path):
        note("MISS  %s" % path)
        return
    backup(path)
    s = io.open(path, encoding="utf-8").read()
    hit, miss = 0, []
    for a, b in pairs:
        if a in s:
            s = s.replace(a, b)
            hit += 1
        else:
            miss.append(a[:60])
    io.open(path, "w", encoding="utf-8").write(s)
    note("EDIT  %-34s 命中 %d/%d" % (os.path.relpath(path, APP), hit, len(pairs)))
    for m in miss:
        note("      ! 未命中: %r" % m)


# ─────────── ① KWS 关键词 ───────────
def redo_keywords(path, new_line, tail_old):
    if not os.path.exists(path):
        note("MISS  %s" % path)
        return
    backup(path)
    lines = io.open(path, encoding="utf-8").read().splitlines()
    kept = [l.strip() for l in lines
            if l.strip() and "小爱同学" not in l and NEW not in l]
    out = [new_line] + kept
    io.open(path, "w", encoding="utf-8").write("\n".join(out) + "\n")
    names = [x.split("@")[-1].strip() for x in out]
    note("EDIT  %-34s 词表 → %s" % (os.path.relpath(path, APP), " / ".join(names)))


redo_keywords(os.path.join(APP, "models/kws/keywords.txt"),
              "x iǎo j īn y ú @小金鱼", None)
redo_keywords(os.path.join(APP, "models/kws/keywords_raw.txt"),
              NEW, None)

# ─────────── ② 代码硬编码 ───────────
edit(os.path.join(SRC, "ai/constants.py"), [
    ('words = ["小爱同学", "你好问问"', 'words = ["小金鱼", "你好问问"'),
    ('if "小爱同学" in words:\n        words.remove("小爱同学")\n    words.insert(0, "小爱同学")',
     'if "小金鱼" in words:\n        words.remove("小金鱼")\n    words.insert(0, "小金鱼")'),
])

edit(os.path.join(SRC, "ai/context.py"), [
    ('"wake_word": "小爱同学",', '"wake_word": "小金鱼",'),
])

edit(os.path.join(SRC, "main.py"), [
    ('唤醒词「小爱同学」→ 语音控制', '唤醒词「小金鱼」→ 语音控制'),
    ('WAKE_WORDS = ["小爱同学"]', 'WAKE_WORDS = ["小金鱼"]'),
    ('DEFAULT_WAKE_WORD = "小爱同学"', 'DEFAULT_WAKE_WORD = "小金鱼"'),
    ('请说小爱同学唤醒我', '请说小金鱼唤醒我'),
])

edit(os.path.join(SRC, "screen_functions.py"), [
    ('_wake_words = ["小爱同学"]', '_wake_words = ["小金鱼"]'),
    ('_ai_ctx.get_config("wake_word", "小爱同学")', '_ai_ctx.get_config("wake_word", "小金鱼")'),
])

# ─────────── ③ 开机欢迎语：离线优先 ───────────
OFFLINE_BLOCK = '''            # ★ 2026-10-11：开机欢迎语改成**离线优先**。
            #   板上已有神经 TTS（models/tts_piper_xiaoya「小雅」，sherpa-onnx VITS），
            #   旧逻辑只认 edge-tts（在线），一断网就掉进 espeak-ng 机械音。
            if (ai_ctx.get_config("tts_engine", "sherpa") if ai_ctx else "sherpa") != "espeak":
                try:
                    from ai.tts_engine import SherpaTTS
                    _wp, _dur = SherpaTTS().synthesize_to_wav(text, "/tmp/welcome.wav")
                    if _wp and os.path.exists(_wp) and os.path.getsize(_wp) > 4096:
                        audio = ("wav", _wp)
                        _log.info("开机欢迎语: 离线神经 TTS 合成完成 %.2fs", _dur)
                except Exception as _e:                       # noqa: BLE001
                    _log.warning("离线 TTS 不可用，回退在线/机械音: %s", _e)
            try:
                mp3_path = "/tmp/welcome.mp3"'''

edit(os.path.join(SRC, "main.py"), [
    ('            audio = None\n            try:\n                mp3_path = "/tmp/welcome.mp3"',
     '            audio = None\n' + OFFLINE_BLOCK),
    ('                if os.path.exists(mp3_path) and os.path.getsize(mp3_path) > 0:\n'
     '                    audio = ("edge_tts", mp3_path)',
     '                if audio is None and os.path.exists(mp3_path) and os.path.getsize(mp3_path) > 0:\n'
     '                    audio = ("edge_tts", mp3_path)'),
    ('                        _, path = audio\n                        for player in ("mpg123", "ffplay"):',
     '                        kind, path = audio\n'
     '                        if kind == "wav":\n'
     '                            subprocess.run(["aplay", "-q", path],\n'
     '                                           timeout=40, capture_output=True)\n'
     '                            return\n'
     '                        for player in ("mpg123", "ffplay"):'),
])

# ─────────── ④ 持久化配置 ───────────
cfg = os.path.join(APP, "config/ai_config.json")
if os.path.exists(cfg):
    backup(cfg)
    try:
        d = json.load(io.open(cfg, encoding="utf-8"))
        d["wake_word"] = NEW
        d.setdefault("tts_engine", "sherpa")
        io.open(cfg, "w", encoding="utf-8").write(
            json.dumps(d, ensure_ascii=False, indent=2) + "\n")
        note("EDIT  %-34s wake_word=%s tts_engine=%s"
             % ("config/ai_config.json", d["wake_word"], d["tts_engine"]))
    except Exception as e:
        note("FAIL  ai_config.json: %s" % e)

# ─────────── ⑤ 语法自检 ───────────
bad = []
for p in ("main.py", "ai/constants.py", "ai/context.py", "screen_functions.py"):
    fp = os.path.join(SRC, p)
    try:
        ast.parse(io.open(fp, encoding="utf-8").read())
    except SyntaxError as e:
        bad.append("%s: line %s %s" % (p, e.lineno, e.msg))
note("语法自检: " + ("全部通过 ✔" if not bad else "!! " + " / ".join(bad)))
note("DONE")
