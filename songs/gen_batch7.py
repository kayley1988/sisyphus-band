# -*- coding: utf-8 -*-
"""批量生成 7 首歌的谱面 CSV（2026-10-03 Z 点歌批次）
共用 songlib 框架（同 gen_houlai.py 语义）：
- 列序 = 1弦..6弦（1弦在前），品位上限 4 品
- 横线 = 纯音符时值（0=四分 857ms基准, 1=八分, 2=十六分），BPM 由固件端缩放
- 区域格非空=拨弦，'up'=上扫
- 拍号/BPM 取第一数据行
"""
import csv
import os

# ---- 和弦指法（1弦..6弦）----
CHORDS = {
    'C':   [0, 1, 0, 2, 3, 3],
    'Em':  [0, 0, 0, 2, 2, 0],
    'Am':  [0, 1, 2, 2, 0, 0],
    'F':   [1, 1, 2, 3, 3, 1],
    'G':   [3, 0, 0, 0, 2, 3],
    'Dm':  [1, 3, 2, 0, 0, 0],
    'E':   [0, 0, 1, 2, 2, 0],   # 新增：大三和弦 E（3级借用）
    'E7':  [0, 0, 1, 0, 2, 0],   # 新增：E7（电台情歌 3/7）
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


def bar_pattern(name, pattern='folk'):
    """folk = 下 下上 上下上（8八分）| half = 下 - 下 -（4四分）
       ballad = 下 - - 上 - - 下 -（慢歌叙事感）| end = 长音收尾"""
    rows = []
    if pattern == 'half':
        seq = ['down', None, 'down', None]
        dash = '0'
    elif pattern == 'ballad':
        seq = ['down', None, None, 'up', None, None, 'down', None]
        dash = '1'
    elif pattern == 'end':
        rows.append(row_chord(name, dash='0', strum='down'))
        rows.append(row_hold('0'))
        rows.append(row_hold('0'))
        rows.append(row_hold('0'))
        return rows
    else:  # folk
        seq = ['down', None, 'down', 'up', None, 'up', 'down', 'up']
        dash = '1'
    for s in seq:
        if s:
            rows.append(row_chord(name, dash=dash, strum=s))
        else:
            rows.append(row_hold(dash))
    return rows


def bar_two(a, b, pattern='folk'):
    """一小节两个和弦各半。"""
    rows = []
    if pattern == 'half':
        seqs = [('down', None), ('down', None)]
    else:  # folk: 前4格a后4格b（下 空 下上 | 上 空 下 上）
        seqs = [('down', None, 'down', 'up'), ('up', None, 'down', 'up')]
    dash = '0' if pattern == 'half' else '1'
    for half, (a_or_b) in enumerate([a, b]):
        for s in seqs[half]:
            if s:
                rows.append(row_chord(a_or_b, dash=dash, strum=s))
            else:
                rows.append(row_hold(dash))
    return rows


def render(bars, bpm):
    rows = [list(HEADER)]
    first = True
    for item in bars:
        if isinstance(item[0], tuple):
            (a, b), pat = item
            rows += bar_two(a, b, pat)
        else:
            name, pat = item
            rows += bar_pattern(name, pat)
        if first:
            rows[1][13], rows[1][14] = '4/4', str(bpm)
            first = False
    return rows, len(bars)


def save(song, rows, nbars):
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       song, '图片源1', '输出结果.csv')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'w', newline='', encoding='utf-8-sig') as f:
        csv.writer(f).writerows(rows)
    print('%-12s bars=%3d rows=%4d' % (song, nbars, len(rows) - 1))
    return out


