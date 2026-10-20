# -*- coding: utf-8 -*-
"""chord4.com 和弦谱爬取 + 解析。

只做两件事：
  1) 搜索歌曲 -> 拿到 tab_id
  2) 抓谱页 -> 从 <pre> 文字和弦谱里解析出  Key / 段落 / 和弦进行

礼貌抓取：带浏览器 UA、限速、本地缓存，同一页只请求一次。
站点较慢（跨境），超时给到 45s 并重试 3 次。
"""
import os
import re
import time
import html
import json
import hashlib
import urllib.parse

import requests

BASE = "https://chord4.com"
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

_last_req = [0.0]
_MIN_GAP = 1.2          # 限速：两次请求至少间隔 1.2s


def _session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": BASE + "/",
    })
    return s


_S = _session()


def _get(url, cache_key, tries=3, timeout=45):
    """带缓存 + 限速 + 重试的 GET。"""
    path = os.path.join(CACHE, cache_key)
    if os.path.exists(path):
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    last = None
    for i in range(tries):
        gap = time.time() - _last_req[0]
        if gap < _MIN_GAP:
            time.sleep(_MIN_GAP - gap)
        try:
            r = _S.get(url, timeout=timeout)
            _last_req[0] = time.time()
            if r.status_code == 200 and r.text:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(r.text)
                return r.text
            last = "HTTP %s" % r.status_code
        except Exception as e:
            last = "%s: %s" % (type(e).__name__, str(e)[:60])
        time.sleep(1.5 * (i + 1))
    raise RuntimeError("请求失败 %s (%s)" % (url, last))


# ────────────────────────── 和弦符号 ──────────────────────────
# 后缀白名单式匹配：拒绝 Bridge / Intro / Verse 这类以 A-G 开头的段落词
_QUAL = (r"(?:maj|min|M|m|dim|aug|sus|add|°|\+|-)?"
         r"\d*"
         r"(?:sus\d|add\d|maj\d|b\d|#\d|maj|min)?")
_CHORD_RE = re.compile(r"^[A-G][#b]?" + _QUAL + r"(?:/[A-G][#b]?)?$")

# 谱面里需要忽略的分隔/记号
_IGNORE = set("│|─—–-·。、，,.()（）[]【】×xX%:：;；'\"“”‘’?？!！_=+*/\\<>～~")
_SECTION_WORDS = re.compile(
    r"^(intro|verse|pre-?chorus|chorus|bridge|interlude|outro|solo|riff|"
    r"repeat|end|fin|a\d?|b\d?|c\d?|"
    r"前奏|间奏|尾奏|主歌|副歌|导歌|桥段|反复|重复|结束|尾奏)\b",
    re.I)


def is_chord(tok):
    t = tok.strip()
    if not t or len(t) > 7:
        return False
    return bool(_CHORD_RE.match(t))


def _tokens(line):
    """按空白(含全角空格)切词。"""
    return [t for t in re.split(r"[\s\u3000]+", line.strip()) if t]


def _is_chord_line(line):
    """整行除了分隔符以外都是和弦 -> 判为和弦行。"""
    toks = _tokens(line)
    if not toks:
        return False
    chords = 0
    for t in toks:
        t2 = t.strip("".join(_IGNORE))
        if not t2 or _SECTION_WORDS.match(t2):
            continue
        if is_chord(t2):
            chords += 1
        elif all(c in _IGNORE for c in t2):
            continue
        else:
            return False        # 出现非和弦非分隔符 -> 不是和弦行
    return chords > 0


def _extract_chords(line):
    out = []
    for t in _tokens(line):
        t2 = t.strip("".join(_IGNORE))
        if t2 and is_chord(t2) and not _SECTION_WORDS.match(t2):
            out.append(t2)
    return out


# ────────────────────────── 抓取 ──────────────────────────
_TAB_HREF = re.compile(r'href="(?:https://chord4\.com)?/tabs/(\d+)"\s+title="([^"]*)"')
_SINGER_HREF = re.compile(r'href="(?:https://chord4\.com)?/singer/(\d+)"\s+title="([^"]*)"')


def search(keyword):
    """搜歌 -> [(tab_id, title, singer, kind)]，kind ∈ {'chord','img',''}"""
    q = urllib.parse.quote(keyword)
    url = "%s/search?search_text=%s" % (BASE, q)
    key = "search/%s.html" % hashlib.md5(keyword.encode("utf-8")).hexdigest()[:12]
    s = _get(url, key)
    hits, seen = [], set()
    for li in re.findall(r"<li>(.*?)</li>", s, re.S):
        m = _TAB_HREF.search(li)
        if not m:
            continue
        tid, title = m.group(1), html.unescape(m.group(2)).strip()
        if tid in seen:
            continue
        seen.add(tid)
        sm = _SINGER_HREF.search(li)
        singer = html.unescape(sm.group(2)).strip() if sm else ""
        low = li.lower()
        kind = "chord" if "(chord)" in low else ("img" if "(img)" in low else "")
        hits.append((tid, title, singer, kind))
    return hits


