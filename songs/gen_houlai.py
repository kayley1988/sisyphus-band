# -*- coding: utf-8 -*-
"""《后来》简谱PDF -> 谱面CSV 转换器
原调 1=bE 75BPM -> 机器版移调 C（全部开放把位，<=4品）
和弦字典（列序 = 1弦,2弦,3弦,4弦,5弦,6弦，0=空弦）
"""
import csv
import os

# ---- 和弦指法（1弦..6弦）----
CHORDS = {
    'C':  [0, 1, 0, 2, 3, 3],   # C/G 低把位全六弦
    'Em': [0, 0, 0, 2, 2, 0],
    'Am': [0, 1, 2, 2, 0, 0],   # 6弦空弦A作低音
    'F':  [1, 1, 2, 3, 3, 1],   # 小横按
    'G':  [3, 0, 0, 0, 2, 3],
    'Dm': [1, 3, 2, 0, 0, 0],   # 5/6弦空弦作低音
}

# 级数->C调和弦（3m/7->Em, 3m/5->Em, 2m7->Dm, 4m->F, 5/7->G, 1/3->C）
DEG = {'1': 'C', '2m': 'Dm', '3m': 'Em', '4': 'F', '5': 'G', '6m': 'Am'}

HEADER = ['1弦', '2弦', '3弦', '4弦', '5弦', '6弦',
          '区域1', '区域2', '区域3', '区域4', '区域5', '区域6',
          '横线', '拍号', 'BPM']


def row_chord(name, dash='', strum=None, meta=''):
    """品位行。strum: None=不拨, 'down'=全下扫, 'up'=全上扫"""
    r = list(CHORDS[name]) + [''] * 6 + [dash, '', '']
    if strum:
        for j in range(6):
            r[6 + j] = 'up' if strum == 'up' else '1'
    if meta:
        r[13], r[14] = '4/4', meta
    return r


def row_hold(dash='0'):
    return [''] * 12 + [dash, '', '']


def bar_pattern(name, pattern='folk'):
    """一小节。folk = 下 下上 上下上（8个八分音符）
       half = 下(1) 空(2) 下(3) 空(4)——前奏/间奏用
       end  = 长音收尾"""
    rows = []
    if pattern == 'half':
        rows.append(row_chord(name, dash='0', strum='down'))
        rows.append(row_hold('0'))
        rows.append(row_chord(name, dash='0', strum='down'))
        rows.append(row_hold('0'))
    elif pattern == 'end':
        rows.append(row_chord(name, dash='0', strum='down'))
        rows.append(row_hold('0'))
        rows.append(row_hold('0'))
        rows.append(row_hold('0'))
    else:  # folk：每格428ms
        seq = ['down', None, 'down', 'up', None, 'up', 'down', 'up']
        for s in seq:
            if s:
                rows.append(row_chord(name, dash='1', strum=s))
            else:
                rows.append(row_hold('1'))
    return rows


def bar_two(a, b, pattern='folk'):
    """一小节两个和弦，各半（folk: 前4格a后4格b）"""
    rows = []
    if pattern == 'half':
        rows.append(row_chord(a, dash='0', strum='down'))
        rows.append(row_hold('0'))
        rows.append(row_chord(b, dash='0', strum='down'))
        rows.append(row_hold('0'))
    else:
        for s in ['down', None, 'down', 'up']:
            rows.append(row_chord(a, dash='1', strum=s) if s else row_hold('1'))
        for s in ['up', None, 'down', 'up']:
            rows.append(row_chord(b, dash='1', strum=s) if s else row_hold('1'))
    return rows


def build():
    rows = [list(HEADER)]
    bars = []  # (和弦或元组, pattern)

    # 前奏
    bars += [('C', 'half'), ('Em', 'half'), ('Am', 'half'), (('F', 'G'), 'half')]
    # 主歌 A1-A6
    bars += [('C', 'folk'), ('Em', 'folk'), ('Am', 'folk'), ('Em', 'folk')]
    bars += [(('F', 'G'), 'folk'), (('Em', 'Am'), 'folk'), (('Dm', 'G'), 'folk'), ('C', 'folk')]
    bars += [('C', 'folk'), ('Em', 'folk'), ('Am', 'folk'), ('Em', 'folk')]
    bars += [(('F', 'G'), 'folk'), (('Em', 'Am'), 'folk'), (('Dm', 'G'), 'folk'), ('C', 'folk')]
    bars += [(('C', 'Em'), 'folk'), (('Dm', 'F'), 'folk'), ('C', 'folk'), ('G', 'folk')]
    bars += [('Am', 'folk'), ('Em', 'folk'), (('Dm', 'G'), 'folk'), ('C', 'folk')]
    # 副歌
    bars += [(('F', 'G'), 'folk'), (('Em', 'Am'), 'folk'), (('Dm', 'G'), 'folk'), ('C', 'folk')]
    # 间奏
    bars += [('C', 'half'), (('Em', 'Am'), 'half'), (('F', 'G'), 'half'), ('C', 'half')]
    # B段
    bars += [(('C', 'Em'), 'folk'), (('Dm', 'F'), 'folk'), ('C', 'folk'), ('G', 'folk')]
    bars += [('Am', 'folk'), ('Em', 'folk'), (('Dm', 'G'), 'folk'), ('C', 'folk')]
    bars += [(('Dm', 'G'), 'folk'), (('Em', 'Am'), 'folk')]
    bars += [(('F', 'G'), 'folk'), (('F', 'G'), 'folk')]
    # 副歌 x2
    bars += [(('F', 'G'), 'folk'), (('Em', 'Am'), 'folk'), (('Dm', 'G'), 'folk'), ('C', 'folk')]
    bars += [(('F', 'G'), 'folk'), (('Em', 'Am'), 'folk'), (('Dm', 'G'), 'folk'), ('C', 'folk')]
    # C段（永远不会再重来）
    bars += [('C', 'folk'), ('C', 'folk'), ('F', 'folk'), ('C', 'folk')]
    bars += [(('Dm', 'G'), 'folk'), ('F', 'folk'), ('C', 'folk'), (('Dm', 'G'), 'folk')]
    # 末副歌 x2 + 收尾
    bars += [(('F', 'G'), 'folk'), (('Em', 'Am'), 'folk'), (('Dm', 'G'), 'folk'), ('C', 'folk')]
    bars += [(('F', 'G'), 'folk'), (('Em', 'Am'), 'folk'), (('Dm', 'G'), 'folk'), ('C', 'folk')]
    bars += [('C', 'end')]

    first = True
    for item in bars:
        if isinstance(item[0], tuple):
            (a, b), pat = item
            rows += bar_two(a, b, pat)
        else:
            name, pat = item
            rows += bar_pattern(name, pat)
        if first:
            # 首行元信息：拍号4/4 + BPM75（原谱速度）
            rows[1][13], rows[1][14] = '4/4', '75'
            first = False

    return rows, len(bars)


if __name__ == '__main__':
    rows, nbars = build()
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       '后来', '图片源1', '输出结果.csv')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'w', newline='', encoding='utf-8-sig') as f:
        csv.writer(f).writerows(rows)
    print('bars=%d rows=%d -> %s' % (nbars, len(rows) - 1, out))
