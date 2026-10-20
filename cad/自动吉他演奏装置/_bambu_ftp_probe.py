# -*- coding: utf-8 -*-
"""探测拓竹 FTPS 的可写目录。"""
import io, os, sys, traceback
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bambu_ftp import connect

def try_stor(f, path, data=b"WB_PROBE"):
    try:
        f.storbinary("STOR " + path, io.BytesIO(data))
        return "OK"
    except Exception as e:
        return "%s: %s" % (type(e).__name__, e)

f = connect()
print("欢迎:", f.getwelcome())
try:
    print("PWD:", f.pwd())
except Exception as e:
    print("PWD 失败:", e)

for p in ["/", ".", "/cache", "cache", "/user", "/data", "/tmp", "/model", "/sdcard"]:
    try:
        f.cwd(p)
        print("cwd %-10s OK -> pwd=%s" % (p, f.pwd()))
        try:
            lines = []
            f.retrlines("LIST -a", lines.append)
            print("    列表(%d 项):" % len(lines))
            for l in lines[:15]:
                print("      ", l)
        except Exception as e:
            print("    LIST 失败:", e)
    except Exception as e:
        print("cwd %-10s 失败: %s" % (p, e))

print("\n=== 试探写入 ===")
f.cwd("/")
for p in ["/wb_probe.txt", "/cache/wb_probe.txt", "/data/wb_probe.txt",
          "/tmp/wb_probe.txt", "/model/wb_probe.txt", "wb_probe.txt"]:
    print("  STOR %-24s -> %s" % (p, try_stor(f, p)))

try:
    f.quit()
except Exception:
    pass
