# -*- coding: utf-8 -*-
"""拓竹打印机局域网发现：UDP 2021 广播 + 存活主机端口扫描。"""
import socket, json, time, concurrent.futures, sys

def udp_discover(timeout=4.0):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.settimeout(timeout)
    try:
        s.bind(("", 2021))
    except Exception as e:
        print("  bind 2021 失败(可能被占用):", e)
    pkt = json.dumps({"msg": 1}).encode()
    targets = ["255.255.255.255", "192.168.1.255", "<broadcast>"]
    for t in targets:
        for _ in range(3):
            try:
                s.sendto(pkt, (t, 2021))
            except Exception as e:
                print("  发送到 %s 失败: %s" % (t, e))
    print("  已广播发现包，等待回应 ...")
    got = []
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            data, addr = s.recvfrom(4096)
        except socket.timeout:
            break
        try:
            j = json.loads(data.decode("utf-8", "replace"))
        except Exception:
            j = {"raw": data[:200].hex()}
        got.append((addr, j))
    s.close()
    return got

def portscan(hosts, ports):
    def one(hp):
        h, p = hp
        s = socket.socket(); s.settimeout(0.5)
        try:
            if s.connect_ex((h, p)) == 0:
                return (h, p)
        finally:
            s.close()
        return None
    jobs = [(h, p) for h in hosts for p in ports]
    out = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=128) as ex:
        for r in ex.map(one, jobs):
            if r:
                out.setdefault(r[0], []).append(r[1])
    return out

print("=== 1) UDP 2021 拓竹发现 ===")
res = udp_discover()
if not res:
    print("  没有回应（打印机可能关机 / 不在同一网段 / 非局域网模式）")
for addr, j in res:
    print("  来自 %s:%s" % addr)
    print("   ", json.dumps(j, ensure_ascii=False)[:600])

print("\n=== 2) 存活主机端口扫描 ===")
hosts = sys.argv[1:] or ["192.168.1.3", "192.168.1.5", "192.168.1.14",
                         "192.168.1.42", "192.168.1.50", "192.168.1.57",
                         "192.168.1.248"]
ports = [990, 8883, 322, 1883, 2021, 80, 443, 8080, 6000, 9000, 22, 23, 5000, 8000, 8081]
found = portscan(hosts, ports)
if not found:
    print("  所有目标主机均无开放端口")
for h in hosts:
    if h in found:
        print("  %-16s 开放: %s" % (h, sorted(found[h])))
    else:
        print("  %-16s 无开放端口" % h)
