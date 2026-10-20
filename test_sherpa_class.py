# 验证接入后的 SherpaTTS 类（走项目真实代码路径）
import os
import sys
import time

sys.path.insert(0, "/root/guitarist_app/src/guitarist")

print("=== 导入 ai.tts_engine.SherpaTTS ===")
try:
    from ai.tts_engine import SherpaTTS
    print("  import OK")
except Exception as e:
    print("  import FAIL:", type(e).__name__, e)
    sys.exit(1)

t = SherpaTTS()
print("  model_dir:", t.model_dir, "exists=", os.path.isdir(t.model_dir))
print("  threads:", t.num_threads, "sid:", t.sid, "speed:", t.speed)

t0 = time.time()
ok = t.warmup()
print("  warmup:", ok, "  %.1fs  sr=%d" % (time.time() - t0, t.sample_rate))

t0 = time.time()
path, dur = t.synthesize_to_wav("你好，我是拨云，离线语音引擎接入成功。",
                                "/tmp/class_tts.wav")
print("  synthesize_to_wav: %.2fs  音频 %.2fs  -> %s (%d bytes)"
      % (time.time() - t0, dur, path, os.path.getsize(path)))
print("=== OK ===")
