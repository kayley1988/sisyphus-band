#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""auto_play_show.py — 自动演奏一首歌 + 1.54" 彩屏「演奏主界面」。

屏幕版式（Z 2026-10-10 需求）
────────────────────────────────
    顶部 : 歌名（32px 亮黄，居中，醒目）
    上部 : 和弦标签带 —— 和弦变化点标名字，跟着音浪一起从右到左滚
    中部 : 音浪区 —— 竖柱从中线上下对称展开，整体**从右到左**滚动
    底部 : 6 弦压弦符号（按了的弦亮青 + 品位数字）+ 当前和弦名 + 进度条
    播放头: 左侧白色竖线（左边 = 刚过去，右边 = 即将到来）

⚠️ 时间轴实现已抽到 `src/guitarist/perform_timeline.py`
   管理页 `POST /api/songs/play` 用的是**同一份**，两处口径不会有偏差。
   （本脚本 = 命令行/调试入口；日常切换请看管理页「歌曲管理 → ▶ 演奏」）

用法
────────────────────────────────
    python3 auto_play_show.py                    # 默认《稻香》
    python3 auto_play_show.py 后来
    python3 auto_play_show.py 稻香 --no-lcd      # 只演奏不刷屏
    python3 auto_play_show.py 稻香 --window 8    # 屏幕覆盖 8 秒（默认 7）

前提: 先停 guitar 服务释放 UART3 / GPIO
      sh /root/guitarist_app/scripts/board_dev.sh stop
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src", "guitarist"))

os.environ.setdefault("GUITAR_USE_LCD", "1")   # 默认走彩屏，跳过 OLED/I2C

SCR_W = 240


def _load_fonts():
    """优先用 hardware.fonts；失败则自己加载 NotoSansCJK。"""
    try:
        import hardware
        return hardware.fonts, "hardware.fonts"
    except Exception as e:
        print("[lcd] import hardware 失败(%s)，改用直接加载字体" % e, flush=True)
        from PIL import ImageFont
        p = "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"
        try:
            return {
                "font": ImageFont.truetype(p, 16),
                "font_small": ImageFont.truetype(p, 12),
                "font_tiny": ImageFont.truetype(p, 10),
            }, "direct"
        except Exception as e2:
            print("[lcd] 字体也加载失败: %s" % e2, flush=True)
            return None, "none"


def main():
    positional = [a for a in sys.argv[1:] if not a.startswith("--")]
    song = positional[0] if positional else "稻香"
    use_lcd = "--no-lcd" not in sys.argv

    window_s = 7.0
    head_x = 26
    fps = 6.0
    for i, a in enumerate(sys.argv):
        if a == "--window" and i + 1 < len(sys.argv):
            window_s = float(sys.argv[i + 1])
        elif a == "--head" and i + 1 < len(sys.argv):
            head_x = int(sys.argv[i + 1])
        elif a == "--fps" and i + 1 < len(sys.argv):
            fps = float(sys.argv[i + 1])
    window_ms = max(1000.0, window_s * 1000.0)

    import perform_timeline as ptl
    import esp32_player as ep
    from servo_controller import ServoController

    # ── 1) 曲谱 + 时间轴 ──
    sc = ServoController(os.path.join(HERE, "config", "servo_config.csv"))
    tl = ptl.load_song(song, sc)
    if tl is None:
        print("[play] 找不到曲谱: 歌曲列表/%s/*/输出结果*.csv" % song, flush=True)
        return 1
    print("[play] 曲谱 %s：%d 音符 / %.0fs / BPM %s / 拍号 %s / 和弦变化 %d 处"
          % (song, len(tl), tl.total_ms / 1000.0, tl.bpm, tl.ts, tl.chord_changes),
          flush=True)
    total_ms = tl.total_ms

    # ── 2) 彩屏 ──
    renderer = None
    if use_lcd:
        try:
            fonts, src = _load_fonts()
            if fonts:
                from lcd_renderer import LCDRenderer
                renderer = LCDRenderer(fonts, perform_window_s=window_s)
                renderer.show_perform_panel(song, 0, [], chord_now="准备",
                                            frets_now=[0] * 6, head_x=head_x)
                print("[lcd] 彩屏就绪 (fonts=%s)" % src, flush=True)
        except Exception as e:
            print("[lcd] 初始化失败: %s" % e, flush=True)
            renderer = None

    # ── 3) 开演 ──
    print("[play] 开始演奏《%s》(全盘模式: 压弦+拨弦)..." % song, flush=True)
    started = ep.esp32_play_song(song, servo_controller=sc)
    print("[play] started = %s" % started, flush=True)
    if not started:
        print("[play] 启动失败 —— 检查 ESP32 是否在线 / 曲谱 CSV 是否存在", flush=True)
        if renderer:
            renderer.show_perform_panel(song, 0, [], chord_now="失败")
        return 1

    t0 = time.time()
    last_pct = -1
    frame_dt = 1.0 / max(1.0, fps)
    next_frame = 0.0

    while ep.esp32_is_playing():
        now = time.time()
        t_ms = (now - t0) * 1000.0

        # ESP32 整数百分比做粗校准（掉帧/缓冲不脱同步）
        p_esp = ep.esp32_get_progress()
        if p_esp and p_esp > 0:
            p_local = t_ms / total_ms * 100.0
            if abs(p_local - p_esp) > 5.0:
                t0 = now - (p_esp / 100.0 * total_ms) / 1000.0
                t_ms = (time.time() - t0) * 1000.0

        pct = int(max(0.0, min(100.0, t_ms / total_ms * 100.0)))
        if pct != last_pct:
            print("[play] 进度 %3d%%   (%5.0fs)" % (pct, now - t0), flush=True)
            last_pct = pct

        if renderer is not None and now >= next_frame:
            next_frame = now + frame_dt
            try:
                events = tl.events_at(t_ms, window_ms, head_x=head_x, scr_w=SCR_W)
                cur = tl.current_at(t_ms)
                renderer.show_perform_panel(
                    song, pct, events,
                    chord_now=(cur["name"] if cur and cur.get("name") else "—"),
                    frets_now=(cur["frets"] if cur else [0] * 6),
                    head_x=head_x)
            except Exception as e:
                print("[lcd] 刷新失败，停止刷屏: %s" % e, flush=True)
                renderer = None

        time.sleep(0.02)
        if time.time() - t0 > 900:
            print("[play] 超时 900s，强制停止", flush=True)
            try:
                ep.esp32_stop_song()
            except Exception:
                pass
            break

    used = int(time.time() - t0)
    print("[play] 播放结束 用时 %ds 进度 %s%%" % (used, ep.esp32_get_progress()), flush=True)

    if renderer is not None:
        try:
            renderer.show_perform_panel(song, 100, [],
                                        chord_now="完成", frets_now=[0] * 6,
                                        head_x=head_x)
        except Exception:
            pass

    try:
        ep._player.close()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
