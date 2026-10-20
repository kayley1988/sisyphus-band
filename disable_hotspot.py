import re, shutil, os

p = '/root/guitarist_app/config/config.yaml'
bak = p + '.bak_hotspot_' + __import__('datetime').datetime.now().strftime('%Y%m%d%H%M%S')
shutil.copy(p, bak)
print('backup ->', bak)

s = open(p, encoding='utf-8').read()
i = s.index('\nhotspot:')
j = s.index('\npower_monitor:', i)
block = s[i:j]
assert 'enabled: true' in block, 'hotspot enabled line not found!'
block2 = re.sub(r'(enabled:\s*)true', r'\1false', block, count=1)
assert 'enabled: false' in block2
s = s[:i] + block2 + s[j:]
open(p, 'w', encoding='utf-8').write(s)
print('hotspot.enabled -> false  OK')
