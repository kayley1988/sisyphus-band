# -*- coding: utf-8 -*-
"""扫描局域网内拓竹打印机（8883 MQTT-TLS / 990 FTPS / 322 / 1883）。"""
import socket, concurrent.futures, ipaddress, sys

net = sys.argv[1] if len(sys.argv) > 1 else "192.168.1.0/24"
PORTS = [8883, 990, 322, 1883, 21]

def probe(ip, port, t=0.35):
    try:
        s = socket.socket(); s.settimeout(t)
        if s.connect_ex((str(ip), port)) == 0:
            s.close(); return (str(ip), port)
        s.close()
    except Exception:
        pass
    return None

ips = list(ipaddress.ip_network(net, strict=False).hosts())
print("扫描 %s 共 %d 个地址，端口 %s ..." % (net, len(ips), PORTS))
found = {}
with concurrent.futures.ThreadPoolExecutor(max_workers=256) as ex:
    futs = [ex.submit(probe, ip, p) for ip in ips for p in PORTS]
    for f in concurrent.futures.as_completed(futs):
        r = f.result()
        if r:
            found.setdefault(r[0], []).append(r[1])

if not found:
    print("未发现开放端口的设备。")
else:
    for ip, ports in sorted(found.items(), key=lambda x: tuple(int(i) for i in x[0].split("."))):
        print("  %-16s 开放端口: %s" % (ip, sorted(ports)))
