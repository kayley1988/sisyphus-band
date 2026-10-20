# -*- coding: utf-8 -*-
"""Analyze 8-channel capture WAVs: per-channel RMS / peak / spectrum centroid."""
import wave
import numpy as np
import os

BASE = "E:/音乐/档3实现/_preview"


def analyze(path):
    if not os.path.exists(path):
        print("MISSING:", path)
        return
    with wave.open(path, "rb") as w:
        n, ch, sr = w.getnframes(), w.getnchannels(), w.getframerate()
        raw = w.readframes(n)
    data = np.frombuffer(raw, dtype=np.int16).reshape(-1, ch).astype(np.float64)
    dur = n / float(sr)
    print("=" * 72)
    print("%s  %d frames  %d ch  %d Hz  %.1fs" % (os.path.basename(path), n, ch, sr, dur))
    print("-" * 72)
    # spectrum centroid (only for ch0..3 to keep output short)
    for c in range(ch):
        a = data[:, c]
        rms = float(np.sqrt(np.mean(a * a)))
        pk = float(np.max(np.abs(a)))
        # spectrum
        seg = a[: min(len(a), sr * 4)]
        if len(seg) > 1024:
            win = np.hanning(len(seg))
            sp = np.abs(np.fft.rfft(seg * win))
            fr = np.fft.rfftfreq(len(seg), 1.0 / sr)
            total = sp.sum() + 1e-9
            cent = float((fr * sp).sum() / total)
            band_low = float(sp[(fr >= 20) & (fr < 300)].sum() / total)
            band_mid = float(sp[(fr >= 300) & (fr < 3400)].sum() / total)
            band_hi = float(sp[fr >= 3400].sum() / total)
            print("  ch%d rms=%8.1f peak=%8.0f  centroid=%6.0fHz  <300Hz=%.2f 300-3k4=%.2f >3k4=%.2f"
                  % (c, rms, pk, cent, band_low, band_mid, band_hi))
        else:
            print("  ch%d rms=%8.1f peak=%8.0f" % (c, rms, pk))
    # cross-channel identity check (is ch N just a copy of ch0?)
    print("-" * 72)
    print("  ch0 vs others (normalized correlation):")
    a0 = data[:, 0] - data[:, 0].mean()
    n0 = np.linalg.norm(a0) + 1e-9
    for c in range(1, ch):
        ac = data[:, c] - data[:, c].mean()
        nc = np.linalg.norm(ac) + 1e-9
        corr = float(np.dot(a0, ac) / (n0 * nc))
        print("    ch0-ch%d: %+.3f" % (c, corr))


for f in ("ch8_main.wav", "ch8_hf.wav", "kws_probe.wav"):
    analyze(os.path.join(BASE, f))
