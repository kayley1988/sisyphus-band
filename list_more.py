import json
import sys

d = json.load(open(sys.argv[1], encoding="utf-8"))
keys = ("melo", "matcha-icefall-zh", "zipvoice", "xiao_ya", "huayan", "chaowen")
for a in d["assets"]:
    n = a["name"]
    if any(k in n for k in keys):
        print("%8.1f MB  %s" % (a["size"] / 1048576.0, n))
