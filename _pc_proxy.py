"""
PC 侧极简 HTTP CONNECT 代理 —— 仅用于把开发机的网络借给板子做联调演示。
用法: python _pc_proxy.py [port]
说明: 支持 HTTPS 的 CONNECT 隧道；板子通过 `adb reverse tcp:PORT tcp:PORT`
      把本地 127.0.0.1:PORT 转发到这里，从而借道 PC 访问外网。
"""
import socket
import sys
import threading

BUF = 65536


def pipe(src, dst):
    try:
        while True:
            data = src.recv(BUF)
            if not data:
                break
            dst.sendall(data)
    except Exception:
        pass
    finally:
        for s in (src, dst):
            try:
                s.close()
            except Exception:
                pass


def handle(conn):
    try:
        req = b""
        while b"\r\n\r\n" not in req:
            chunk = conn.recv(4096)
            if not chunk:
                return
            req += chunk
            if len(req) > 65536:
                return
        head = req.split(b"\r\n\r\n", 1)[0].decode("latin1")
        first = head.split("\r\n")[0]
        parts = first.split(" ")
        if len(parts) < 2:
            conn.close()
            return
        method, target = parts[0], parts[1]
        if method.upper() == "CONNECT":
            host, _, port = target.partition(":")
            port = int(port or 443)
            up = socket.create_connection((host, port), timeout=15)
            conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            threading.Thread(target=pipe, args=(conn, up), daemon=True).start()
            threading.Thread(target=pipe, args=(up, conn), daemon=True).start()
        else:
            # 非 CONNECT 的普通 HTTP 请求（简单透传）
            conn.close()
    except Exception:
        try:
            conn.close()
        except Exception:
            pass


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18080
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(64)
    print(f"[pc-proxy] listening on 127.0.0.1:{port}", flush=True)
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=handle, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    main()
