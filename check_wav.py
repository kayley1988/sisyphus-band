"""读一段 wav 报电平：用于判断麦克风是否真在拾音。
用法: python3 check_wav.py /tmp/mictest.wav
"""
import sys
import wave

import numpy as np

p = sys.argv[1] if len(sys.argv) > 1 else "/tmp/mictest.wav"
w = wave.open(p)
d = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)
print("file=%s frames=%d rate=%d" % (p, w.getnframes(), w.getframerate()))
print("avg=%.1f peak=%d" % (float(np.abs(d).mean()), int(np.abs(d).max())))
print("判定: avg<100 视为无声(麦克风没接/静音)；正常说话 500~20000")
