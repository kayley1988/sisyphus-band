#!/usr/bin/env python3
"""Mic level meter - speak/clap at the board and watch the bars.

Usage:  python3 -u mic_level_test.py [seconds, default 12]

Onboard RK809-5 mic -> ALSA card0 hw:0,0 -> sounddevice 16kHz mono.
Stop guitar.service first (it holds the capture device exclusively).
"""
import sys
import time

import numpy as np
import sounddevice as sd

SR = 16000
DUR = float(sys.argv[1]) if len(sys.argv) > 1 else 12.0
BLK = 512          # 32 ms per block
EVERY = 10         # print every ~320 ms
BARW = 44
FS = 0.25          # full-scale RMS reference (~ -12 dBFS)


def bar(v):
    n = int(min(v / FS, 1.0) * BARW)
    return "#" * n + "." * (BARW - n)


def main():
    print("Capture %.0fs @ %dHz  -- speak or clap now\n" % (DUR, SR))
    t0 = time.time()
    pk = 0.0
    ss = 0.0
    c = 0
    i = 0
    with sd.InputStream(samplerate=SR, channels=1, dtype="int16", blocksize=BLK) as st:
        while time.time() - t0 < DUR:
            d, _ov = st.read(BLK)
            x = d[:, 0].astype(np.float32) / 32768.0
            rms = float(np.sqrt((x * x).mean()))
            peak = float(np.abs(x).max())
            if peak > pk:
                pk = peak
            ss += rms
            c += 1
            i += 1
            if i % EVERY == 0:
                print("[%5.1fs] RMS=%6.1f peak=%6.1f |%s|"
                      % (time.time() - t0, rms * 32768, peak * 32768, bar(rms)))
    avg = ss / max(c, 1)
    db = 20 * np.log10(avg + 1e-9)
    print("\nAvg RMS=%.0f (%.1f dBFS)  Global peak=%.0f"
          % (avg * 32768, db, pk * 32768))
    # 注意: 硬件增益已拉满(MIC Boost +30dB / ADC PGA +27dB), 静默本底就有
    # RMS≈600 / peak≈1500, 所以不能用旧的"<200 才算太低"阈值判读。
    avg_rms = avg * 32768
    if avg_rms > 3000:
        print("Verdict: mic OK - human voice detected")
    elif avg_rms > 800:
        print("Verdict: weak signal - check wiring / speak closer")
    else:
        print("Verdict: NO voice - mic not wired? (noise floor only)")


main()
