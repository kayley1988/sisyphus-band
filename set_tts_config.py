import json
import sys

p = "/root/guitarist_app/config/ai_config.json"
val = sys.argv[1] if len(sys.argv) > 1 else "sherpa"
d = json.load(open(p, encoding="utf-8"))
old = d.get("tts_engine")
d["tts_engine"] = val
json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("tts_engine: %r -> %r" % (old, val))
print(json.dumps(d, ensure_ascii=False, indent=2))
