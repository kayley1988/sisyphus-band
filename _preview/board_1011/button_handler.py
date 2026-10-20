#!/usr/bin/env python3
from logger import get_logger
_log = get_logger("button")
# -*- coding: utf-8 -*-
"""
按键处理模块 — 按键检测、去抖、短按/长按逻辑

从主循环提取的独立按键处理模块:
  - check_buttons()           — 主按键检测函数 (每帧调用一次)
  - button_detection_thread() — 后台按键检测线程
  - handle_up/down/set()     — 菜单导航与功能触发
  - handle_esc_short()       — ESC 短按返回上级菜单

去抖策略:
  - 基础去抖: 20ms 间隔 (debounce_time)
  - SET 冷却: 300ms 间隔 (set_cooldown) 防止重复触发
  - ESC 短按: 释放时 dur < 2s 且非 MAIN↔MENU 切换
  - ESC 长按: MAIN↔MENU 需要 2s 长按，其他场景 0.15s 即触发
"""

import time
import threading

from state import (
    get_state,
    get_all_state,
    update_state,
    STATE_MAIN,
    STATE_MENU,
    STATE_FUNCTION,
    STATE_CALIBRATION,
    STATE_TUNER,
    STATE_WIFI_SCAN,
    STATE_WIFI_CONN,
    STATE_WIFI_SCANING,
    STATE_SONG_PICK,
    STATE_VOICE_DOWNLOAD_CONFIRM,
    STATE_VOICE_DOWNLOAD_PROGRESS,
)
from hardware import up_button, down_button, set_button, esc_button, is_pressed
from menu_system import MENU_ITEMS, get_music_folders
from voice_player import play_prompt
from mode_toggle import DoubleClickDetector, toggle_performance_mode, stamp_toast

# ── 语音下载确认对话框辅助函数 ──
_STEP_NAMES = {
    0: "初始化", 1: "搜索乐谱", 2: "六线谱定位", 3: "和弦识别",
    4: "OCR识别", 5: "音符定位", 6: "符尾识别", 7: "数据合并", 8: "AI纠错",
}


def _start_voice_download():
    """启动语音触发的下载流程（由确认对话框的 UP 键触发）"""
    import threading as _th
    from screen_functions import _voice_player as vp

    # ★ 防重入：上一次下载线程仍在运行（如取消 UI 后遗留的后台任务）则拒绝，
    # 避免两个流水线子进程并发写入同一个 图片源N 目录导致文件互相覆盖
    prev = get_state("voice_download_thread")
    if prev is not None and prev.is_alive():
        _log.warning("已有下载任务正在运行，忽略新的下载请求")
        return

    song_name = get_state("voice_download_song_name")
    update_state("current_state", 11)  # STATE_VOICE_DOWNLOAD_PROGRESS
    update_state("calibration_title", f"下载 {song_name}")
    update_state("calibration_progress", 0)
    update_state("calibration_status", "正在搜索乐谱...")
    update_state("display_dirty", True)
    update_state("voice_download_result", None)

    # 异步播放"开始下载"
    _th.Thread(target=lambda: play_prompt("download_started"), daemon=True).start()

    def _download_thread():
        def on_progress(step, step_name, detail, overall_pct):
            if step == 'log':
                update_state("calibration_status", detail[:30] if detail else "")
            else:
                name = _STEP_NAMES.get(step, step_name or f"步骤{step}")
                status = f"{name}: {detail[:20]}" if detail else name
                update_state("calibration_status", status)
                if overall_pct:
                    update_state("calibration_progress", float(overall_pct))

        success, error_msg = vp.download_and_recognize(
            song_name, progress_callback=on_progress)

        if success:
            update_state("calibration_progress", 100)
            update_state("calibration_status", "下载完成!")
            update_state("voice_download_result", True)
        else:
            update_state("voice_download_result", error_msg)

    t = _th.Thread(target=_download_thread, daemon=True)
    update_state("voice_download_thread", t)
    t.start()


