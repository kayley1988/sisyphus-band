# -*- coding: utf-8 -*-
"""《真的爱你》(BEYOND) 谱面CSV生成器
Key C 4/4 BPM77（ComeGetChord 和弦谱口径），全曲 C G Am F 循环
"""
import csv
import os

CHORDS = {
    'C':  [0, 1, 0, 2, 3, 3],
    'Em': [0, 0, 0, 2, 2, 0],
    'Am': [0, 1, 2, 2, 0, 0],
    'F':  [1, 1, 2, 3, 3, 1],
    'G':  [3, 0, 0, 0, 2, 3],
    'Dm': [1, 3, 2, 0, 0, 0],
}

HEADER = ['1弦', '2弦', '3弦', '4弦', '5弦', '6弦',
          '区域1', '区域2', '区域3', '区域4', '区域5', '区域6',
          '横线', '拍号', 'BPM']


def row_chord(name, dash='', strum=None, meta=''):
    r = list(CHORDS[name]) + [''] * 6 + [dash, '', '']
    if strum:
        for j in range(6):
            r[6 + j] = 'up' if strum == 'up' else '1'
    if meta:
        r[13], r[14] = '4/4', meta
    return r


def row_hold(dash='0'):
    return [''] * 12 + [dash, '', '']


def bar_folk(seq):
    """folk 八分音符扫弦：下 下上 上下上，seq 为该小节和弦序列(1~2个)"""
    rows = []
    if len(seq) == 1:
        pattern = ['down', None, 'down', 'up', None, 'up', 'down', 'up']
        for s in pattern:
            rows.append(row_chord(seq[0], dash='1', strum=s) if s else row_hold('1'))
    else:
        a, b = seq
        for s in ['down', None, 'down', 'up']:
            rows.append(row_chord(a, dash='1', strum=s) if s else row_hold('1'))
        for s in ['up', None, 'down', 'up']:
            rows.append(row_chord(b, dash='1', strum=s) if s else row_hold('1'))
    return rows


def bar_half(seq):
    """half 每两拍一扫（前奏/间奏）"""
    rows = []
    if len(seq) == 1:
        rows += [row_chord(seq[0], dash='0', strum='down'), row_hold('0'),
                 row_chord(seq[0], dash='0', strum='down'), row_hold('0')]
    else:
        a, b = seq
        rows += [row_chord(a, dash='0', strum='down'), row_hold('0'),
                 row_chord(b, dash='0', strum='down'), row_hold('0')]
    return rows


def bar_end(name):
    return [row_chord(name, dash='0', strum='down'),
            row_hold('0'), row_hold('0'), row_hold('0')]


def build():
    bars = []  # (seq_list, pattern)
    # 前奏: C G | Am F | C G | C
    bars += [(['C', 'G'], 'half'), (['Am', 'F'], 'half'), (['C', 'G'], 'half'), (['C'], 'half')]
    # 主歌1: 无法可修饰的一对手 / 纵使啰嗦始终关注
    bars += [(['C', 'G'], 'folk'), (['Am', 'F'], 'folk'), (['C', 'G'], 'folk'), (['C'], 'folk')]
    # 主歌2: 沉醉于音阶她不赞赏 / 决心冲开心中挣扎
    bars += [(['C', 'G'], 'folk'), (['Am', 'F'], 'folk'), (['C', 'G'], 'folk'), (['C'], 'folk')]
    # 桥段: 春风化雨暖透我的心 一生眷顾无言地送赠
    bars += [(['F', 'G'], 'folk'), (['F', 'G'], 'folk'), (['C', 'G'], 'folk'), (['Am', 'F'], 'folk')]
    # 副歌: 是你多么温馨的目光 真的爱你
    bars += [(['C', 'G'], 'folk'), (['Am', 'F'], 'folk'), (['C', 'G'], 'folk'), (['C'], 'folk')]
    # 主歌3: 仍记起温馨的一对手 / 理想今天终于等到
    bars += [(['C', 'G'], 'folk'), (['Am', 'F'], 'folk'), (['C', 'G'], 'folk'), (['C'], 'folk')]
    # 桥段2
    bars += [(['F', 'G'], 'folk'), (['F', 'G'], 'folk'), (['C', 'G'], 'folk'), (['Am', 'F'], 'folk')]
    # 副歌 x2
    bars += [(['C', 'G'], 'folk'), (['Am', 'F'], 'folk'), (['C', 'G'], 'folk'), (['C'], 'folk')]
    bars += [(['C', 'G'], 'folk'), (['Am', 'F'], 'folk'), (['C', 'G'], 'folk'), (['C'], 'folk')]
    # 尾奏: C G | Am F | C G | C
    bars += [(['C', 'G'], 'half'), (['Am', 'F'], 'half'), (['C', 'G'], 'half'), (['C'], 'end')]

    rows = [list(HEADER)]
    first = True
    for seq, pat in bars:
        if pat == 'end':
            rows += bar_end(seq[0])
        elif pat == 'half':
            rows += bar_half(seq)
        else:
            rows += bar_folk(seq)
        if first:
            rows[1][13], rows[1][14] = '4/4', '77'
            first = False
    return rows, len(bars)


if __name__ == '__main__':
    rows, nbars = build()
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       '真的爱你', '图片源1', '输出结果.csv')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'w', newline='', encoding='utf-8-sig') as f:
        csv.writer(f).writerows(rows)
    print('bars=%d rows=%d -> %s' % (nbars, len(rows) - 1, out))
