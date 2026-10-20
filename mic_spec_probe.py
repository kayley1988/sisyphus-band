#!/usr/bin/env python3
"""Mic spectrum probe: does the soldered electret mic pick up voice?

Why not just RMS: a floating/unconnected input can show a high RMS too, but
its energy sits mostly ABOVE 4 kHz (amplifier self-noise / pickup). Real speech
puts most of its energy in 300-3400 Hz. So we compare BOTH the level and the
voice-band ratio between a quiet baseline and actual speaking.

Two hardware-gain settings are measured in one run (mid, then full), so we can
tell "mic dead" apart from "mic alive but needs a different gain".

Usage:  python3 -u mic_spec_probe.py [quiet=10] [speak_each=12]
Run with guitar.service stopped (it owns the capture device).
Pure ASCII output (board stdout is not UTF-8).
"""
import subprocess
import sys
import time

import numpy as np
import sounddevice as sd

SR = 16000
BLK = 1024
FULL = 32768.0
QUIET = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0
SPEAK = float(sys.argv[2]) if len(sys.argv) > 2 else 12.0


def sh(*args):
    subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def set_gain(boost, pga):
    sh("amixer", "-c", "0", "sset", "MIC Boost Gain", "%d,%d" % (boost, boost))
    sh("amixer", "-c", "0", "sset", "ADC PGA Gain", "%d,%d" % (pga, pga))
    sh("amixer", "-c", "0", "sset", "ADC Capture Volume", "255,255")


def set_path(name):
    sh("amixer", "-c", "0", "sset", "Capture MIC Path", name)


def band_info(block):
    x = block.astype(np.float32) / FULL
    x = x - x.mean()
    w = np.hanning(x.size)
    mag = np.abs(np.fft.rfft(x * w))
    f = np.fft.rfftfreq(x.size, 1.0 / SR)
    total = mag.sum() + 1e-9
    voice = float(mag[(f >= 300) & (f <= 3400)].sum() / total)
    high = float(mag[f > 4000].sum() / total)
    cen = float((mag * f).sum() / total)
    return voice, high, cen


def collect(dur, tag):
    rms_l, pk_l, vo_l, hi_l, ce_l = [], [], [], [], []
    t0 = time.time()
    last = -9.0
    with sd.InputStream(samplerate=SR, channels=1, dtype="int16",
                        blocksize=BLK) as st:
        while time.time() - t0 < dur:
            d, _ = st.read(BLK)
            b = d[:, 0].copy()
            x = b.astype(np.float32) / FULL
            rms_l.append(float(np.sqrt((x * x).mean())) * FULL)
            pk_l.append(float(np.abs(x).max()) * FULL)
            v, h, c = band_info(b)
            vo_l.append(v)
            hi_l.append(h)
            ce_l.append(c)
            el = time.time() - t0
            if el - last >= 2.0:
                last = el
                print("[%s t=%4.1fs] rms=%7.1f pk=%7.1f voice=%.2f high=%.2f cen=%5.0fHz"
                      % (tag, el, rms_l[-1], pk_l[-1], vo_l[-1], hi_l[-1], ce_l[-1]))
                sys.stdout.flush()
    return {
        "rms": float(np.mean(rms_l)),
        "pk": float(np.max(pk_l)),
        "voice": float(np.mean(vo_l)),
        "high": float(np.mean(hi_l)),
        "cen": float(np.mean(ce_l)),
    }


def main():
    print("=" * 72)
    print("MIC SPECTRUM PROBE")
    print("  PHASE 1 (first %.0f s) : STAY QUIET, do not speak" % QUIET)
    print("  PHASE 2 (next  %.0f s) : SPEAK LOUDLY 'xiao-ai-tong-xue', KEEP GOING" % (SPEAK * 2))
    print("         (gain switches mid-way - do not stop speaking)")
    print("=" * 72)
    sys.stdout.flush()

    set_path("Main Mic")
    set_gain(1, 6)
    time.sleep(0.3)

    print("\n---- phase 1 : quiet baseline (MIC Boost=1 ADC PGA=6) ----")
    sys.stdout.flush()
    q = collect(QUIET, "QUIET ")

    print("\n>>>>>>>>>>>>>>>>  SPEAK NOW - DO NOT STOP  <<<<<<<<<<<<<<<<\n")
    sys.stdout.flush()
    s_mid = collect(SPEAK, "SPEAK-m")

    set_gain(3, 15)
    print("\n---- gain -> FULL (MIC Boost=3 ADC PGA=15) - keep speaking ----")
    sys.stdout.flush()
    time.sleep(0.2)
    s_full = collect(SPEAK, "SPEAK-f")

    print("\n" + "=" * 72)
    print("RESULT")
    print("  quiet      : rms=%8.1f pk=%8.1f voice=%.3f high=%.3f cen=%5.0fHz"
          % (q["rms"], q["pk"], q["voice"], q["high"], q["cen"]))
    print("  speak(mid) : rms=%8.1f pk=%8.1f voice=%.3f high=%.3f cen=%5.0fHz"
          % (s_mid["rms"], s_mid["pk"], s_mid["voice"], s_mid["high"], s_mid["cen"]))
    print("  speak(full): rms=%8.1f pk=%8.1f voice=%.3f high=%.3f cen=%5.0fHz"
          % (s_full["rms"], s_full["pk"], s_full["voice"], s_full["high"], s_full["cen"]))
    print("-" * 72)

    def verdict(label, baseline, speak):
        rr = speak["rms"] / max(baseline["rms"], 1e-6)
        vr = speak["voice"] / max(baseline["voice"], 1e-6)
        reacts = (speak["rms"] > 200 and rr >= 1.6) or (rr >= 1.2 and vr >= 1.4)
        print("  %-10s rms x%.2f  voice-band x%.2f  -> %s"
              % (label, rr, vr, "REACTS TO SPEECH" if reacts else "no reaction"))
        return reacts

    ok_mid = verdict("mid gain", q, s_mid)
    ok_full = verdict("full gain", q, s_full)
    print("-" * 72)
    if ok_mid or ok_full:
        print("VERDICT: MIC IS WORKING -> %s"
              % ("use MID gain (less clipping risk)" if ok_mid else "needs higher gain"))
    else:
        print("VERDICT: MIC NOT REACTING -> check solder joints / MICBIAS / wrong pad")
    print("=" * 72)


main()
