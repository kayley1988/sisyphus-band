# -*- coding: utf-8 -*-
"""枚举窗口 + 用 PrintWindow 抓取指定窗口的画面（绕过屏幕 BitBlt 受限）。

用法:
  python _gui_cap.py list
  python _gui_cap.py cap <标题关键字> <输出png>
  python _gui_cap.py click <x> <y>          # 相对窗口客户区
  python _gui_cap.py clickwin <标题关键字> <x> <y>
"""
import ctypes, ctypes.wintypes as w, sys, time, os

u = ctypes.windll.user32
gdi = ctypes.windll.gdi32
user32 = u

PW_RENDERFULLCONTENT = 0x00000002

class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, w.HWND, w.LPARAM)

def enum_windows():
    out = []
    def cb(hwnd, lparam):
        if not u.IsWindowVisible(hwnd):
            return True
        n = u.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(hwnd, buf, n + 1)
        cls = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(hwnd, cls, 256)
        r = RECT()
        u.GetWindowRect(hwnd, ctypes.byref(r))
        out.append((hwnd, buf.value, cls.value, (r.left, r.top, r.right, r.bottom)))
        return True
    u.EnumWindows(WNDENUMPROC(cb), 0)
    return out

def find(kw):
    kws = kw.lower()
    for hwnd, title, cls, rect in enum_windows():
        if kws in title.lower() or kws in cls.lower():
            return hwnd, title, cls, rect
    return None