# ============ 1. 那么骄傲（bE→C, 74, 慢歌） ============
def gen_nmdgao():
    b = []
    b += [('C', 'half'), (('Am', 'F'), 'half'), (('C', 'G'), 'half'), ('F', 'half')]   # 前奏
    for _ in range(2):                                                                  # 主歌×2
        b += [('C', 'half'), ('G', 'half'), ('F', 'half'), ('G', 'half'),
              ('C', 'half'), ('G', 'half'), ('F', 'half'), ('C', 'half')]
    for _ in range(2):                                                                  # 副歌
        b += [('Am', 'folk'), ('G', 'folk'), ('F', 'folk'), ('G', 'folk'),
              ('Am', 'folk'), ('G', 'folk'), ('F', 'folk'), ('G', 'folk')]
    b += [('C', 'half'), ('G', 'half'), ('Am', 'half'), ('F', 'half'),                  # B段 走掉/月光
          ('C', 'half'), ('G', 'half'), ('Am', 'half'), ('F', 'half')]
    for _ in range(2):                                                                  # 副歌2
        b += [('Am', 'folk'), ('G', 'folk'), ('F', 'folk'), ('G', 'folk'),
              ('Am', 'folk'), ('G', 'folk'), ('F', 'folk'), ('G', 'folk')]
    b += [('C', 'half'), ('G', 'half'), ('Am', 'half'), ('F', 'half'),                  # 拥抱/微笑
          ('C', 'half'), ('G', 'half'), ('F', 'half'), ('C', 'half')]
    b += [('Am', 'half'), ('G', 'half'), ('F', 'half'), ('G', 'half')]                  # 间奏
    b += [('C', 'half'), ('G', 'half'), ('F', 'half'), ('G', 'half'),
          ('C', 'half'), ('G', 'half'), ('F', 'half'), ('C', 'half')]                   # 主歌3
    for _ in range(2):                                                                  # 末副歌
        b += [('Am', 'folk'), ('G', 'folk'), ('F', 'folk'), ('G', 'folk'),
              ('Am', 'folk'), ('G', 'folk'), ('F', 'folk'), ('G', 'folk')]
    b += [('C', 'half'), ('G', 'half'), ('F', 'half'), ('C', 'end')]                    # 尾
    return render(b, 74)


# ============ 2. 把耳朵叫醒（C, 128, 轻快） ============
def gen_berjx():
    LOOP = [('Am', 'folk'), ('Dm', 'folk'), ('G', 'folk'), ('C', 'folk')]
    VS = [('Am', 'folk'), ('G', 'folk'), ('G', 'folk'), ('Am', 'folk'),
          ('Am', 'folk'), ('Dm', 'folk'), ('Em', 'folk'), ('Am', 'folk')]
    b = []
    b += LOOP + LOOP                                    # 前奏 riff ×2
    b += VS + VS                                        # 主歌 4 行
    b += LOOP + LOOP                                    # 预副歌 do-do-do ×2
    b += VS                                             # 副歌（乐园就要拆去…）
    b += LOOP + LOOP                                    # 间奏 嘟粑哩哟粑 ×2
    b += VS                                             # 副歌2
    b += LOOP                                           # 尾 riff
    b += [('C', 'end')]
    return render(b, 128)


