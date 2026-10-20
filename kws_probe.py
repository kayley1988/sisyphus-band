#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KWS on-board probe -- answers 3 questions in ONE run (ASCII output only):
  1) what do the ALSA capture gains actually hold?
  2) can a 16 kHz InputStream be opened, and what is the RAW signal level?
  3) does the wake word "xiao ai tong xue" hit -- on RAW audio AND on AGC'd audio?

Run with guitar.service STOPPED (capture sub-stream is exclusive).
"""
import os
import sys
import time
import wave
import glob
import subprocess
import numpy as np

SR = 16000
CHUNK = 1024
DUR = 40.0
KWS_DIR = "/root/guitarist_app/models/kws"
OUT_WAV = "/root/kws_probe.wav"


def sh(cmd):
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=6)
        return (p.stdout or "").strip()
    except Exception as e:
        return "ERR:%s" % e


def bar(v, mx=32768.0, n=32):
    k = int(min(1.0, max(0.0, v / mx)) * n)
    return "#" * k + "." * (n - k)


# ---------------- 1) ALSA state ----------------
print("### 1) ALSA capture controls")
print(sh("amixer -c 0 scontrols"))
for nid in (13, 16, 17, 18):
    print("  numid=%d -> %s" % (nid, sh("amixer -c 0 cget numid=%d | tail -1" % nid)))

# ---------------- 2) devices ----------------
import sounddevice as sd

print("### 2) input devices")
try:
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            print("  [%d] %s  in=%d  default_sr=%.0f" % (
                i, d["name"], d["max_input_channels"], d["default_samplerate"]))
except Exception as e:
    print("  query err: %s" % e)

# ---------------- 3) open stream ----------------
print("### 3) open InputStream @16000")
blocks = []
allpcm = []


def cb(indata, n, ti, stat):
    blocks.append(indata.copy())
    allpcm.append(indata.copy())


st = None
try:
    st = sd.InputStream(samplerate=SR, channels=1, dtype="int16",
                        blocksize=CHUNK, callback=cb)
    st.start()
    print("  OK  samplerate=%d blocksize=%d latency=%s" % (
        st.samplerate, st.blocksize, str(st.latency)))
except Exception as e:
    print("  OPEN FAILED @16000: %s" % e)
    for alt in (48000, 44100, 32000, 8000):
        try:
            st = sd.InputStream(samplerate=alt, channels=1, dtype="int16",
                                blocksize=CHUNK, callback=cb)
            st.start()
            print("  OPEN OK @%d (fallback)" % alt)
            break
        except Exception as e2:
            print("  fail @%d: %s" % (alt, e2))
            st = None
    if st is None:
        print("  !!! no capture rate available")
        sys.exit(1)

# ---------------- 4) KWS ----------------
print("### 4) load KWS model")
import sherpa_onnx


def pick(kind):
    c = [f for f in glob.glob(os.path.join(KWS_DIR, "*.onnx"))
         if kind in os.path.basename(f)
         and "int8" not in os.path.basename(f)
         and "epoch-99" in os.path.basename(f)]
    return c[0] if c else None


enc, dec, joi = pick("encoder"), pick("decoder"), pick("joiner")
print("  enc=%s" % (os.path.basename(enc or "?")))
try:
    spot = sherpa_onnx.KeywordSpotter(
        encoder=enc, decoder=dec, joiner=joi,
        tokens=os.path.join(KWS_DIR, "tokens.txt"),
        keywords_file=os.path.join(KWS_DIR, "keywords.txt"),
        num_threads=2, keywords_score=0.10, keywords_threshold=0.05)
    s_raw = spot.create_stream()
    s_agc = spot.create_stream()
    print("  spotter ok (2 streams)")
except Exception as e:
    print("  spotter FAILED: %s" % e)
    sys.exit(1)


class AGC:
    """same shape as ai/audio_utils.AGCProcessor + WakeListener params"""

    def __init__(self, target=0.08, attack=0.3, release=0.05, mx=25.0, g0=5.0):
        self.t, self.a, self.r, self.mx, self.g = target, attack, release, mx, g0

    def process(self, x):
        rms = float(np.sqrt(np.mean(x * x)) + 1e-10)
        if rms < self.t:
            self.g += self.a * (self.t / rms - 1.0)
        else:
            self.g -= self.r * (rms / self.t - 1.0)
        self.g = max(1.0, min(self.mx, self.g))
        return x * (3.0 * self.g)


agc = AGC()
hits_raw = []
hits_agc = []


def feed(stream, x):
    try:
        stream.accept_waveform(SR, x)
    except Exception as e:
        return "accept_err:%s" % e
    try:
        while spot.is_ready(stream):
            spot.decode_stream(stream)
    except TypeError:
        try:
            while spot.is_ready():
                spot.decode_stream(stream)
        except Exception:
            pass
    except Exception:
        pass
    try:
        r = spot.get_result(stream)
    except TypeError:
        r = spot.get_result()
    return r if isinstance(r, str) else getattr(r, "keyword", None)


print("### 5) >>> SPEAK NOW NOW NOW: say 'xiao ai tong xue' x3  (%.0fs window) <<<" % DUR)
t0 = time.time()
nb = 0
last = -1.0
peak_all = 0.0
rms_sum = 0.0
rms_n = 0
while time.time() - t0 < DUR:
    if not blocks:
        time.sleep(0.01)
        continue
    blk = blocks.pop(0)
    nb += 1
    x = blk[:, 0].astype(np.float32) / 32768.0
    rms = float(np.sqrt(np.mean(x * x)) * 32768)
    pk = float(np.abs(x).max() * 32768)
    peak_all = max(peak_all, pk)
    rms_sum += rms
    rms_n += 1

    kw = feed(s_raw, x)
    if kw:
        hits_raw.append(kw)
        print("  *** RAW HIT: %s @ %.1fs" % (kw, time.time() - t0))
        try:
            spot.reset_stream(s_raw)
        except Exception:
            pass

    xa = agc.process(x)
    kw2 = feed(s_agc, xa)
    if kw2:
        hits_agc.append(kw2)
        print("  *** AGC HIT: %s @ %.1fs" % (kw2, time.time() - t0))
        try:
            spot.reset_stream(s_agc)
        except Exception:
            pass

    el = time.time() - t0
    if el - last >= 1.0:
        print("  t=%4.1fs raw_rms=%6.0f raw_peak=%6.0f |%s|" % (el, rms, pk, bar(rms)))
        last = el

avg_rms = rms_sum / max(1, rms_n)
print("### 7) SUMMARY blocks=%d avg_raw_rms=%.0f peak_raw=%.0f" % (nb, avg_rms, peak_all))
print("###    RAW hits=%s" % (hits_raw if hits_raw else "NONE"))
print("###    AGC hits=%s" % (hits_agc if hits_agc else "NONE"))

try:
    st.stop()
    st.close()
except Exception:
    pass

# ---------------- 8) save wav ----------------
try:
    if allpcm:
        pcm = np.concatenate(allpcm, axis=0)[:, 0]
        with wave.open(OUT_WAV, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(pcm.tobytes())
        print("### 8) wav saved: %s  %.1fs %d samples" % (OUT_WAV, len(pcm) / SR, len(pcm)))
    else:
        print("### 8) no audio captured")
except Exception as e:
    print("### 8) wav save failed: %s" % e)

print("### PROBE DONE")
