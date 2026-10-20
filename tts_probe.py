# Edge-TTS 可用性探测：库是否安装 / 播放器是否齐 / 在线合成能否成功
# 用法（板上）: python3 -u /tmp/tts_probe.py
import asyncio
import os
import shutil
import sys
import time

print("=== 1) edge_tts 是否安装 ===")
try:
    import edge_tts
    print("edge_tts OK", getattr(edge_tts, "__version__", "?"))
except Exception as e:
    print("edge_tts MISSING:", e)
    sys.exit(0)

print("=== 2) 播放器是否齐 ===")
for n in ("mpg123", "ffplay", "aplay"):
    print("  %-8s -> %s" % (n, shutil.which(n) or "NOT FOUND"))

print("=== 3) 在线合成测试（zh-CN-XiaoxiaoNeural）===")


async def synth(text, voice, path):
    c = edge_tts.Communicate(text, voice)
    n = 0
    with open(path, "wb") as f:
        async for ch in c.stream():
            if ch["type"] == "audio":
                f.write(ch["data"])
                n += len(ch["data"])
    return n


t = time.time()
try:
    n = asyncio.run(synth("你好，我是拨云，这是一次在线语音合成测试。",
                          "zh-CN-XiaoxiaoNeural", "/tmp/edge_test.mp3"))
    print("SYNTH OK bytes=%d  %.1fs  -> /tmp/edge_test.mp3" % (n, time.time() - t))
except Exception as e:
    print("SYNTH FAIL", type(e).__name__, repr(e)[:200])

print("=== 4) 可用中文嗓音（前 12 个）===")
try:
    voices = asyncio.run(edge_tts.list_voices())
    zh = [v["ShortName"] for v in voices if v["Locale"].startswith("zh-")]
    for v in zh[:12]:
        print("  ", v)
except Exception as e:
    print("list_voices FAIL", repr(e)[:200])
print("=== done ===")
