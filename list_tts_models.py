import json
import sys

path = sys.argv[1]
d = json.load(open(path, encoding="utf-8"))
rows = []
for a in d.get("assets", []):
    n = a["name"]
    if any(k in n for k in ("vits-zh", "vits-cantonese", "matcha", "kokoro",
                            "sherpa-onnx-vits-zh", "vits-icefall-zh")):
        rows.append((a["size"], n))
rows.sort()
for s, n in rows:
    print("%8.1f MB  %s" % (s / 1048576.0, n))
print("---- total assets:", len(d.get("assets", [])))