def _cancel_voice_download():
    """取消语音下载（由确认对话框的 DOWN/ESC 键或超时触发）"""
    import threading as _th

    update_state("current_state", 0)  # STATE_MAIN
    update_state("display_dirty", True)
    update_state("voice_download_countdown", 0)
    _th.Thread(target=lambda: play_prompt("download_cancelled"), daemon=True).start()

# ──────────── 模块级按键状态（避免全局变量） ────────────
# ── 按键有效电平：已收口到 hardware.BTN_ACTIVE_HIGH / hardware.is_pressed() ──
#   2026-10-11（本轮）：screen_functions.py / screens/common.py / mode_toggle.py
#   里还散着十几处**直读** `not X_button.value`（老的低有效写法）。在 TTP223
#   （触摸 = 高）下它们**整体反向**——不碰反而全被判定为"按下"，于是：
#     · 演奏循环把悬空的 ESC 当成"一直按着" → 开弹 0.15s 就被自己中断；
#     · 各功能屏按键判定错乱（表现为"自己乱跑""怎么按都没反应"）。
#   为避免"同一电平语义散落多处、改一处漏一处"，电平表与折算函数统一由
#   hardware 提供，本模块只保留 _pressed 这个转发别名（历史调用点全部不变）。
def _pressed(name: str) -> bool:
    """统一按压读取（转发 hardware.is_pressed，保留历史调用点）。"""
    return is_pressed(name)


debounce_time = 0.02
_last_up = False
_last_down = False
_last_set = False
_last_diag_log = 0.0     # btn.diag 心跳节流
_last_esc = False
_last_action_time = 0.0
_set_press_start = 0.0

# ── 双击 SET → 一键切「全自动弹唱 / 人机配合」（Z 2026-10-10 想法）──
#    只在主界面生效；整曲演奏中由 screen_functions.play_song 的播放循环单独挂钩。
_set_double = DoubleClickDetector()
_ptt_takes_set = False          # 唤醒方式=按键 时，SET 归 push-to-talk → 双击让位
_ptt_takes_set_logged = False

# ── 选歌屏：上/下按住连发（曲库上百首时一格一格按不现实）──
_song_repeat_delay = 0.45       # 按住多久后开始连发
_song_repeat_rate = 0.12        # 连发间隔
_song_up_next = 0.0
_song_down_next = 0.0
_song_idle_start = 0.0          # 选歌屏无输入起点（0=刚有输入）；ESC 暂缺时的自动退回用


# ──────────── 按键动作处理 ────────────
def _is_ptt_mode(ai_ctx) -> bool:
    """唤醒方式=按键 时 SET 是 push-to-talk 键 → 双击切换必须让位，避免互相打架。

    现场口径（37 号笔记）：本机无麦、AI 整体关闭 → 实际上这里几乎总是 False，
    SET 是空闲的。保留判断只为将来打开语音唤醒时不发生行为突变。
    """
    try:
        return bool(ai_ctx) and ai_ctx.get_config("wake_method") == "button"
    except Exception:
        return False


def _do_mode_toggle():
    """双击 SET 的实际动作：切模式 + 记一条待渲染的提示（渲染交给主循环）。"""
    res = toggle_performance_mode()
    try:
        update_state("ui_toast", stamp_toast(res.get("lines")))
        update_state("display_dirty", True)
    except Exception:
        pass
    if res.get("ok"):
        _log.info("[mode] 双击 SET -> %s (%s)", res.get("mode"), res.get("label"))
    else:
        _log.warning("[mode] 双击 SET 切换失败: %s", res.get("reason"))
    return res


# ──────────── 选歌屏（主界面短按 上/下 呼出）────────────────────
# 曲库数据来自 song_library.scan_songs()：**只列"真有可用 CSV"的歌**，
# 版本（图片源N）由它自动挑好——这就是为什么这里没有"选版本"这一层。
# 详见 song_library 模块注释（旧逻辑写死图片源1，《晴天》必失败的坑）。

def _song_pick_move(delta):
    songs = get_state("song_list") or []
    n = len(songs)
    if not n:
        return
    update_state("song_index", (get_state("song_index") + delta) % n)
    update_state("display_dirty", True)


