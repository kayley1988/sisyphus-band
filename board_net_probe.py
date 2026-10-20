import socket
import ssl


def raw_dns(server, name, timeout=6):
    q = b"\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00"
    for part in name.split("."):
        q += bytes([len(part)]) + part.encode()
    q += b"\x00\x00\x01\x00\x01"
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.sendto(q, (server, 53))
        d, _ = s.recvfrom(1024)
        return "OK len=%d" % len(d)
    except Exception as e:
        return "FAIL %s %s" % (type(e).__name__, str(e)[:60])
    finally:
        s.close()


print("rawDNS 223.5.5.5    :", raw_dns("223.5.5.5", "api.deepseek.com"))
print("rawDNS 192.168.1.1  :", raw_dns("192.168.1.1", "api.deepseek.com"))

# 绕过 DNS，直接用 IP 建 TLS（验证到 DeepSeek 的链路是否真的通）
for ip in ["221.11.190.218"]:
    try:
        s = socket.create_connection((ip, 443), timeout=12)
        ctx = ssl.create_default_context()
        ss = ctx.wrap_socket(s, server_hostname="api.deepseek.com")
        print("TLS", ip, "OK ->", ss.version())
        ss.close()
    except Exception as e:
        print("TLS", ip, "FAIL", type(e).__name__, str(e)[:90])
