#!/usr/bin/env python3
"""Raw mic level comparison: quiet baseline vs speaking.

Purpose: after soldering a real electret mic onto the RK809 MIC pads, decide
  (a) whether the mic actually picks up voice (vs still floating), and
  (b) what hardware gain is safe (no clipping).
Run with LOW hardware gain so the true signal is visible (full gain would
clip everything into a square wave and hide the difference).

Usage:  python3 -u mic_raw_cmp.py [quiet_sec=6] [speak_sec=14]
Stop guitar.service first (it owns the capture device exclusively).
Pure ASCII output (board stdout is not UTF-8).
"""
import sys
import time

import numpy as np
import sounddevice as sd

SR = 16000
BLK = 512
QUIET = float(sys.argv[1]) if len(sys.argv) > 1 else 6.0
SPEAK = float(sys.argv[2]) if len(sys.argv) > 2 else 14.0
FULL = 32768.0


def f7(v):
    return "%7.1f" % v


def main():
    total = QUIET + SPEAK
    print("=" * 66)
    print("RAW MIC TEST     quiet %.0fs  +  speak %.0fs    (LOW hw gain)" % (QUIET, SPEAK))
    print("  PHASE 1 (first %.0fs) : STAY QUIET, do not speak" % QUIET)
    print("  PHASE 2 (next  %.0fs) : SPEAK LOUDLY  xiao-ai-tong-xue" % SPEAK)
    print("=" * 66)
    sys.stdout.flush()

    quiet_rms, quiet_pk, speak_rms, speak_pk = [], [], [], []
    t0 = time.time()
    last = -9.0
    phase = 1
    with sd.InputStream(samplerate=SR, channels=1, dtype="int16", blocksize=BLK) as st:
        while True:
            el = time.time() - t0
            if el >= total:
                break
            new_phase = 1 if el < QUIET else 2
            if new_phase != phase:
                phase = new_phase
                print("\n[t=%5.1fs] >>>>>>>>>>- SPEAK NOW -<<<<<<<<\n" % el)
                sys.stdout.flush()
            d, _ = st.read(BLK)
            x = d[:, 0].astype(np.float32) / FULL
            rms = float(np.sqrt((x * x).mean())) * FULL
            pk = float(np.abs(x).max()) * FULL
            if phase == 1:
                quiet_rms.append(rms)
                quiet_pk.append(pk)
            else:
                speak_rms.append(rms)
                speak_pk.append(pk)
            if el - last >= 1.0:
                last = el
                print("[t=%5.1fs][%s] rms=%s pk=%s"
                      % (el, "QUIET" if phase == 1 else "SPEAK", f7(rms), f7(pk)))
                sys.stdout.flush()


    def mx(v):
        return max(v) if v else 0.0

    def av(v):
        return (sum(v) / len(v)) if v else 0.0

    qr, qp = av(quiet_rms), mx(quiet_pk)
    sr, sp = av(speak_rms), mx(speak_pk)
    print("\n" + "-" * 66)
    print("baseline : rms_avg=%s  pk_max=%s" % (f7(qr), f7(qp)))
    print("speaking : rms_avg=%s  pk_max=%s" % (f7(sr), f7(sp)))
    print("delta    : rms x%.1f   pk x%.1f" % (sr / max(qr, 1.0), sp / max(qp, 1.0)))
    print("clip     : %s   (peak %s of 32767)"
          % ("YES" if sp > 32000 else "no", f7(sp)))
    print("-" * 66)
    if sr > qr * 2.0 and sp > 500:
        if sp > 32000:
            print("Verdict: MIC WORKS but TOO HOT -> lower hw gain")
        else:
            print("Verdict: MIC WORKS, level clean -> keep / fine-tune gain")
    elif sp > 500:
        print("Verdict: SOME change but weak -> check solder joint / mic position")
    else:
        print("Verdict: NO CHANGE -> mic not connected, or wired wrong")


main()