def open_song_pick():
    """主界面短按 上/下 → 呼出选歌屏。

    每次呼出都**现扫**曲库（几十首歌 <50ms，不缓存）：PC 上刚转换好的歌
    同步上板后立刻出现在列表最前（按 CSV 修改时间倒序）。
    """
    global _song_up_next, _song_down_next, _song_idle_start
    _log.info("[songpick] 呼入选歌屏（入口日志，先于扫描）")
    import song_library
    res = song_library.scan_songs()
    songs = res["songs"]
    update_state("song_list", songs)
    update_state("song_index", 0)
    update_state("current_state", STATE_SONG_PICK)
    update_state("display_dirty", True)
    _set_double.reset()   # 清掉可能残留的"半次双击"，避免回主界面后误切模式
    # ★ 呼出用的那颗上/下键此刻还按着——必须把它的连发计时器推到"从现在起
    #   再等 0.45s"，否则计时器里的旧值(0)会让下一次轮询立刻再移一格，
    #   表现成"一按键就跳两格"（单测第 1/2 组抓到的就是这个）。
    _now = time.time()
    _song_up_next = _now + _song_repeat_delay
    _song_down_next = _now + _song_repeat_delay
    _song_idle_start = 0.0
    if not songs:
        _log.warning("[songpick] 曲库里没有能弹的歌 (root=%s)", res.get("root"))
    else:
        skipped = [s["name"] for s in res.get("skipped", [])]
        _log.info("[songpick] 可弹 %d 首%s", len(songs),
                  ("，跳过(无CSV): %s" % ",".join(skipped)) if skipped else "")
    return len(songs)


def close_song_pick():
    """ESC → 退回主界面。"""
    update_state("current_state", STATE_MAIN)
    update_state("display_dirty", True)
    _set_double.reset()


def _play_picked_song():
    """选歌屏里 SET = 开弹。版本已挑好，直接进演奏，不再过确认框。"""
    songs = get_state("song_list") or []
    idx = get_state("song_index")
    if not songs or not (0 <= idx < len(songs)):
        return
    song = songs[idx]
    _log.info("[songpick] 选定《%s》（%s）", song["name"], song["source"])
    close_song_pick()   # 先退屏再播：演奏结束的归位逻辑（skip_confirm）会回主界面
    import screen_functions
    screen_functions.play_song(song["name"], song["source"], skip_confirm=True)

def handle_esc_short():
    """短按 Esc 处理：返回上一级菜单（原子读取状态避免竞态）"""
    all_state = get_all_state()
    cs = all_state["current_state"]
    if cs == STATE_MENU:
        stack = list(all_state["menu_stack"])
        if stack:
            prev_menu, prev_index = stack.pop()
            update_state("menu_stack", stack)
            update_state("current_menu", prev_menu)
            update_state("selected_index", prev_index)
            update_state("display_dirty", True)
    elif cs == STATE_FUNCTION:
        update_state("current_state", STATE_MENU)
        update_state("display_dirty", True)


def handle_up():
    """上键：菜单项向上导航；主界面 = 呼出选歌屏"""
    if get_state("current_state") == STATE_MENU:
        menu = MENU_ITEMS.get(get_state("current_menu"))
        if menu and "items" in menu and menu["items"]:
            idx = (get_state("selected_index") - 1) % len(menu["items"])
            update_state("selected_index", idx)
            update_state("display_dirty", True)
    elif get_state("current_state") == STATE_MAIN:
        open_song_pick()


def handle_down():
    """下键：菜单项向下导航；主界面 = 呼出选歌屏"""
    if get_state("current_state") == STATE_MENU:
        menu = MENU_ITEMS.get(get_state("current_menu"))
        if menu and "items" in menu and menu["items"]:
            idx = (get_state("selected_index") + 1) % len(menu["items"])
            update_state("selected_index", idx)
            update_state("display_dirty", True)
    elif get_state("current_state") == STATE_MAIN:
        open_song_pick()


