import json
import shutil

p = '/root/guitarist_app/config/ai_config.json'
shutil.copy(p, p + '.bak_1011_micgain')
d = json.load(open(p))
old = d.get('mic_gain')
d['mic_gain'] = 1.0
json.dump(d, open(p, 'w'), ensure_ascii=False, indent=2)
print('mic_gain: %s -> %s' % (old, d['mic_gain']))
