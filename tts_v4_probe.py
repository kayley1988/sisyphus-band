# 强制 IPv4 下探测 Edge-TTS 是否能通（怀疑板子 IPv6 路由坏导致 wss 超时）
# 用法（板上）: python3 -u /tmp/tts_v4_probe.py
import asyncio
import socket
import time

_orig = socket.getaddrinfo


def _v4(host, port, family=0, type=0, proto=0, flags=0):
    return _orig(host, port, socket.AF_INET, type, proto, flags)


host = "speech.platform.bing.com"
print("=== 1) TCP 直连（IPv4）===")
try:
    infos = _orig(host, 443, socket.AF_INET, socket.SOCK_STREAM)
    ips = sorted({i[4][0] for i in infos})
    print("  IPv4 解析:", ips)
    for ip in ips:
        t = time.time()
        try:
            s = socket.create_connection((ip, 443), timeout=6)
            s.close()
            print("  TCP OK   %-16s %.2fs" % (ip, time.time() - t))
        except Exception as e:
            print("  TCP FAIL %-16s %s %.2fs" % (ip, type(e).__name__, time.time() - t))
except Exception as e:
    print("  resolve fail:", e)

print("=== 2) 打补丁后 edge_tts 合成（强制 IPv4）===")
socket.getaddrinfo = _v4
try:
    import edge_tts

    async def go():
        c = edge_tts.Communicate("你好，我是拨云，这是强制 IPv4 的在线语音测试。",
                                 "zh-CN-XiaoxiaoNeural")
        n = 0
        with open("/tmp/edge_v4.mp3", "wb") as f:
            async for ch in c.stream():
                if ch["type"] == "audio":
                    f.write(ch["data"])
                    n += len(ch["data"])
        return n

    t = time.time()
    n = asyncio.run(go())
    print("  SYNTH OK bytes=%d  %.1fs  -> /tmp/edge_v4.mp3" % (n, time.time() - t))
except Exception as e:
    print("  SYNTH FAIL", type(e).__name__, repr(e)[:180])
print("=== done ===")