def handle_set():
    """SET 按键：菜单进入子菜单或执行功能"""
    if get_state("current_state") != STATE_MENU:
        return
    menu = MENU_ITEMS.get(get_state("current_menu"))
    if not menu or "items" not in menu:
        return
    items = menu["items"]
    idx = get_state("selected_index")
    if idx >= len(items):
        return
    item = items[idx]

    if item["type"] == "menu":
        if item["target"] == "music_menu":
            folders = get_music_folders()
            MENU_ITEMS["music_menu"]["items"] = [
                {"name": n, "type": "menu", "target": f"music_source_{n}"}
                for n in folders
            ]
        # 动态创建歌曲子菜单（列出该歌曲下的图片源）
        if item["target"].startswith("music_source_"):
            song_name = item["target"].replace("music_source_", "")
            from menu_system import get_song_sources
            sources = get_song_sources(song_name)
            if not sources:
                sources = ["图片源1"]  # fallback
            MENU_ITEMS[item["target"]] = {
                "title": f"《{song_name}》- 选择版本",
                "items": [
                    {"name": s, "type": "function", "func": "play_song",
                     "args": [song_name, s]}
                    for s in sources
                ],
            }
        stack = get_state("menu_stack")
        stack.append((get_state("current_menu"), get_state("selected_index")))
        update_state("menu_stack", stack)
        update_state("current_menu", item["target"])
        update_state("selected_index", 0)
        update_state("display_dirty", True)
        return

    elif item["type"] == "function":
        # 延迟导入 screen_functions：避免启动时加载 servo_controller/guitar_tuner 等重型模块
        import screen_functions

        func = getattr(screen_functions, item["func"], None)
        if not callable(func):
            return
        args = item.get("args", [])
        update_state("running_function", True)
        is_special = item["func"] in (
            "strum_servo_calibration",
            "chord_servo_calibration",
            "servo_test_program",
            "guitar_tuner_ui",
        )
        if not is_special:
            update_state("current_state", STATE_FUNCTION)
        update_state("display_dirty", True)

        try:
            func(*args)
        except Exception as e:
            _log.error("按钮处理异常: %s", e, exc_info=True)
        finally:
            # 校准和调音器自己管理状态，不在此覆盖
            if get_state("current_state") not in (STATE_CALIBRATION, STATE_TUNER):
                update_state("running_function", False)
                update_state("current_state", STATE_MENU)
                update_state("display_dirty", True)