def capture(hwnd, path):
    r = RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    wd, ht = r.right - r.left, r.bottom - r.top
    if wd <= 0 or ht <= 0:
        return None, "窗口尺寸无效 %dx%d" % (wd, ht)
    hdc = u.GetWindowDC(hwnd)
    mem = gdi.CreateCompatibleDC(hdc)
    bmp = gdi.CreateCompatibleBitmap(hdc, wd, ht)
    old = gdi.SelectObject(mem, bmp)
    ok = u.PrintWindow(hwnd, mem, PW_RENDERFULLCONTENT)
    # 取位图数据
    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", w.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                    ("biPlanes", w.WORD), ("biBitCount", w.WORD), ("biCompression", w.DWORD),
                    ("biSizeImage", w.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                    ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", w.DWORD), ("biClrImportant", w.DWORD)]
    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.biWidth = wd
    bi.biHeight = -ht          # top-down
    bi.biPlanes = 1
    bi.biBitCount = 32
    bi.biCompression = 0
    bufsz = wd * ht * 4
    buf = ctypes.create_string_buffer(bufsz)
    got = gdi.GetDIBits(mem, bmp, 0, ht, buf, ctypes.byref(bi), 0)
    gdi.SelectObject(mem, old)
    gdi.DeleteObject(bmp)
    gdi.DeleteDC(mem)
    u.ReleaseDC(hwnd, hdc)
    if got == 0:
        return None, "GetDIBits 失败"
    from PIL import Image
    im = Image.frombuffer("RGBA", (wd, ht), buf, "raw", "BGRA", 0, 1).convert("RGB")
    im.save(path)
    return (wd, ht, ok), None

def click_at(hwnd, x, y):
    """把窗口客户区坐标转成屏幕坐标后点击"""
    p = POINT(x, y)
    u.ClientToScreen(hwnd, ctypes.byref(p))
    u.SetCursorPos(p.x, p.y)
    time.sleep(0.15)
    u.mouse_event(0x0002, 0, 0, 0, 0)   # LEFTDOWN
    time.sleep(0.06)
    u.mouse_event(0x0004, 0, 0, 0, 0)   # LEFTUP
    return p.x, p.y

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        for hwnd, title, cls, rect in enum_windows():
            if title.strip():
                print("0x%08X | %-40s | %-28s | %s" % (hwnd, title[:40], cls[:28], rect))
    elif cmd == "cap":
        kw = sys.argv[2]
        out = sys.argv[3] if len(sys.argv) > 3 else "_print_job/gui_cap.png"
        r = find(kw)
        if not r:
            print("未找到窗口:", kw); sys.exit(1)
        hwnd, title, cls, rect = r
        print("窗口 0x%08X %s (%s) rect=%s" % (hwnd, title, cls, rect))
        res, err = capture(hwnd, out)
        if err:
            print("抓取失败:", err); sys.exit(1)
        print("已保存 %s 尺寸=%s" % (out, res))
    elif cmd == "btn":
        """列出指定窗口的子控件（含文本与矩形）"""
        kw = sys.argv[2]
        r = find(kw)
        if not r:
            print("未找到窗口:", kw); sys.exit(1)
        hwnd = r[0]
        kids = []
        CB = ctypes.WINFUNCTYPE(ctypes.c_bool, w.HWND, w.LPARAM)
        def cb(h, lp):
            t = ctypes.create_unicode_buffer(256)
            u.GetWindowTextW(h, t, 256)
            c = ctypes.create_unicode_buffer(256)
            u.GetClassNameW(h, c, 256)
            rr = RECT()
            u.GetWindowRect(h, ctypes.byref(rr))
            vis = bool(u.IsWindowVisible(h))
            kids.append((h, t.value, c.value, (rr.left, rr.top, rr.right, rr.bottom), vis))
            return True
        u.EnumChildWindows(hwnd, CB(cb), 0)
        for h, t, c, rr, vis in kids:
            if t.strip() or c.lower() == "button":
                print("0x%08X | %-24s | %-14s | %s | 可见=%s" % (h, t[:24], c[:14], rr, vis))
        if not kids:
            print("(无子控件)")
    elif cmd == "press":
        """press <窗口关键字> <按钮文本> —— 找到按钮并点击其中心"""
        kw, btxt = sys.argv[2], sys.argv[3]
        r = find(kw)
        if not r:
            print("未找到窗口:", kw); sys.exit(1)
        hwnd = r[0]
        CB = ctypes.WINFUNCTYPE(ctypes.c_bool, w.HWND, w.LPARAM)
        hit = []
        def cb(h, lp):
            t = ctypes.create_unicode_buffer(256)
            u.GetWindowTextW(h, t, 256)
            if btxt in t.value:
                rr = RECT()
                u.GetWindowRect(h, ctypes.byref(rr))
                hit.append((h, t.value, rr))
            return True
        u.EnumChildWindows(hwnd, CB(cb), 0)
        if not hit:
            print("未找到按钮:", btxt); sys.exit(1)
        h, t, rr = hit[0]
        cx, cy = (rr.left + rr.right) // 2, (rr.top + rr.bottom) // 2
        u.SetForegroundWindow(hwnd)
        time.sleep(0.2)
        u.SetCursorPos(cx, cy)
        time.sleep(0.2)
        u.mouse_event(0x0002, 0, 0, 0, 0)
        time.sleep(0.06)
        u.mouse_event(0x0004, 0, 0, 0, 0)
        print("已点击 [%s] @ 屏幕(%d,%d)" % (t, cx, cy))
    elif cmd == "focus":
        """把匹配的窗口带到前台"""
        kw = sys.argv[2]
        r = find(kw)
        if not r:
            print("未找到窗口:", kw); sys.exit(1)
        hwnd = r[0]
        u.ShowWindow(hwnd, 9)          # SW_RESTORE
        u.SetForegroundWindow(hwnd)
        u.BringWindowToTop(hwnd)
        time.sleep(0.4)
        print("已前置窗口 0x%08X %s, 前台现在=0x%X" % (
            hwnd, r[1], u.GetForegroundWindow()))
    elif cmd == "clickxy":
        """绝对屏幕坐标点击"""
        x, y = int(sys.argv[2]), int(sys.argv[3])
        u.SetCursorPos(x, y)
        time.sleep(0.15)
        u.mouse_event(0x0002, 0, 0, 0, 0)
        time.sleep(0.06)
        u.mouse_event(0x0004, 0, 0, 0, 0)
        print("已在屏幕 (%d,%d) 点击" % (x, y))
    elif cmd == "clickwin":
        kw = sys.argv[2]
        x, y = int(sys.argv[3]), int(sys.argv[4])
        r = find(kw)
        if not r:
            print("未找到窗口:", kw); sys.exit(1)
        hwnd = r[0]
        print("点击窗口 0x%08X 客户区(%d,%d) -> 屏幕%s" % (hwnd, x, y, click_at(hwnd, x, y)))
