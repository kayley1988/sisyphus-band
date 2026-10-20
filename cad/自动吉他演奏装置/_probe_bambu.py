# -*- coding: utf-8 -*-
"""探测 Bambu Studio 配置里的打印机设备信息（IP / access_code / 序列号）。"""
import json, os, re, sys

appdata = os.environ.get("APPDATA", "")
conf = os.path.join(appdata, "BambuStudio", "BambuStudio.conf")
if not os.path.exists(conf):
    print("找不到配置:", conf); sys.exit(1)

raw = open(conf, encoding="utf-8", errors="replace").read()
print("配置文件大小: %d 字符" % len(raw))
try:
    data = json.loads(raw)
    print("JSON 解析: OK, 顶层键 =", list(data.keys()))
except Exception as e:
    print("JSON 解析失败:", e)
    data = None
    # 退化为文本检索
    for key in ("access_code", "lan_devices", "device", "dev_id", "22E8BJ631501784"):
        for m in re.finditer(re.escape(key), raw):
            i = m.start()
            print("---- %s @ %d ----" % (key, i))
            print(raw[max(0, i-400):i+400].replace("\n", " "))
            break
    sys.exit(0)

SER = "22E8BJ631501784"

def walk(node, path=""):
    """递归找出所有含 ip/access_code/dev_id 的子树"""
    hits = []
    if isinstance(node, dict):
        keys = set(k.lower() for k in node.keys())
        score = len(keys & {"ip", "access_code", "dev_id", "dev_ip", "dev_access_code",
                            "dev_name", "model", "printer_model", "bind_state",
                            "dev_connect_mode", "name", "online"})
        if score >= 2:
            hits.append((path, node))
        for k, v in node.items():
            hits += walk(v, path + "/" + str(k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            hits += walk(v, path + "[%d]" % i)
    return hits

hits = walk(data)
print("\n=== 疑似设备条目 %d 个 ===" % len(hits))
for p, node in hits:
    print("\n路径:", p)
    for k, v in node.items():
        sv = str(v)
        if len(sv) > 120:
            sv = sv[:120] + "..."
        print("   %-24s = %s" % (k, sv))

# 直接搜序列号位置
print("\n=== 配置中 %s 出现的位置 ===" % SER)
for path, node in walk(data):
    pass
def find_ser(node, path=""):
    out = []
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, str) and SER in v:
                out.append((path + "/" + str(k), v))
            out += find_ser(v, path + "/" + str(k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out += find_ser(v, path + "[%d]" % i)
    return out
for p, v in find_ser(data):
    print(" ", p, "=", v[:160])
