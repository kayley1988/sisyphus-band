import json
import sys

d = json.load(open(sys.argv[1], encoding="utf-8"))
for a in d["assets"]:
    n = a["name"]
    if "piper" in n and "zh" in n:
        print("%8.1f MB  %-52s %s" % (a["size"] / 1048576.0, n,
                                      a["browser_download_url"]))
