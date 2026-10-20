# -*- coding: utf-8 -*-
"""把 PETG 耗材预设的继承链手动合并成一个自包含文件，供 Bambu Studio 命令行使用
（CLI 的 --load-filaments 不会自动解析 inherits，导致热床温度掉成默认 45）。
链: fdm_filament_pet -> Bambu PETG Basic @base -> Bambu PETG Basic @BBL P2S 0.4 nozzle
"""
import json, os

R = r'E:/Bambu Studio/resources/profiles/BBL/filament'
CHAIN = [
    'fdm_filament_pet.json',
    'Bambu PETG Basic @base.json',
    'Bambu PETG Basic @BBL P2S 0.4 nozzle.json',
]

merged = {}
for f in CHAIN:
    p = os.path.join(R, f)
    if not os.path.exists(p):
        print('跳过(不存在):', f); continue
    d = json.load(open(p, encoding='utf-8'))
    d.pop('inherits', None)
    merged.update(d)
    print('合并', f, '-> 累计键', len(merged))

# 明确锁定 PETG 关键值（防止任何回退）
merged.update({
    'filament_type': ['PETG'],
    'filament_density': ['1.25'],
    'nozzle_temperature': ['250'],
    'nozzle_temperature_initial_layer': ['245'],
    'hot_plate_temp': ['70'],
    'hot_plate_temp_initial_layer': ['70'],
    'textured_plate_temp': ['70'],
    'textured_plate_temp_initial_layer': ['70'],
    'cool_plate_temp': ['0', '0', '0'],
    'eng_plate_temp': ['70', '70', '70'],
    'filament_settings_id': ['Bambu PETG Basic @BBL P2S 0.4 nozzle'],
})

out = r'E:/音乐/档3实现/cad/自动吉他演奏装置/_print_job/filament_petg_full.json'
json.dump(merged, open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print('已写出', out, '键数', len(merged))
for k in ['filament_type', 'filament_density', 'textured_plate_temp', 'hot_plate_temp',
          'nozzle_temperature', 'nozzle_temperature_initial_layer']:
    print('  %-34s = %s' % (k, merged.get(k)))