# ============ 3. 蒙娜丽莎的眼泪（E→C, 62, 慢歌） ============
def gen_mnls():
    b = []
    b += [(('Am', 'G'), 'ballad'), (('Am', 'G'), 'ballad'), ('E', 'ballad'), ('E', 'ballad')]  # 前奏
    b += [('Am', 'ballad'), ('G', 'ballad'), ('Am', 'ballad'), ('G', 'ballad')]                # 你看到了蒙娜丽莎的微笑
    b += [(('Am', 'G'), 'ballad'), (('F', 'G'), 'ballad'), (('C', 'G'), 'ballad'), ('Am', 'ballad')]  # 这次旅行…
    b += [('Dm', 'ballad'), ('E', 'ballad')]                                                    # 温柔的痛苦
    b += [('Am', 'ballad'), ('G', 'ballad'), ('Am', 'ballad'), ('Dm', 'ballad')]                # 因为可以和你相爱
    b += [(('Am', 'G'), 'ballad'), (('F', 'G'), 'ballad'), (('C', 'G'), 'ballad'), ('Am', 'ballad')]  # 我期待…
    b += [('Dm', 'ballad'), ('E', 'ballad')]                                                    # 苦苦的嘲笑
    b += [('Am', 'ballad'), ('G', 'ballad'), ('Dm', 'ballad'), ('G', 'ballad')]                 # 她是谁
    b += [('C', 'ballad'), ('E', 'ballad'), ('Am', 'ballad'), ('G', 'ballad')]                  # 失恋的泪水
    b += [('Am', 'ballad'), ('G', 'ballad'), ('Dm', 'ballad'), ('G', 'ballad')]                 # 她是否也曾为爱寻觅
    b += [('G', 'ballad'), ('C', 'ballad'), ('E', 'ballad'), ('Dm', 'ballad'), ('E', 'ballad')] # 微笑那么神秘
    b += [(('Am', 'G'), 'ballad'), (('Am', 'G'), 'ballad'), ('E', 'ballad'), ('E', 'ballad')]   # 间奏 在我的梦里
    b += [('Am', 'ballad'), ('G', 'ballad'), ('Dm', 'ballad'), ('G', 'ballad'),
          ('G', 'ballad'), ('C', 'ballad'), ('E', 'ballad'), ('Dm', 'ballad'), ('E', 'ballad')] # 末段
    b += [(('Am', 'G'), 'ballad'), (('Am', 'G'), 'ballad'), ('Am', 'end')]                      # 尾
    return render(b, 62)


# ============ 4. 电台情歌（bE→C, 69, 爵士慢歌） ============
def gen_dthg():
    INTRO = [(('Am', 'G'), 'ballad'), (('F', 'C'), 'ballad'),
             (('Am', 'G'), 'ballad'), (('F', 'C'), 'ballad')]
    INTRO2 = [(('Am', 'G'), 'ballad'), (('F', 'C'), 'ballad'),
              (('Am', 'G'), 'ballad'), (('F', 'E7'), 'ballad')]
    VS = [(('Am', 'G'), 'ballad'), (('F', 'C'), 'ballad'),
          (('Am', 'G'), 'ballad'), (('F', 'C'), 'ballad'),
          (('Am', 'G'), 'ballad'), (('F', 'C'), 'ballad'),
          (('Am', 'G'), 'ballad'), (('F', 'E7'), 'ballad')]
    BR = [(('F', 'Em'), 'ballad'), (('F', 'Em'), 'ballad'),
          (('Dm', 'G'), 'ballad'), ('E', 'ballad')]
    CH = VS
    b = []
    b += INTRO + INTRO2                     # 前奏 riff
    b += VS                                 # 谁能够将天上月亮电源关掉
    b += BR                                 # 我看着霓虹穿过爱情街道
    b += CH                                 # 我们一直忘了要搭一座桥
    b += VS                                 # 当两颗心放在感情天平上
    b += BR                                 # 我仰望星空走到爱情边疆
    b += CH + CH                            # 副歌×2
    b += [(('Am', 'G'), 'ballad'), (('F', 'C'), 'ballad'),
          (('Am', 'G'), 'ballad'), ('C', 'end')]   # 尾 riff
    return render(b, 69)


# ============ 5. 夜空中最亮的星（B→C, 107, 摇滚民谣） ============
def gen_yzk():
    b = []
    b += [('C', 'half'), ('F', 'half'), ('C', 'half'), ('G', 'half')]     # 前奏 riff
    for _ in range(2):                                                     # 主歌×2（听清/记起）
        b += [('C', 'folk'), ('F', 'folk'), ('Dm', 'folk'), ('C', 'folk'),
              ('Dm', 'folk'), ('C', 'folk'), ('C', 'folk'), ('Em', 'folk')]
    for _ in range(4):                                                     # 副歌×4行
        b += [('Am', 'folk'), ('F', 'folk'), ('C', 'folk'), ('G', 'folk')]
    b += [('Am', 'half'), ('F', 'half'), ('C', 'half'), ('G', 'half'),     # 间奏
          ('Am', 'half'), ('F', 'half'), ('G', 'half'), ('G', 'half')]
    b += [('C', 'folk'), ('F', 'folk'), ('Dm', 'folk'), ('C', 'folk'),     # 是否知道
          ('Dm', 'folk'), ('C', 'folk'), ('C', 'folk'), ('Em', 'folk')]
    for _ in range(4):                                                     # 副歌×4
        b += [('Am', 'folk'), ('F', 'folk'), ('C', 'folk'), ('G', 'folk')]
    b += [('Am', 'folk'), ('F', 'folk'), ('C', 'folk'), ('G', 'folk')]     # 请照亮我前行
    b += [('C', 'half'), ('F', 'half'), ('Dm', 'half'), ('C', 'end')]      # 尾 riff
    return render(b, 107)


