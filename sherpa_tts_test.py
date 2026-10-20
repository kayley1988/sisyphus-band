# sherpa-onnx 离线中文 TTS 冒烟测试：合成一句中文 -> 存 wav -> 打印信息
# 用法（板上）: python3 -u /tmp/sherpa_tts_test.py [模型目录] [输出wav]
import array
import glob
import os
import sys
import time
import wave

MODEL_DIR = sys.argv[1] if len(sys.argv) > 1 else \
    "/root/guitarist_app/models/tts/vits-zh-hf-fanchen-C"
OUT = sys.argv[2] if len(sys.argv) > 2 else "/tmp/sherpa_tts.wav"
THREADS = int(sys.argv[3]) if len(sys.argv) > 3 else 2
TEXT = "你好，我是拨云。今天天气不错，我来弹一首歌给你听。"

print("=== 0) 模型目录 ===")
print(" ", MODEL_DIR, "exists=", os.path.isdir(MODEL_DIR))
if os.path.isdir(MODEL_DIR):
    for n in sorted(os.listdir(MODEL_DIR)):
        p = os.path.join(MODEL_DIR, n)
        sz = os.path.getsize(p) if os.path.isfile(p) else 0
        print("   %-42s %s" % (n, ("%.1fMB" % (sz / 1048576.0)) if sz else "<dir>"))

try:
    import sherpa_onnx
except Exception as e:
    print("sherpa_onnx import FAIL:", e)
    sys.exit(1)

print("=== 1) API 字段 ===")
print("  VitsModelConfig:", [a for a in dir(sherpa_onnx.OfflineTtsVitsModelConfig)
                              if not a.startswith("_")])
print("  TtsConfig:", [a for a in dir(sherpa_onnx.OfflineTtsConfig)
                       if not a.startswith("_")])

onnx = glob.glob(os.path.join(MODEL_DIR, "*.onnx"))
lex = os.path.join(MODEL_DIR, "lexicon.txt")
tok = os.path.join(MODEL_DIR, "tokens.txt")
dict_dir = os.path.join(MODEL_DIR, "dict")
fsts = [p for p in (os.path.join(MODEL_DIR, f) for f in
                    ("phone.fst", "date.fst", "number.fst", "new_heteronym.fst"))
        if os.path.exists(p)]
print("=== 2) 关键文件 ===")
print("  onnx:", onnx)
print("  lexicon:", os.path.exists(lex), "| tokens:", os.path.exists(tok),
      "| dict_dir:", os.path.isdir(dict_dir))
print("  rule fsts:", [os.path.basename(f) for f in fsts])
if not onnx:
    print("!! 没找到 .onnx，无法继续")
    sys.exit(1)

print("=== 3) 构建配置 ===")
kwargs = dict(model=onnx[0], tokens=tok)
if os.path.exists(lex):
    kwargs["lexicon"] = lex
if os.path.isdir(dict_dir):
    kwargs["dict_dir"] = dict_dir
try:
    vits = sherpa_onnx.OfflineTtsVitsModelConfig(**kwargs)
except TypeError as e:
    print("  构造失败(去掉 dict_dir 重试):", e)
    kwargs.pop("dict_dir", None)
    vits = sherpa_onnx.OfflineTtsVitsModelConfig(**kwargs)

cfg_kw = dict(model=sherpa_onnx.OfflineTtsModelConfig(
    vits=vits, num_threads=THREADS, debug=False, provider="cpu"))
try:
    cfg = sherpa_onnx.OfflineTtsConfig(rule_fsts=",".join(fsts), **cfg_kw)
except TypeError as e:
    print("  配置不支持 rule_fsts，忽略:", e)
    cfg = sherpa_onnx.OfflineTtsConfig(**cfg_kw)

t0 = time.time()
tts = sherpa_onnx.OfflineTts(cfg)
print("  引擎加载 %.1fs  sample_rate=%d  num_speakers=%d"
      % (time.time() - t0, tts.sample_rate, tts.num_speakers))

print("=== 4) 合成 ===")
print("  文本:", TEXT)
t0 = time.time()
audio = tts.generate(TEXT, sid=0, speed=1.0)
dur = len(audio.samples) / float(audio.sample_rate) if audio.sample_rate else 0
print("  合成 %.2fs  音频时长 %.2fs  sr=%d  n=%d"
      % (time.time() - t0, dur, audio.sample_rate, len(audio.samples)))

with wave.open(OUT, "wb") as f:
    f.setnchannels(1)
    f.setsampwidth(2)
    f.setframerate(audio.sample_rate)
    f.writeframes(array.array("h", [int(max(-1.0, min(1.0, x)) * 32767)
                                    for x in audio.samples]).tobytes())
print("  已保存:", OUT, os.path.getsize(OUT), "bytes")
print("=== done ===")