# ──────────── 主按键检测循环 ────────────
def check_buttons(ai_ctx=None):
    """
    主按键检测函数，每个主循环调用一次。
    ai_ctx: AI 上下文对象（用于 PTT 按钮触发）
    """
    global _last_up, _last_down, _last_set, _last_esc
    global _last_action_time, _set_press_start
    global _ptt_takes_set, _ptt_takes_set_logged
    global _song_up_next, _song_down_next, _song_idle_start

    # 按钮未初始化（GPIO 被占用）→ 安全跳过，不崩溃
    if up_button is None or down_button is None or set_button is None or esc_button is None:
        return
    # ★ 停机信号：deinit 之后 btn.value 会抛 AttributeError。线程循环上的守卫
    #   不够 —— 它可能**已经进到 check_buttons 里**（一次迭代里有 I2C/OLED 操作，
    #   耗时可能远超一个循环周期），所以真正的守卫必须在这里。实测证明：
    #   只加循环守卫时，退出仍会打印 `set_button.value` 的 AttributeError。
    if _buttons_stopping.is_set():
        return

    current_time = time.time()
    cs = get_state("current_state")

    # ── 诊断心跳（2026-10-11 排查"四键全无反应"）：每 2s 打一条
    #   状态机状态 + running_function + 四键原始电平。放在一切 early-return
    #   **之前**，线程活着就一定有输出；按键按下电平变化也会被看见。
    #   定位问题后删除或降频。
    global _last_diag_log
    if current_time - _last_diag_log >= 2.0:
        _last_diag_log = current_time
        try:
            _log.info("[btn.diag] cs=%s running=%s up=%s down=%s set=%s esc=%s",
                      cs, get_state("running_function"),
                      up_button.value, down_button.value,
                      set_button.value, esc_button.value)
        except Exception:
            _log.error("[btn.diag] 读引脚失败", exc_info=True)

    # 如果 running_function 且不是校准/调音状态，则跳过
    if get_state("running_function") and cs not in (STATE_CALIBRATION, STATE_TUNER):
        return
    if cs in [STATE_WIFI_SCAN, STATE_WIFI_CONN, STATE_WIFI_SCANING]:
        return

    # ── 语音下载确认对话框的按键处理 ──
    # （STATE_VOICE_DOWNLOAD_CONFIRM / PROGRESS 已从 state 正常导入，
    #   原先这里的"本地引用"补丁漏了 PROGRESS，是导致按键线程 NameError 崩溃的根因）
    if cs == STATE_VOICE_DOWNLOAD_CONFIRM:
        # UP 键 → 确认下载
        current_up = _pressed("up")
        if current_up and not _last_up:
            _last_up = current_up
            _last_action_time = current_time
            _start_voice_download()
            return
        _last_up = current_up

        # DOWN 键 → 取消
        current_down = _pressed("down")
        if current_down and not _last_down:
            _last_down = current_down
            _last_action_time = current_time
            _cancel_voice_download()
            return
        _last_down = current_down

        # ESC 键 → 取消（同 DOWN）
        if _pressed("esc"):
            if get_state("esc_press_start") == 0:
                update_state("esc_press_start", current_time)
            elif current_time - get_state("esc_press_start") >= 0.15:
                update_state("esc_long_press", True)
                _cancel_voice_download()
        else:
            if get_state("esc_press_start") > 0:
                update_state("esc_press_start", 0)
        return

    # ---- 选歌屏（STATE_SONG_PICK）：独立处理块 ----
    #   不并入下方通用分支的三个原因：
    #   · UP/DOWN 在主界面是"呼出选歌"，进了选歌屏语义变成"移动高亮"；
    #   · ESC 这里要"轻点即退"，通用分支对非 MAIN/MENU 状态是 0.15s 长按语义；
    #   · SET 这里是"开弹"，且必须与双击 SET 切模式完全互斥——选歌屏打开时
    #     cs≠STATE_MAIN，那段检测代码天然不跑；两边再各自 reset，双保险。
    if cs == STATE_SONG_PICK:
        # 上：按下即动一次，按住 0.45s 后以 0.12s 间隔连发（曲库上百首用）
        current_up = _pressed("up")
        if current_up:
            if not _last_up:
                _song_up_next = current_time + _song_repeat_delay
                _last_action_time = current_time
                _song_pick_move(-1)
            elif current_time >= _song_up_next:
                _song_up_next = current_time + _song_repeat_rate
                _last_action_time = current_time
                _song_pick_move(-1)
        else:
            _song_up_next = 0.0
        _last_up = current_up

        current_down = _pressed("down")
        if current_down:
            if not _last_down:
                _song_down_next = current_time + _song_repeat_delay
                _last_action_time = current_time
                _song_pick_move(+1)
            elif current_time >= _song_down_next:
                _song_down_next = current_time + _song_repeat_rate
                _last_action_time = current_time
                _song_pick_move(+1)
        else:
            _song_down_next = 0.0
        _last_down = current_down

        # SET：确定开弹（沿用 set_cooldown 防抖，与菜单一致）
        current_set = _pressed("set")
        if current_set and not _last_set:
            if current_time - get_state("last_set_press_time") >= get_state("set_cooldown"):
                update_state("last_set_press_time", current_time)
                _last_action_time = current_time
                _play_picked_song()
        _last_set = current_set

        # ESC 暂缺的兜底：选歌屏 20s 无任何输入自动退回主界面
        # （esc 硬件补齐后这条路径基本不会走到；有输入立即清零重计）
        if current_up or current_down or current_set:
            _song_idle_start = 0.0
        elif _song_idle_start == 0.0:
            _song_idle_start = current_time
        elif current_time - _song_idle_start >= 20.0:
            _song_idle_start = 0.0
            _log.info("[songpick] 20s 无输入，自动退回主界面（ESC 未接硬件）")
            close_song_pick()
            return

        # ESC：轻点即退；按住 0.15s 也在按下沿退（两处都会复位计时，不会连退两次）
        if _pressed("esc"):
            if get_state("esc_press_start") == 0:
                update_state("esc_press_start", current_time)
            elif current_time - get_state("esc_press_start") >= 0.15:
                update_state("esc_press_start", 0)
                close_song_pick()
        else:
            if get_state("esc_press_start") > 0:
                update_state("esc_press_start", 0)
                close_song_pick()
        return

    # ---- SET 按键：push-to-talk（仅在主界面） ----
    if cs == STATE_MAIN:
        if _pressed("set"):
            if _set_press_start == 0:
                _set_press_start = current_time
                if ai_ctx and (
                    ai_ctx.get_config("wake_method") == "button"
                    and ai_ctx.get_status("state") == "IDLE"
                    and not ai_ctx.get_control("ptt_recording", False)
                ):
                    ai_ctx.set_control("ptt_recording", True)
        else:
            if _set_press_start > 0:
                if ai_ctx and ai_ctx.get_control("ptt_recording", False):
                    ai_ctx.set_control("ptt_recording", False)
                _set_press_start = 0

        # ---- SET 双击：全自动弹唱 / 人机配合 一键切（主界面）----
        #   ★ 让位规则：唤醒方式=按键 时 SET 是 push-to-talk，双击识别整个关闭，
        #     保证"按住说话"的行为一个字节都不变（见 _is_ptt_mode）。
        _ptt_takes_set = _is_ptt_mode(ai_ctx)
        if _ptt_takes_set:
            _set_double.reset()
            if not _ptt_takes_set_logged:
                _ptt_takes_set_logged = True
                _log.info("[mode] 唤醒方式=按键：SET 归语音，双击切换让位")
        elif _set_double.feed(_pressed("set"), current_time):
            _do_mode_toggle()

    # ---- ESC 按键：仅 MAIN↔MENU 用 2s 长按，其他全部短按 ----
    if _pressed("esc"):
        if get_state("esc_press_start") == 0:
            update_state("esc_press_start", current_time)
        else:
            dur = current_time - get_state("esc_press_start")
            # 仅 MAIN↔MENU 之间需要长按 2s
            if cs in (STATE_MAIN, STATE_MENU):
                if dur >= 2.0 and not get_state("esc_long_press"):
                    update_state("esc_long_press", True)
                    if cs == STATE_MAIN:
                        update_state("current_state", STATE_MENU)
                        update_state("current_menu", "root")
                        update_state("selected_index", 0)
                        update_state("menu_stack", [])
                    elif cs == STATE_MENU:
                        update_state("current_state", STATE_MAIN)
                        update_state("menu_stack", [])
                    update_state("display_dirty", True)
            # 其他所有场景短按即处理
            else:
                if dur >= 0.15 and not get_state("esc_long_press"):
                    update_state("esc_long_press", True)
                    if cs == STATE_CALIBRATION:
                        # ① 发 STOP 给 ESP32（语音演奏/校准时都需要）
                        try:
                            import esp32_player
                            esp32_player.esp32_stop_song()
                        except Exception:
                            pass
                        # ② 设置停止事件（校准线程用）
                        stop_event = get_state("calibration_stop_event")
                        if stop_event:
                            stop_event.set()
                        # ③ 等待线程结束
                        thread = get_state("calibration_thread")
                        if thread and thread.is_alive():
                            thread.join(timeout=5)
                        update_state("running_function", False)
                        update_state("current_state", STATE_MENU)
                        update_state("calibration_thread", None)
                    elif cs == STATE_TUNER:
                        stop_event = get_state("tuner_stop_event")
                        if stop_event:
                            stop_event.set()
                        thread = get_state("tuner_thread")
                        if thread and thread.is_alive():
                            thread.join(timeout=5)
                        update_state("running_function", False)
                        update_state("current_state", STATE_MENU)
                        update_state("tuner_thread", None)
                    elif cs == STATE_VOICE_DOWNLOAD_PROGRESS:
                        # ★ 自动播放中：先停止 ESP32 演奏（纯下载阶段调用也无害），
                        # 再取消界面，避免"界面已取消但吉他继续弹"
                        try:
                            import esp32_player
                            esp32_player.esp32_stop_song()
                        except Exception:
                            pass
                        _cancel_voice_download()
                    update_state("display_dirty", True)
    else:
        dur = 0
        if get_state("esc_press_start") > 0:
            dur = current_time - get_state("esc_press_start")
            update_state("esc_press_start", 0)
        # 短按处理（释放时 dur < 2.0 且非 MAIN/MENU 状态下由上面的 else 分支处理）
        if dur > 0 and dur < 2.0 and not get_state("esc_long_press"):
            if cs == STATE_MAIN:
                if ai_ctx and ai_ctx.get_status("state") == "SPEAKING":
                    ai_ctx.set_control("interrupt_playing", True)
            elif cs == STATE_MENU:
                handle_esc_short()
            elif cs in (STATE_CALIBRATION, STATE_TUNER, STATE_VOICE_DOWNLOAD_PROGRESS):
                pass  # 长按已处理
            else:
                # STATE_VOICE_DOWNLOAD_CONFIRM 由上方专用处理块接管，不会到达此处
                handle_esc_short()
        update_state("esc_long_press", False)
        update_state("display_dirty", True)

    # ---- 上/下/SET 按键（带去抖） ----
    if current_time - _last_action_time >= debounce_time:
        current_up = _pressed("up")
        if current_up and not _last_up:
            handle_up()
            _last_action_time = current_time
        _last_up = current_up

        current_down = _pressed("down")
        if current_down and not _last_down:
            handle_down()
            _last_action_time = current_time
        _last_down = current_down

        current_set = _pressed("set")
        if current_set and not _last_set:
            if current_time - get_state("last_set_press_time") >= get_state("set_cooldown"):
                handle_set()
                update_state("last_set_press_time", current_time)
                _last_action_time = current_time
        _last_set = current_set


