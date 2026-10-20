# -*- coding: utf-8 -*-
"""抓取拓竹打印机摄像头画面（局域网 6000 端口 JPEG 流，P1/A1 系列协议）。"""
import socket, ssl, time, sys, os

HOST, PORT = "192.168.1.50", 6000
USER, PASSWD = "bblp", "278e12a5"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_print_job")

def grab(use_tls, seconds=6.0):
    raw = socket.create_connection((HOST, PORT), timeout=8)
    s = raw
    if use_tls:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try:
            ctx.set_ciphers("DEFAULT@SECLEVEL=0")
        except Exception:
            pass
        s = ctx.wrap_socket(raw, server_hostname=HOST)
    s.settimeout(2.0)
    buf = b""
    t0 = time.time()
    while time.time() - t0 < seconds:
        try:
            d = s.recv(65536)
            if not d:
                break
            buf += d
        except socket.timeout:
            continue
        except Exception as e:
            print("  recv 异常:", e)
            break
    s.close()
    return buf

def extract_jpegs(buf):
    frames = []
    i = 0
    while True:
        a = buf.find(b"\xff\xd8\xff", i)
        if a < 0:
            break
        b = buf.find(b"\xff\xd9", a + 3)
        if b < 0:
            break
        frames.append(buf[a:b+2])
        i = b + 2
    return frames

if __name__ == "__main__":
    for tls in (True, False):
        tag = "TLS" if tls else "明文"
        print("=== 尝试 %s 连接 %s:%d ===" % (tag, HOST, PORT))
        try:
            buf = grab(tls)
        except Exception as e:
            print("  连接失败:", type(e).__name__, e)
            continue
        print("  收到 %d 字节" % len(buf))
        if not buf:
            continue
        print("  头部:", buf[:64].hex())
        frames = extract_jpegs(buf)
        print("  解析到 %d 张 JPEG" % len(frames))
        if frames:
            best = max(frames, key=len)
            p = os.path.join(OUT, "camera.jpg")
            open(p, "wb").write(best)
            print("  已保存最大帧 ->", p, "(%d 字节)" % len(best))
            sys.exit(0)
    print("未能取得画面")
