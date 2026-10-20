# 探查 ONNX Runtime 可用后端 + 不同后端/线程下的合成熟度
# 用法（板上）: python3 -u /tmp/sherpa_probe2.py [模型目录]
import os
import sys
import time

MODEL_DIR = sys.argv[1] if len(sys.argv) > 1 else "/root/guitarist_app/models/tts_piper_xiaoya"
SHORT = "我在呢"
LONG = "你好，我是拨云。今天天气不错，我来弹一首歌给你听。"

print("=== ONNX Runtime 可用后端 ===")
try:
    import onnxruntime as ort
    print("  ort", ort.__version__, "->", ort.get_available_providers())
except Exception as e:
    print("  ort 查询失败:", e)

import sherpa_onnx

lex = os.path.join(MODEL_DIR, "lexicon.txt")
tok = os.path.join(MODEL_DIR, "tokens.txt")
onnx = [os.path.join(MODEL_DIR, f) for f in os.listdir(MODEL_DIR) if f.endswith(".onnx")][0]
fsts = [os.path.join(MODEL_DIR, f) for f in ("phone.fst", "date.fst", "number.fst")
        if os.path.exists(os.path.join(MODEL_DIR, f))]


def build(provider, threads):
    vits = sherpa_onnx.OfflineTtsVitsModelConfig(
        model=onnx, tokens=tok, lexicon=lex)
    cfg = sherpa_onnx.OfflineTtsConfig(
        model=sherpa_onnx.OfflineTtsModelConfig(
            vits=vits, num_threads=threads, debug=False, provider=provider),
        rule_fsts=",".join(fsts))
    return sherpa_onnx.OfflineTts(cfg)


for provider, threads in [("cpu", 4), ("xnnpack", 4), ("cpu", 2), ("cpu", 6)]:
    tag = "%-8s x%d" % (provider, threads)
    try:
        t0 = time.time()
        tts = build(provider, threads)
        load = time.time() - t0
        if provider == "xnnpack":
            print("  [%s] !! 后端似乎未生效（sherpa 可能静默回退 cpu）" % tag)
        for name, text in (("短", SHORT), ("长", LONG)):
            t0 = time.time()
            a = tts.generate(text, sid=0, speed=1.0)
            el = time.time() - t0
            dur = len(a.samples) / float(a.sample_rate)
            print("  [%s] %s句: 合成 %.2fs / 音频 %.2fs  = %.2fx 实时   (加载 %.1fs)"
                  % (tag, name, el, dur, el / dur if dur else 0, load))
        del tts
    except Exception as e:
        print("  [%s] 失败: %s" % (tag, repr(e)[:150]))
print("=== done ===")