#: 停机信号：`main.cleanup_on_exit` 在 `btn.deinit()` **之前**置位。
#: 不置位的话，检测线程会在 GPIO 已释放后继续读引脚 —— 实测退出时会打印
#: `AttributeError: 'NoneType' object has no attribute 'value'`（`check_buttons`
#: 里的 `set_button.value`），既是噪声也可能掩盖真正的退出异常。
_buttons_stopping = threading.Event()


def request_button_scan_stop():
    """请求按键检测线程退出（幂等）。**必须在 deinit 按键之前调用。**"""
    _buttons_stopping.set()


def button_detection_thread(ai_ctx=None):
    """后台按键检测线程。

    按钮未初始化 → 直接返回；收到停机信号 → 退出循环（不再触碰引脚）。
    """
    _log.info("[btn.diag] 按键检测线程启动 (up=%s down=%s set=%s esc=%s)",
              up_button is not None, down_button is not None,
              set_button is not None, esc_button is not None)
    if up_button is None:
        # ★ 四键全无反应时必查：这里原本静默 return，日志什么都没有
        _log.error("[btn.diag] 按键未初始化(GPIO被占用或初始化失败)，按键线程不工作！")
        return
    while not _buttons_stopping.is_set():
        try:
            check_buttons(ai_ctx=ai_ctx)
        except Exception:
            # 双保险：入口守卫已经在 check_buttons 里，这里只兜"守卫之前就已进栈"
            # 的极端时序；收到停机信号即退出，不再继续循环。
            if _buttons_stopping.is_set():
                break
            # ★ 不能静默吞：按键线程炸了主界面毫无反馈且旧版无任何日志可查
            #   （2026-10-11 排查"短按上/下无反应"时发现这里是黑洞）。
            _log.error("[buttons] 按键检测异常", exc_info=True)
        time.sleep(0.02)