# ============ 6. 南方姑娘（F→C, 58, 民谣叙事） ============
def gen_nfgn():
    INTRO = [('C', 'half'), ('Em', 'half'), ('Am', 'half'), ('G', 'half')]
    VS = [('C', 'half'), ('G', 'half'), ('Am', 'half'), ('Em', 'half'),
          ('F', 'half'), ('C', 'half'), ('Dm', 'half'), ('G', 'half')]
    CH = [('F', 'half'), ('G', 'half'), ('C', 'half'),
          ('F', 'half'), ('G', 'half'), ('C', 'half')]
    b = []
    b += INTRO                                   # 前奏 riff
    b += VS + VS                                 # 北方村庄 / 她的话不多
    b += VS                                      # 南方的小镇 / 不需要朦胧棉衣
    b += CH                                      # 南方姑娘 你是否习惯秋凉/直爽
    b += [('C', 'half'), ('G', 'half'), ('F', 'half'), ('G', 'half'), ('C', 'half'),
          ('C', 'half'), ('G', 'half'), ('F', 'half'), ('G', 'half'), ('C', 'half')]  # 日子/口香糖
    b += INTRO                                   # 间奏
    b += VS                                      # 昨日的雨 / 夜空北斗
    b += VS                                      # 阳光里 / 四季的风
    b += CH                                      # 你是否爱上北方 / 回到家乡
    b += [('C', 'half'), ('G', 'half'), ('F', 'half'), ('G', 'half'), ('C', 'half'),
          ('C', 'half'), ('G', 'half'), ('F', 'half'), ('G', 'half'), ('C', 'half')]  # 思念/果子
    b += [('F', 'half'), ('G', 'half'), ('C', 'half'),
          ('F', 'half'), ('G', 'half'), ('C', 'half'),
          ('F', 'half'), ('G', 'half'), ('C', 'end')]   # 呜呜呜×3 收
    return render(b, 58)


# ============ 7. 稻香（A→C, 82, 说唱民谣） ============
def gen_dx():
    LOOP = [(('C', 'G'), 'folk'), (('Am', 'Em'), 'folk'), (('Dm', 'G'), 'folk'), ('C', 'folk')]
    b = []
    b += LOOP                                                    # 前奏 riff
    b += LOOP + LOOP                                             # 主歌1 抱怨/跌倒 脆弱/电视
    b += LOOP                                                    # 知足/珍惜
    b += LOOP + LOOP + LOOP                                      # 副歌 城堡/奔跑 微笑/萤火虫 歌谣/回家
    b += LOOP + LOOP                                             # 主歌2 放弃/梦想 鲜艳上色
    b += LOOP + LOOP                                             # 功成名就/纸飞机
    b += LOOP + LOOP + LOOP                                      # 副歌
    b += LOOP                                                    # 间奏 哦哦午后吉他
    b += LOOP + LOOP + LOOP                                      # 末副歌
    b += LOOP[:-1] + [('C', 'end')]                              # 尾 riff
    return render(b, 82)


if __name__ == '__main__':
    jobs = [('那么骄傲', gen_nmdgao), ('把耳朵叫醒', gen_berjx),
            ('蒙娜丽莎的眼泪', gen_mnls), ('电台情歌', gen_dthg),
            ('夜空中最亮的星', gen_yzk), ('南方姑娘', gen_nfgn),
            ('稻香', gen_dx)]
    for song, fn in jobs:
        rows, nbars = fn()
        save(song, rows, nbars)