def _norm_title(t):
    t = re.sub(r"#\d+", "", t)
    t = re.sub(r"\((ver|版本)[^)]*\)", "", t, flags=re.I)
    t = re.sub(r"[（(].*?[)）]", "", t)
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", t).lower()


def pick(hits, want_title):
    """优先 (chord) 类型，其次标题最匹配。"""
    if not hits:
        return None
    w = _norm_title(want_title)

    def score(h):
        tid, title, singer, kind = h
        t = _norm_title(title)
        s = 0
        if kind == "chord":
            s += 100
        if t == w:
            s += 50
        elif w and (w in t or t in w):
            s += 25
        return s
    return max(hits, key=score)


def fetch_sheet(tab_id):
    """抓谱页 -> (原始谱面文本, 页面HTML)"""
    url = "%s/tabs/%s" % (BASE, tab_id)
    s = _get(url, "tabs/%s.html" % tab_id)
    pres = re.findall(r"<pre[^>]*>(.*?)</pre>", s, re.S)
    if not pres:
        raise RuntimeError("没找到 <pre> 谱面")
    # 谱面 = 最长的那个 pre（第一个常常是 ASCII 小图）
    best = max(pres, key=lambda x: len(re.sub(r"<[^>]+>", "", x)))
    text = html.unescape(re.sub(r"<[^>]+>", "", best))
    return text, s


def page_meta(s):
    """从页面里取歌名与歌手。"""
    t = re.search(r"<title>(.*?)</title>", s, re.S)
    title = html.unescape(t.group(1)).split("-")[0].strip() if t else ""
    singers = []
    m = re.search(r'<h1[^>]*>(.*?)</h1>', s, re.S)
    return title, singers


# ────────────────────────── 解析 ──────────────────────────
_KEY_RE = re.compile(r"Key\s*[=:：]\s*([A-G][#b]?)", re.I)
_SIG_RE = re.compile(r"\b([1-9]\d?/\d)\b")


def parse_sheet(text, title="", singer=""):
    """把文字和弦谱解析成结构化进行。

    返回:
      {title, singer, key, time_sig, sections:[{label, chords:[...]}]}
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    key, ts = "", "4/4"
    m = _KEY_RE.search(text)
    if m:
        key = m.group(1)
    m = _SIG_RE.search(text)
    if m:
        ts = m.group(1)

    sections = []

    def section(label):
        cur = {"label": label or ("段落%d" % (len(sections) + 1)), "chords": []}
        sections.append(cur)
        return cur

    cur = section("主歌")
    # 头部信息区（Key/作曲/谱源）里可能有和弦，丢弃
    started = False

    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            continue
        stripped = line.strip("".join(_IGNORE)).strip()
        if not stripped:
            continue

        # 段落标签：整行就是一个段落词（可带 : ）
        lab = stripped.rstrip(":：").strip()
        if _SECTION_WORDS.match(lab) and len(_tokens(line)) <= 2 and not _is_chord_line(line):
            started = True
            cur = section(lab)
            continue

        # Repeat 行
        if re.match(r"^(repeat|反复|重复|再来)", lab, re.I):
            # 复用上一段
            if len(sections) >= 2:
                prev = sections[-2]
                cur = {"label": prev["label"], "chords": list(prev["chords"])}
                sections.append(cur)
            continue

        if not started:
            # 头部还没结束；如果这行是纯和弦(前奏)则开始
            if not _is_chord_line(line):
                continue
            started = True
            cur = section("前奏")

        if _is_chord_line(line):
            ch = _extract_chords(line)
            if ch:
                cur["chords"].extend(ch)

    # 去掉空段
    sections = [s for s in sections if s["chords"]]
    return {"title": title, "singer": singer, "key": key,
            "time_sig": ts, "sections": sections}


def all_chords(parsed):
    seen, out = set(), []
    for s in parsed["sections"]:
        for c in s["chords"]:
            if c not in seen:
                seen.add(c)
                out.append(c)
    return out


if __name__ == "__main__":
    import sys
    for kw in sys.argv[1:]:
        print("=" * 70)
        print("搜索:", kw)
        try:
            hits = search(kw)
        except Exception as e:
            print("  搜索失败:", e)
            continue
        if not hits:
            print("  无结果")
            continue
        hit = pick(hits, kw)
        tid, title, singer, kind = hit
        print("  命中: %s  《%s》 - %s  [%s]  (共%d个版本)" % (tid, title, singer, kind, len(hits)))
        try:
            text, page = fetch_sheet(tid)
            p = parse_sheet(text, title, singer)
        except Exception as e:
            print("  抓取/解析失败:", e)
            continue
        print("  Key=%s  %s  段落数=%d" % (p["key"], p["time_sig"], len(p["sections"])))
        for s in p["sections"][:8]:
            print("    [%s] %s" % (s["label"], " ".join(s["chords"][:24])))
        print("  和弦集合:", " ".join(all_chords(p)))
