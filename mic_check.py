import sys
try:
    import sounddevice as sd
except Exception as e:
    print("IMPORT_ERR", type(e).__name__, e)
    sys.exit(0)

try:
    devs = sd.query_devices()
    print("total_devices:", len(devs))
    for i, d in enumerate(devs):
        if d.get("max_input_channels", 0) > 0:
            print("IN", i, d.get("name"), d.get("max_input_channels"))
    try:
        print("default_input:", sd.default.device[0])
    except Exception as e:
        print("default_input_err", e)
except Exception as e:
    print("QUERY_ERR", type(e).__name__, e)

# 试着真开一次流（1 秒），这是最硬的判据
try:
    import numpy as np
    data = sd.rec(int(16000 * 0.5), samplerate=16000, channels=1, dtype="int16")
    sd.wait()
    amp = int(abs(data).max()) if data.size else -1
    print("REC_OK frames=%d max_amp=%d" % (len(data), amp))
except Exception as e:
    print("REC_ERR", type(e).__name__, e)

# vosk 模型在不在
import os
for p in ["/root/guitarist_app/models", "/root/vosk-model", "/opt/vosk-model"]:
    if os.path.isdir(p):
        print("MODELDIR", p, os.listdir(p)[:8])
