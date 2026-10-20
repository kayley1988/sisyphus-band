# 离线 TTS 可行性探测：sherpa-onnx 版本/能力 / 磁盘 / 现有模型 / 能否下载模型
# 用法（板上）: python3 -u /tmp/tts_offline_probe.py
import os
import subprocess
import urllib.request
import socket

print("=== 1) sherpa_onnx 能力 ===")
try:
    import sherpa_onnx
    print("  version:", getattr(sherpa_onnx, "__version__", "?"))
    print("  has OfflineTts:", hasattr(sherpa_onnx, "OfflineTts"))
    print("  has OfflineTtsConfig:", hasattr(sherpa_onnx, "OfflineTtsConfig"))
    print("  has OfflineTtsModelConfig:", hasattr(sherpa_onnx, "OfflineTtsModelConfig"))
    print("  has OfflineTtsVitsModelConfig:", hasattr(sherpa_onnx, "OfflineTtsVitsModelConfig"))
except Exception as e:
    print("  sherpa_onnx FAIL:", e)

print("=== 2) 现有模型目录 ===")
for p in ("/root/guitarist_app/models", "/root/guitarist_app/models/kws",
          "/root/guitarist_app/models/tts", "/opt/models"):
    if os.path.isdir(p):
        try:
            print(" ", p, "->", sorted(os.listdir(p))[:20])
        except Exception as e:
            print(" ", p, "ERR", e)
    else:
        print(" ", p, "MISSING")

print("=== 3) 磁盘空间 ===")
print(subprocess.run(["df", "-h", "/"], capture_output=True, text=True).stdout.strip())

print("=== 4) 关键下载源连通性 ===")


def probe(url, t=8):
    import time
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        r = urllib.request.urlopen(req, timeout=t)
        print("  OK   %-52s %s %.1fs" % (url[:52], r.status, time.time() - t0))
        return True
    except Exception as e:
        print("  FAIL %-52s %s %.1fs" % (url[:52], type(e).__name__, time.time() - t0))
        return False


for u in ("https://github.com",
          "https://hf-mirror.com",
          "https://modelscope.cn",
          "https://pypi.tuna.tsinghua.edu.cn/simple/"):
    probe(u)

print("=== 5) IPv6 情况（判断是否因 IPv6 导致 wss 超时）===")
try:
    infos = socket.getaddrinfo("speech.platform.bing.com", 443)
    fams = sorted({("IPv6" if i[0] == socket.AF_INET6 else "IPv4") for i in infos})
    print("  speech.platform.bing.com 解析出:", fams)
except Exception as e:
    print("  DNS FAIL", e)
print("=== done ===")
