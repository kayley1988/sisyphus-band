#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
泰山派 OLED 吉他自动演奏系统 — 主入口 (v4.3 模块化重构版)

启动流程:
  1. 硬件初始化 (hardware.py)          — GPIO / I2C / OLED / 按键
  2. AI 语义助手加载 (AI_voice_assistant.py) — 唤醒词 / ASR / TTS
  3. 屏幕功能依赖注入 (screen_functions.py)  — 所有外部依赖注入
  4. 开机启动序列 (Boot Screen)            — 进度动画 + 欢迎语音
  5. 按键检测线程 + 主渲染循环              — 每帧处理状态机

交互方式 (三种方式状态互相同步):
  - 🖥️ OLED + 按键: 四级菜单导航
  - 🌐 Web 管理界面: Flask，端口 5000
  - 🎤 语音助手: 唤醒词「小爱同学」→ 语音控制

演奏流程:
  选歌 → ESP32 加载 CSV → 发送舵机配置 → 硬件定时器驱动自动演奏
"""

import os
import time
import threading
import atexit
import subprocess
import sys
from logger import get_logger

_log = get_logger("main")

# ──────────── 模块导入 ────────────
from state import (
    get_state, get_all_state, update_state,
    STATE_MAIN, STATE_MENU, STATE_WIFI_SCAN, STATE_WIFI_SCANING,
    STATE_WIFI_CONN, STATE_CALIBRATION, STATE_TUNER, STATE_BOOT,
    STATE_VOICE_DOWNLOAD_CONFIRM, STATE_VOICE_DOWNLOAD_PROGRESS,
    STATE_SONG_PICK,
)
from hardware import i2c, oled, fonts, up_button, down_button, set_button, esc_button, USE_LCD
from oled_renderer import OLEDRenderer
from menu_system import MENU_ITEMS
from button_handler import check_buttons, button_detection_thread
from screens import (
    read_sensors_safe, get_wifi_display_info, wait_for_esc,
    init_screens,
)

# ──────────── 外部依赖 ────────────
from wifi_manager import WiFiManager
from power_monitor import PowerMonitor

# AI 语音助手（可选）
AI_AVAILABLE = False
VOSK_AVAILABLE = False
WAKE_WORDS = ["小爱同学"]
DEFAULT_WAKE_WORD = "小爱同学"
ai_ctx = None

try:
    from ai import AIContext, load_wake_words, VOSK_AVAILABLE as _VOSK
    AI_AVAILABLE = True
    VOSK_AVAILABLE = _VOSK
    WAKE_WORDS = load_wake_words()
except ImportError as e:
    _log.warning("AI 模块导入失败: %s，AI 功能不可用", e)

if AI_AVAILABLE:
    ai_ctx = AIContext()

# 语音演奏模块
try:
    from voice_player import VoicePlayer, play_prompt
    VOICE_PLAYER_AVAILABLE = True
except ImportError:
    _log.warning("voice_player 模块导入失败，语音演奏功能不可用")
    VoicePlayer = None
    play_prompt = lambda x: False
    VOICE_PLAYER_AVAILABLE = False


# ──────────── 全局退出清理 ────────────
def cleanup_on_exit():
    global ai_ctx
    # ① 停止 AI 语音引擎
    if ai_ctx:
        try:
            ai_ctx.control["stop_flag"] = True
        except Exception:
            pass
    # ② 归位舵机并失能（同步执行，退出时不依赖后台线程）
    try:
        import esp32_player
        from servo_controller import ServoController
        from frame_codec import make_servo_batch, make_servo_ctrl
        config_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..", "config", "servo_config.csv")
        sc = ServoController(config_file)
        chord_90 = [90] * 12
        strum_point_a = [int(sc._strum_angles[i][0]) for i in range(6)]
        sc._update_chord_channels(chord_90)
        sc._reset_strum_to_idle(safe_mode=False)
        time.sleep(0.35)
        sc._send_servo_ctrl(0x02)  # DISABLE
    except Exception:
        pass
    # ③ 释放 GPIO 和 OLED
    #   ★ 先让按键检测线程停下：它在 20ms 循环里读 btn.value，若先 deinit，
    #     线程会撞上 `AttributeError: 'NoneType' object has no attribute 'value'`
    #     （实测退出时必现）。顺序反了就是竞态。
    try:
        from button_handler import request_button_scan_stop
        request_button_scan_stop()
        # 短暂让出时间片，让已在 check_buttons 栈内的那一次迭代收尾；
        # 真正的安全由 check_buttons 入口守卫保证（不是靠等够时间）。
        time.sleep(0.05)
    except Exception:
        pass
    for btn in [up_button, down_button, set_button, esc_button]:
        try:
            btn.deinit()
        except Exception:
            pass
    try:
        oled.fill(0)
        oled.show()
    except Exception:
        pass

atexit.register(cleanup_on_exit)

# ──────────── 服务初始化 ────────────
wifi_manager = WiFiManager()
if USE_LCD:
    from lcd_renderer import LCDRenderer
    renderer = LCDRenderer(fonts)
    _log.info("渲染器: LCDRenderer（1.54″ ST7789 彩屏）")
else:
    renderer = OLEDRenderer(oled, fonts)
power_monitor = PowerMonitor()

# 语音演奏控制器
voice_player = None
if VOICE_PLAYER_AVAILABLE and ai_ctx:
    voice_player = VoicePlayer(ai_ctx=ai_ctx, power_monitor=power_monitor)

# 演奏线程信号量（同时最多 1 个演奏线程，防止误唤醒导致线程爆炸）
_play_thread_semaphore = threading.Semaphore(1)

# 她的演奏模式实例（无障碍改造核心层 · 见 trigger_integration.py）
# 主舞台默认 touch 模式；timer 模式仅供调试/彩蛋。由下方引导钩子初始化。
her_performance = None

# ──────────── 依赖注入到 screen_functions ────────────
init_screens(
    renderer=renderer,
    wifi_manager=wifi_manager,
    power_monitor=power_monitor,
    i2c=i2c,
    fonts=fonts,
    ai_ctx=ai_ctx,
    strumming_module=None,
    ai_available=AI_AVAILABLE,
    vosk_available=VOSK_AVAILABLE,
    wake_words=WAKE_WORDS,
    voice_player=voice_player,
)

# ──────────── 主屏幕信息收集 ────────────
marquee_offset = 0
last_main_screen_update = 0


def collect_main_screen_info():
    vbat, temp = read_sensors_safe()
    wifi_ssid, wifi_ip, wifi_signal = get_wifi_display_info()

    # 获取设备 IP（优先 WiFi IP，否则 AP 热点 IP）
    device_ip = wifi_ip if wifi_ip else ""
    ap_mode = False
    hotspot_ssid = ""
    if not device_ip:
        # 检查是否在 AP 热点模式
        if wifi_manager and wifi_manager.is_hotspot_active():
            device_ip = "10.42.0.1"
            ap_mode = True
            hotspot_ssid = wifi_manager._hotspot_ssid or "Guitar-Config"
        elif wifi_manager:
            info = wifi_manager.get_connected_wifi_info()
            if info and info.get("ip_address"):
                device_ip = info["ip_address"]
    if ai_ctx:
        # 使用原子快照替代逐字段读取，保证状态一致性
        status_snap = ai_ctx.get_status_snapshot()
        control_snap = ai_ctx.get_control_snapshot()
        config_snap = ai_ctx.get_config_snapshot()
        state_raw = status_snap.get("state", "IDLE")
        ai_active = control_snap.get("active", False)
        ai_available = status_snap.get("available", False)
        ai_error = status_snap.get("error", "")
        user_text = status_snap.get("user_text", "")
        reply_text = status_snap.get("reply_text", "")
        wake_word = config_snap.get("wake_word", "")
        wake_method = config_snap.get("wake_method", "voice")
        state_changed_at = status_snap.get("state_changed_at", 0.0)
        loading_detail = status_snap.get("loading_detail", "")
    else:
        state_raw = "IDLE"
        ai_active = False
        ai_available = False
        ai_error = ""
        user_text = ""
        reply_text = ""
        wake_word = ""
        wake_method = "voice"
        state_changed_at = 0.0
        loading_detail = ""
    return {
        "battery_v": vbat,
        "temperature": temp,
        "wifi_signal": wifi_signal,
        "device_ip": device_ip,
        "ap_mode": ap_mode,
        "hotspot_ssid": hotspot_ssid,
        "ai_state_raw": state_raw,
        "ai_active": ai_active,
        "ai_available": ai_available,
        "ai_error": ai_error,
        "ai_user_text": user_text,
        "ai_reply_text": reply_text,
        "ai_wake_word": wake_word,
        "wake_method": wake_method,
        "state_changed_at": state_changed_at,
        "loading_detail": loading_detail,
        "her_chord": _her_chord_text(),
        "her_hint": _her_hint_text(),
    }


def _her_chord_text() -> str:
    """her 模式**当前按着**的和弦名（供主界面中间大字）。

    无保持 / 未装配 → 空串。全程 try/except，提示层异常不得影响主界面渲染。

    显示规则（Z 要求，2026-10-07）：单字母和弦（C / G / D / F / E / A）显示
    "X 和弦"；双字母和弦（Am / Dm / Em / Bm / F#m 等）保持原名，避免误读
    （"Am和弦"不成立）。判据：仅当 chord 为 1 个大写字母时加"和弦"。
    """
    try:
        if her_performance is not None:
            chord = (her_performance.guard_status() or {}).get("chord") or ""
            if len(chord) == 1 and chord.isupper():
                return f"{chord} 和弦"
            return chord
    except Exception:
        pass
    return ""


def _her_hint_text() -> str:
    """her 模式的就位提示文本（供主界面底部横幅）。

    her_mode_guard 维护：压弦已就位 → 「C 已就位 请拨弦 4」；无保持 → 空串。
    和弦名已由**中间大字**单独呈现，此处剥掉重复前缀，底部只留节奏提示。
    取用全程 try/except —— 提示层任何异常都不得影响主界面渲染。
    """
    try:
        if her_performance is not None:
            txt = her_performance.hint_text() or ""
            chord = _her_chord_text()
            if chord and txt.startswith(chord):
                txt = txt[len(chord):].strip()
            return txt
    except Exception:
        pass
    return ""


# ──────────── 开机启动序列 ────────────
def _selftest_welcome_loopback():
    """开机自检 P4：**仅当** GUITAR_VOICE_LOOPBACK_SELFTEST 打开时，在欢迎语播放窗口
    录一段原始 PCM（不做 ASR、不写盘），等语音引擎就绪后再本地转写比对，
    结果只留分档，不留音频/正文。

    默认关闭 ⇒ 直接返回，客户可听的欢迎语路径完全不受影响；
    任何异常都被吞掉，绝不因为诊断而影响启动。
    """
    try:
        import boot_selftest as _st
        _st.capture_welcome_window(6.0)
    except Exception:
        pass


def run_boot_sequence():
    """开机启动画面 + 欢迎语音"""
    _log.info("自动吉他演奏系统 v4.3 启动中...")
    boot_steps = [
        ("初始化硬件...", 0.6),
        ("加载AI模型...", 1.2),
        ("启动语音引擎...", 0.0),  # 真实等待 AI 就绪，不使用虚假动画
        ("检查舵机状态...", 0.5),
    ]
    # ★ 2026-10-11：删掉末尾的「连接网络...」与「系统就绪」两步后，
    #   总时长改为对**全部**步骤求和（原先是 [:-1]，依赖最后一项时长为 0）。
    #   这样进度条在最后一步「检查舵机状态...」结束时正好到 100%。
    total_duration = sum(d[1] for d in boot_steps)
    boot_frame = 0
    cumulated = 0.0
    _log.info("启动序列: %d 步, 预计 %.1f 秒", len(boot_steps), total_duration)

    # 后台播放欢迎语音（等待 AI 就绪后才播放）
    def _speak_welcome():
        try:
            # 等待 AI 就绪，最多等 60 秒
            if ai_ctx:
                _wait_start = time.time()
                while not ai_ctx.get_control("active", False):
                    if time.time() - _wait_start > 60:
                        break
                    time.sleep(0.5)
            text = "欢迎使用自动吉他演奏系统，请说小爱同学唤醒我"

            # ★ 关键时序（2026-09-27 修）：先把 TTS **音频备好**，再"同时"起录音与播放。
            #
            #   旧实现的 bug：`_selftest_welcome_loopback()` 是**阻塞**的
            #   （`capture_welcome_window(6.0)` 自己录满 6 秒），它把 TTS 生成与播放
            #   一起挡在后面 —— 实测时间线：欢迎语 08:09:50 播完，录音窗口 08:10:03
            #   才开始，录到**整段静音**，回环永远 `no_speech`。
            #   现在：生成音频（不发声）→ 同时起录 + 起播 ⇒ 录音窗口必定覆盖欢迎语。
            audio = None
            try:
                mp3_path = "/tmp/welcome.mp3"
                subprocess.run(
                    [sys.executable, "-m", "edge_tts",
                     "--voice", "zh-CN-XiaoxiaoNeural",
                     "--text", text, "--write-media", mp3_path],
                    timeout=15, capture_output=True,
                )
                if os.path.exists(mp3_path) and os.path.getsize(mp3_path) > 0:
                    audio = ("edge_tts", mp3_path)
            except Exception:
                pass

            # 起录音（独立线程，不阻塞下面起播）
            capture_started = False
            try:
                threading.Thread(target=_selftest_welcome_loopback,
                                 daemon=True).start()
                capture_started = True
            except Exception:
                pass

            # 起播（同样放线程：播放会阻塞到音频结束）
            play_done = threading.Event()

            def _play():
                try:
                    if audio is not None:
                        _, path = audio
                        for player in ("mpg123", "ffplay"):
                            if subprocess.run(["which", player],
                                              capture_output=True).returncode == 0:
                                if player == "mpg123":
                                    subprocess.run(["mpg123", "-q", path],
                                                   timeout=30, capture_output=True)
                                else:
                                    subprocess.run(
                                        ["ffplay", "-nodisp", "-autoexit",
                                         "-loglevel", "quiet", path],
                                        timeout=30, capture_output=True)
                                return
                        if subprocess.run(["which", "ffmpeg"],
                                          capture_output=True).returncode == 0:
                            wav_path = "/tmp/welcome.wav"
                            subprocess.run(["ffmpeg", "-y", "-i", path, wav_path],
                                           timeout=15, capture_output=True)
                            subprocess.run(["aplay", wav_path],
                                           timeout=30, capture_output=True)
                            return
                        _log.info("欢迎语音: 未找到音频播放器，跳过")
                        return
                    # 无 edge-tts 音频：退回本机 TTS
                    if subprocess.run(["which", "espeak-ng"],
                                      capture_output=True).returncode == 0:
                        subprocess.run(["espeak-ng", "-v", "zh", text],
                                       timeout=10, capture_output=True)
                        return
                    subprocess.run(["espeak", "-v", "zh", text],
                                   timeout=10, capture_output=True)
                except Exception as exc:                       # noqa: BLE001
                    _log.warning("欢迎语音播放失败: %s", exc)
                finally:
                    play_done.set()

            threading.Thread(target=_play, daemon=True).start()
            # 等放完（有界），再请求回环转写：此时录音窗口也已结束、PCM 就绪
            play_done.wait(40.0)
            if capture_started:
                try:
                    import boot_selftest as _st
                    # 转写不需要麦克风（用窗口缓冲的 PCM），超时给足 ASR 时间
                    _st.request_voice_loopback(reason="welcome_window", timeout_s=25.0)
                except Exception:
                    pass
            return
        except Exception as e:
            _log.warning("欢迎语音播放失败: %s", e)

    welcome_thread = threading.Thread(target=_speak_welcome, daemon=True)
    welcome_thread.start()

    for i, (step_name, step_duration) in enumerate(boot_steps):
        _log.info("[%d/%d] %s", i+1, len(boot_steps), step_name)

        # 第 3 步"启动语音引擎"真实等待 AI 就绪
        if step_name == "启动语音引擎..." and ai_ctx:
            loading_detail = ""
            last_detail = ""
            _ai_wait_start = time.time()
            while True:
                # 获取 AI 加载子阶段（供 OLED 显示）
                loading_detail = ai_ctx.get_status("loading_detail", "")
                if loading_detail and loading_detail != last_detail:
                    last_detail = loading_detail
                    renderer.show_boot_screen(
                        progress=min(1.0, cumulated / max(total_duration, 0.01)),
                        status_text=step_name,
                        boot_frame=boot_frame,
                        loading_detail=loading_detail,
                        show_ready=False,
                    )
                    boot_frame += 1
                else:
                    renderer.show_boot_screen(
                        progress=min(1.0, cumulated / max(total_duration, 0.01)),
                        status_text=step_name,
                        boot_frame=boot_frame,
                        loading_detail=loading_detail,
                        show_ready=False,
                    )
                    boot_frame += 1

                # 检查 AI 是否就绪
                if ai_ctx.get_control("active", False):
                    cumulated += sum(d[1] for d in boot_steps[:3])  # 前 3 步累积
                    break
                # 检查 AI 是否不可用
                if ai_ctx.get_status("state") == "UNAVAILABLE":
                    error_msg = ai_ctx.get_status("error", "未知错误")
                    _log.warning("AI 不可用: %s", error_msg)
                    renderer.show_boot_screen(
                        progress=min(1.0, cumulated / max(total_duration, 0.01)),
                        status_text="AI 不可用",
                        boot_frame=boot_frame,
                        loading_detail=error_msg[:30],
                        show_ready=False,
                    )
                    boot_frame += 1
                    time.sleep(2.0)
                    cumulated += sum(d[1] for d in boot_steps[:3])
                    break
                # 超时保护（60 秒）
                if time.time() - _ai_wait_start > 60:
                    _log.warning("AI 启动超时(60s)，继续启动")
                    cumulated += sum(d[1] for d in boot_steps[:3])
                    break
                time.sleep(0.3)
        elif step_duration > 0:
            chunk = 0.15
            waited = 0.0
            while waited < step_duration:
                time.sleep(chunk)
                waited += chunk
                cumulated += chunk
                renderer.show_boot_screen(
                    progress=min(1.0, cumulated / max(total_duration, 0.01)),
                    status_text=step_name,
                    boot_frame=boot_frame,
                    show_ready=False,
                )
                boot_frame += 1
        else:
            cumulated += step_duration

    # AI 语音栈未装配时**不报唤醒词** —— 报了也没用，只会让人以为"喊了就该有反应"。
    wake_word = ai_ctx.get_config("wake_word", DEFAULT_WAKE_WORD) if ai_ctx else ""
    # ★ 2026-10-11（用户要求）：精简开机动画。
    #   ① 去掉末尾的「系统就绪」停留页 —— 进度条跑完后直接进主界面，不再多停 1.5s。
    #   ② 去掉「连接网络...」这一步 —— 它只是动画文案、不执行任何联网，
    #      但最容易让人误以为系统在等网络。至此最后一步为「检查舵机状态...」。
    #   唤醒词仍照常记录（见下方 _log.info），只是不再单独占一屏。
    welcome_thread.join(timeout=5)

    if wake_word:
        _log.info("系统就绪! 唤醒词: %s", wake_word)
    else:
        _log.info("系统就绪!（语音助手未装配，无唤醒词）")
    if ai_ctx:
        _log.info("AI 引擎: %s", "已启动" if ai_ctx.get_control('active', False) else "启动中...")
    _log.info("OLED 主界面已显示")

    update_state("current_state", STATE_MAIN)
    update_state("display_dirty", True)

    # 通知 AI 线程开机启动已完成，可以开始监听唤醒词
    if ai_ctx:
        ai_ctx.set_control("boot_complete", True)
        _log.info("开机完成，AI 可开始监听唤醒词")

    # ── 开机末尾「启动中」等待动画（覆盖 动画结束 → 主循环 之间的空档）──
    _start_boot_wait()


# ──────────── 开机「启动中」等待屏 ────────────
# 背景：run_boot_sequence() 返回后，主线程还要跑一段**真实初始化**
#   （配置校验 / 她的演奏模式 / 系统监控 / 热点监控 / 首次引导 / Web 服务 / 舵机同步），
#   实测数十秒 ~ 2 分钟不等。这段期间主线程在忙、不渲染，屏幕会**冻在动画最后一帧**
#   （删掉「连接网络」后就固定显示为「检查舵机状态...」，用户以为卡死）。
# 解法：用一个后台 daemon 线程持续刷 spinner，让屏幕保持“活着”；
#   主循环开始渲染前调用 _stop_boot_wait() 停掉它，避免两个线程抢同一块 SPI。
_boot_wait_stop = threading.Event()
_boot_wait_thread = None


def _start_boot_wait():
    """启动「启动中，请稍候...」等待屏（幂等：重复调用只保留一个线程）。"""
    global _boot_wait_thread
    if _boot_wait_thread is not None and _boot_wait_thread.is_alive():
        return
    _boot_wait_stop.clear()

    def _loop():
        frame = 0
        while not _boot_wait_stop.is_set():
            try:
                renderer.show_boot_screen(
                    progress=1.0,
                    status_text="启动中，请稍候...",
                    boot_frame=frame,
                    show_ready=False,
                )
            except Exception:
                pass  # 渲染失败绝不能让启动卡住
            frame += 1
            # 用 Event.wait 代替 sleep：收到停止信号能立即退出
            _boot_wait_stop.wait(0.2)

    _boot_wait_thread = threading.Thread(
        target=_loop, name="boot-wait-spinner", daemon=True)
    _boot_wait_thread.start()


def _stop_boot_wait():
    """停掉等待屏并等它退出（主循环开始渲染前调用）。"""
    global _boot_wait_thread
    if _boot_wait_thread is None:
        return
    _boot_wait_stop.set()
    try:
        _boot_wait_thread.join(timeout=1.5)
    except Exception:
        pass
    _boot_wait_thread = None


# ──────────── 主循环 ────────────
def main():
    global marquee_offset, last_main_screen_update, ai_ctx

    # ── 初始化串口总线（所有模块共享）──
    from serial_bus import SerialBus
    SerialBus.instance().open("/dev/ttyS3", 921600)

    # ── 项目根目录（dirname=src/guitarist, 再上2级 → project_root）──
    _project_root = os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", ".."))

    # ── 初始化结构化日志系统 ──
    try:
        from logger import init_logger
        init_logger(log_dir=os.path.join(_project_root, "logs"), level="INFO")
    except Exception as e:
        _log.error("日志系统初始化失败: %s", e, exc_info=True)

    # ── 初始化全局错误收集器 ──
    try:
        from error_collector import init_error_collector
        error_collector = init_error_collector(max_errors=200, log_file=os.path.join(_project_root, "logs", "errors.jsonl"))
    except Exception as e:
        _log.error("错误收集器初始化失败: %s", e, exc_info=True)
        error_collector = None

    # ── 日志统一链路（ESP32→泰山派 上传 + 主机 JSONL + 双端对照下载）──
    #   ★ 纯新增、可一键回退（删掉本段即可）；失败只降级，不影响既有功能。
    #   ★ 只注册帧回调 + 起两个后台线程，不接管串口，也不发任何舵机/模式/播放帧。
    try:
        import log_runtime
        from serial_bus import SerialBus as _SerialBus
        log_runtime.install(log_dir=os.path.join(_project_root, "logs"),
                            project_root=_project_root,
                            bus=_SerialBus.instance())
        _log.info("日志统一链路已启用 (esp32 上传 + 主机 JSONL + /api/logs*)")
    except Exception as e:
        _log.warning("日志统一链路启用失败（不影响其它功能）: %s", e, exc_info=True)

    # ── 读取并校验版本信息 ──
    from state import get_app_version
    app_version = get_app_version()
    update_state("app_version", app_version)
    if "unknown" in app_version:
        _log.warning("版本信息不可用: %s", app_version)
    else:
        _log.info("应用版本: v%s", app_version)

    # 启动按键检测线程
    threading.Thread(target=button_detection_thread, args=(ai_ctx,), daemon=True).start()

    # 提前启动 AI 语音引擎（在开机动画之前），让启动画面能真实反映加载进度
    ai_ready = False
    if ai_ctx:
        _log.info("启动 AI 语音引擎（后台加载）...")
        ai_ctx.start()
    else:
        _log.warning("AI 不可用（未配置 API Key 或导入失败）")

    # 开机启动画面（现在可以真实等待 AI 就绪）
    run_boot_sequence()

    # ── 配置校验 ──
    try:
        import yaml as _yaml
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "config", "config.yaml",
        )
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = _yaml.safe_load(f)
            from config_validator import validate_config, print_validation_report
            cfg_errors, cfg_warnings = validate_config(cfg or {})
            print_validation_report(cfg_errors, cfg_warnings)
        else:
            _log.warning("config.yaml 不存在，跳过配置校验")
    except Exception as e:
        _log.warning("配置校验失败: %s", e)

    # ── 初始化「她的演奏模式」（无障碍改造核心层 · 见 trigger_integration.py）──
    # ★ 全程保护：任何失败都不影响原自动演奏/语音路径。
    #   硬件到货 + config.yaml 填真实 touch_pins 后自动生效；无硬件仅 warning。
    try:
        import yaml as _yaml2
        _cfg_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "config", "config.yaml",
        )
        _cfg = {}
        if os.path.exists(_cfg_path):
            with open(_cfg_path, "r", encoding="utf-8") as _f:
                _cfg = _yaml2.safe_load(_f) or {}
        global her_performance
        from trigger_integration import build_her_performance
        _chord_csv = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "data", "chord_definitions.csv",
        )
        her_performance = build_her_performance(_cfg, chord_csv=_chord_csv)
        her_performance.start()
        _log.info("她的演奏模式已就绪: mode=%s", her_performance.mode)
    except Exception as e:
        _log.warning("她的演奏模式初始化失败(不影响自动演奏): %s", e)

    # ── 初始化系统资源监控 ──
    system_monitor = None
    try:
        from system_monitor import SystemMonitor
        sm_config = {}
        try:
            if os.path.exists(config_path):
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg2 = _yaml.safe_load(f)
                sm_config = (cfg2 or {}).get("system_monitor", {})
        except Exception:
            pass
        system_monitor = SystemMonitor(sm_config)
        _log.info("系统资源监控已初始化")
    except Exception as e:
        _log.warning("系统资源监控初始化失败: %s", e)

    # ── 初始化热点自动监控 ──
    hotspot_monitor = None
    try:
        from hotspot_monitor import HotspotMonitor

        # 尝试从 config.yaml 读取热点配置
        hotspot_config = {
            "enabled": True,
            "ssid": "Guitar-Config",
            "password": "12345678",
            "check_interval": 10,
            "grace_period": 30,
        }
        try:
            import yaml
            config_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                "config", "config.yaml",
            )
            if os.path.exists(config_path):
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f)
                if cfg and "hotspot" in cfg:
                    hotspot_config.update(cfg["hotspot"])
        except Exception:
            pass  # 配置文件不存在或格式错误时使用默认值

        hotspot_monitor = HotspotMonitor(wifi_manager, hotspot_config)
        hotspot_monitor.start()
        _log.info("热点自动监控已启动")
    except ImportError as e:
        _log.warning("热点监控模块导入失败: %s", e)
    except Exception as e:
        _log.warning("热点监控启动失败: %s", e)

    # ── 注册 ESP32 过流推送回调 ──
    # 过流检测已迁移到 ESP32 端 (main.cpp loop()), ESP32 自主判断并失能舵机,
    # 通过 !OVERCURRENT 推送事件通知上位机。Python 端不再运行后台轮询线程。
    try:
        from serial_bus import SerialBus

        def _on_esp32_overcurrent(event: str):
            """ESP32 推送的过流事件处理 (在 SerialBus 读线程中同步调用, 必须快速返回)"""
            _log.error("ESP32 过流保护触发: %s", event)
            # 标记过流状态
            power_monitor.set_overcurrent_triggered(event)
            # 停止当前演奏
            try:
                import esp32_player as _esp
                _esp.esp32_stop_song()
            except Exception:
                pass
            # 上报错误
            if error_collector:
                try:
                    from error_codes import ErrorCode
                    error_collector.report(ErrorCode.SERVO_OVERCURRENT_CHORD, detail=event)
                except Exception:
                    pass

        SerialBus.instance().set_overcurrent_callback(_on_esp32_overcurrent)
        _log.info("ESP32 过流保护回调已注册 (ESP32 自主检测)")
    except Exception as e:
        _log.error("过流保护回调注册失败: %s", e)

    # ── 首次使用引导检测 ──
    onboarding_manager = None
    try:
        from onboarding import OnboardingManager
        onboarding_manager = OnboardingManager(wifi_manager, hotspot_monitor)
        if onboarding_manager.is_first_boot():
            info = onboarding_manager.start_onboarding()
            _log.info("首次使用引导已启动")
            update_state("onboarding_active", True)
            update_state("onboarding_ap_mode", info.get("ap_mode", True))
            if info.get("ap_mode", True):
                _log.info("引导 AP: %s, Web: http://%s:%s", info['ssid'], info['ip'], info['port'])
            else:
                _log.info("引导 WiFi: %s, IP: %s",
                          info.get('ssid', ''), info.get('ip', ''))
            update_state("onboarding_ssid", info.get("ssid", ""))
            update_state("onboarding_password", info.get("password", ""))
            update_state("onboarding_ip", info.get("ip", ""))
            update_state("onboarding_port", info.get("port", 5000))
        else:
            _log.info("非首次启动，跳过引导")
    except Exception as e:
        _log.warning("首次引导初始化失败: %s", e)

    # ── 初始化自启动管理器 ──
    autostart_manager = None
    try:
        from autostart_manager import AutostartManager
        autostart_manager = AutostartManager()
        _log.info("自启动管理器已初始化")
    except ImportError as e:
        _log.warning("自启动管理模块导入失败: %s", e)
    except Exception as e:
        _log.warning("自启动管理器初始化失败: %s", e)

    # 启动 Web 管理界面
    try:
        from web_ui import start_web_server
        start_web_server(
            servo_controller=None,
            wifi_manager=wifi_manager,
            power_monitor=power_monitor,
            ai_ctx=ai_ctx,
            strumming_module=None,
            hotspot_monitor=hotspot_monitor,
            autostart_manager=autostart_manager,
            error_collector=error_collector,
            system_monitor=system_monitor,
            onboarding_manager=onboarding_manager,
        )
    except Exception as e:
        _log.error("Web 管理界面启动失败: %s", e, exc_info=True)

    # 开机同步舵机校准参数到 ESP32
    try:
        update_state("running_function", True)
        import esp32_player
        shared_ser = power_monitor._ser if power_monitor else None
        esp32_player.esp32_send_servo_config(shared_ser=shared_ser)
    except Exception as e:
        _log.warning("开机舵机校准同步失败: %s", e)
    finally:
        update_state("running_function", False)

    last_main_screen_update = time.time()
    refresh_interval = 0.25
    last_status = None  # 跟踪 AI 状态变化，避免重复打印
    prev_state = None  # 跟踪上一帧的页面状态，用于检测页面切换

    state = get_all_state()  # 初始快照，避免循环首轮未定义

    # ── systemd Watchdog 心跳（WatchdogSec=60s，需每 ~30s 发一次）──
    _watchdog_sock = None
    _watchdog_path = os.environ.get("NOTIFY_SOCKET", "")
    if _watchdog_path:
        import socket as _socket
        try:
            _watchdog_sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_DGRAM)
            _watchdog_sock.connect(_watchdog_path)
            _watchdog_sock.send(b"READY=1\nWATCHDOG=1")
            _log.info("systemd Watchdog 已激活")
        except Exception:
            _watchdog_sock = None
    _last_watchdog_ping = time.time()

    # ── STATUS 遥测定时刷新 ──
    _last_status_refresh = 0.0

    # ── 停掉开机「启动中」等待屏：主循环马上开始渲染，避免两线程抢 SPI ──
    _stop_boot_wait()

    try:
        exit_reason = "normal"
        while True:
            # ── systemd Watchdog 心跳（每 30 秒发一次）──
            if _watchdog_sock and time.time() - _last_watchdog_ping > 30:
                try:
                    _watchdog_sock.send(b"WATCHDOG=1")
                    _last_watchdog_ping = time.time()
                except Exception:
                    _watchdog_sock = None

            # ── STATUS 遥测刷新 (每 1s, 演奏/校准期间跳过以避免串口争用) ──
            now = time.time()
            if now - _last_status_refresh > 1.0 and not power_monitor.is_paused:
                try:
                    power_monitor.refresh_status(timeout=1.0)
                except Exception:
                    pass
                _last_status_refresh = now

            # [已删除 is_playback_active() 分支 — ESP32 端自己管理播放进度]

            state = get_all_state()
            current_time = time.time()

            # ── 页面切换检测：同步 AI 的 page_active 标志 ──
            # 注：串口竞争已由 SerialBus 单例统一管理，不再需要手动暂停/恢复过流保护
            current_page = state["current_state"]
            if current_page != prev_state:
                if ai_ctx:
                    if current_page == STATE_MAIN:
                        ai_ctx.set_control("page_active", True)
                    elif prev_state == STATE_MAIN:
                        ai_ctx.set_control("page_active", False)
                        ai_ctx.set_control("interrupt_playing", True)

                prev_state = current_page

            # 仅在 AI 状态变化时打印一次（不再每 10 秒刷屏）
            if ai_ctx:
                ai_state = ai_ctx.get_status("state", "N/A")
                ai_active = ai_ctx.get_control("active", False)
                current_status = (ai_active, ai_state)
                if current_status != last_status:
                    _log.info("AI 状态变更: active=%s, state=%s", ai_active, ai_state)
                    if ai_active and not ai_ready:
                        ai_ready = True
                        _log.info("AI 语音引擎就绪!")
                    last_status = current_status
            else:
                if last_status != (False, "N/A"):
                    _log.info("AI 不可用，系统继续运行")
                    last_status = (False, "N/A")

            # 如果 running_function 且不是校准/调音状态，跳过渲染
            if state["running_function"] and state["current_state"] not in (STATE_CALIBRATION, STATE_TUNER):
                time.sleep(0.1)
                continue

            if state["current_state"] == STATE_BOOT:
                time.sleep(0.05)
                continue

            if state["current_state"] == STATE_MAIN:
                # ── 语音演奏请求处理 ──
                if ai_ctx and voice_player:
                    play_request = ai_ctx.get_control("voice_play_request")
                    if play_request:
                        ai_ctx.set_control("voice_play_request", None)
                        song_info = voice_player.search_song(play_request)
                        if song_info and song_info["playable"]:
                            # 命中 + 有 CSV → 直接演奏
                            update_state("calibration_title", f"演奏 {song_info['name']}")
                            update_state("calibration_progress", 0)
                            update_state("calibration_status", "准备演奏...")
                            update_state("current_state", STATE_CALIBRATION)
                            update_state("running_function", True)
                            update_state("display_dirty", True)

                            def _play_thread(si=song_info):
                                def _on_progress(p):
                                    update_state("calibration_progress", float(p))
                                    update_state("calibration_status", f"演奏中... {p}%")
                                try:
                                    voice_player.play_direct(si, progress_callback=_on_progress)
                                except Exception as e:
                                    _log.error("语音演奏异常: %s", e)
                                update_state("running_function", False)
                                # ★ 条件复位：ESC 已把界面切到菜单时不覆盖，避免把用户踢回主界面
                                if get_state("current_state") == STATE_CALIBRATION:
                                    update_state("current_state", STATE_MAIN)
                                update_state("display_dirty", True)

                            if _play_thread_semaphore.acquire(blocking=False):
                                def _play_thread_wrapper(si=song_info):
                                    try:
                                        _play_thread(si)
                                    finally:
                                        _play_thread_semaphore.release()
                                threading.Thread(target=_play_thread_wrapper, daemon=True).start()
                            else:
                                _log.warning("演奏线程已满，忽略新的演奏请求")
                        else:
                            # 未命中 / 命中但无 CSV → 下载确认流程
                            update_state("voice_download_song_name", play_request)
                            update_state("voice_download_countdown", 10.0)
                            update_state("voice_download_countdown_start", time.time())
                            update_state("current_state", STATE_VOICE_DOWNLOAD_CONFIRM)
                            update_state("display_dirty", True)
                            # 异步播放提示音
                            threading.Thread(
                                target=lambda: play_prompt("song_not_found"),
                                daemon=True,
                            ).start()

                    # ── 语音下载请求处理 ──
                    download_request = ai_ctx.get_control("voice_download_request")
                    if download_request:
                        ai_ctx.set_control("voice_download_request", None)
                        # 直接进入下载确认流程
                        update_state("voice_download_song_name", download_request)
                        update_state("voice_download_countdown", 10.0)
                        update_state("voice_download_countdown_start", time.time())
                        update_state("current_state", STATE_VOICE_DOWNLOAD_CONFIRM)
                        update_state("display_dirty", True)
                        threading.Thread(
                            target=lambda: play_prompt("song_not_found"),
                            daemon=True,
                        ).start()

                if ai_ctx:
                    ai_state = ai_ctx.get_status("state", "IDLE")
                    refresh_interval = 0.15 if ai_state in ("SPEAKING", "LISTENING") else 0.3
                else:
                    refresh_interval = 0.3

                if (
                    state["display_dirty"]
                    or state["force_redraw"]
                    or current_time - last_main_screen_update > refresh_interval
                ):
                    # ★ 双击 SET 切换模式后的临时提示（mode_toggle 写入 ui_toast）。
                    #   由**主循环**渲染，而不是按键线程直接刷屏——两者抢同一块屏必花。
                    _toast = get_state("ui_toast")
                    if _toast and current_time < _toast.get("until", 0):
                        renderer.show_message(_toast.get("lines", []), duration=0)
                    else:
                        if _toast:
                            update_state("ui_toast", None)
                        info = collect_main_screen_info()
                        if USE_LCD and her_performance is not None and hasattr(renderer, "show_chord_panel"):
                            # her 演奏（彩屏）：3×2 和弦面板（当前红 / 下一个绿）
                            cp = her_performance.chord_panel_state()
                            renderer.show_chord_panel(cp.get("active_index", -1),
                                                       cp.get("next_index", -1))
                        else:
                            renderer.show_main_screen(info, marquee_offset)
                    marquee_offset += 2
                    if marquee_offset > 5000:
                        marquee_offset = 0
                    last_main_screen_update = current_time
                    update_state("display_dirty", False)
                    update_state("force_redraw", False)
                    if ai_ctx:
                        ai_ctx.display_dirty = False

            elif state["current_state"] == STATE_CALIBRATION:
                title = state["calibration_title"]
                progress = state["calibration_progress"]
                status = state["calibration_status"]
                renderer.show_calibration_progress(title, progress, status)
                update_state("display_dirty", False)
                thread = state["calibration_thread"]
                if thread and not thread.is_alive():
                    update_state("running_function", False)
                    update_state("current_state", STATE_MENU)
                    update_state("display_dirty", True)
                    update_state("calibration_thread", None)
                    _log.info("校准/测试线程结束")

            elif state["current_state"] == STATE_TUNER:
                renderer.show_tuner(
                    state["tuner_display_text"],
                    state["tuner_target_string"],
                    state["tuner_cents"],
                    state["tuner_locked"],
                )
                update_state("display_dirty", False)
                thread = state["tuner_thread"]
                if thread and not thread.is_alive():
                    update_state("running_function", False)
                    update_state("current_state", STATE_MENU)
                    update_state("display_dirty", True)
                    update_state("tuner_thread", None)
                    _log.info("调音器线程结束")

            elif state["current_state"] == STATE_VOICE_DOWNLOAD_CONFIRM:
                song_name = get_state("voice_download_song_name")
                countdown_start = get_state("voice_download_countdown_start", time.time())
                elapsed = time.time() - countdown_start
                remaining = max(0, 10.0 - elapsed)
                update_state("voice_download_countdown", remaining)
                renderer.show_voice_download_confirm(song_name, remaining)
                update_state("display_dirty", False)
                # 倒计时到期（基于真实时间，不再依赖帧率估算）
                if remaining <= 0:
                    update_state("current_state", STATE_MAIN)
                    update_state("display_dirty", True)
                    threading.Thread(
                        target=lambda: play_prompt("download_cancelled"),
                        daemon=True,
                    ).start()

            elif state["current_state"] == STATE_VOICE_DOWNLOAD_PROGRESS:
                title = get_state("calibration_title")
                progress = get_state("calibration_progress")
                status = get_state("calibration_status")
                renderer.show_calibration_progress(title, progress, status)
                update_state("display_dirty", False)
                # 检查下载线程
                thread = get_state("voice_download_thread")
                if thread and not thread.is_alive():
                    result = get_state("voice_download_result")
                    update_state("voice_download_thread", None)
                    if result is True:
                        # 下载成功 → 自动演奏
                        update_state("voice_download_result", None)
                        play_prompt("download_done")
                        song_name = get_state("voice_download_song_name")
                        if voice_player:
                            song_info = voice_player.search_song(song_name)
                            if song_info and song_info["playable"]:
                                # ★ 与语音直接播放路径对齐：进入演奏状态并占用运行门闩，
                                # 防止演奏期间 Web/按键/语音再启动第二个播放
                                update_state("calibration_title", f"演奏 {song_name}")
                                update_state("calibration_progress", 0)
                                update_state("calibration_status", "准备演奏...")
                                update_state("current_state", STATE_CALIBRATION)
                                update_state("running_function", True)
                                update_state("display_dirty", True)

                                def _auto_play(si=song_info):
                                    def _on_progress(p):
                                        update_state("calibration_progress", float(p))
                                        update_state("calibration_status", f"演奏中... {p}%")
                                    try:
                                        voice_player.play_direct(si, progress_callback=_on_progress)
                                    except Exception as e:
                                        _log.error("自动演奏异常: %s", e)
                                    update_state("running_function", False)
                                    # ★ 条件复位：ESC 已切走界面时（MENU）不覆盖
                                    if get_state("current_state") == STATE_CALIBRATION:
                                        update_state("current_state", STATE_MAIN)
                                    update_state("display_dirty", True)
                                threading.Thread(target=_auto_play, daemon=True).start()
                            else:
                                update_state("current_state", STATE_MAIN)
                                update_state("display_dirty", True)
                        else:
                            update_state("current_state", STATE_MAIN)
                            update_state("display_dirty", True)
                    elif isinstance(result, str):
                        # 下载失败 → 播报原因
                        error_msg = result
                        update_state("voice_download_result", None)
                        update_state("current_state", STATE_MAIN)
                        update_state("display_dirty", True)
                        # 选择对应的提示音
                        if "找不到对应" in error_msg:
                            prompt_id = "no_image_source"
                        elif "网络" in error_msg:
                            prompt_id = "network_error"
                        else:
                            prompt_id = "download_failed"
                        threading.Thread(
                            target=lambda pid=prompt_id: play_prompt(pid),
                            daemon=True,
                        ).start()

            else:
                if state["display_dirty"] or state["force_redraw"]:
                    if state["current_state"] == STATE_MENU:
                        menu = MENU_ITEMS.get(state["current_menu"])
                        items = menu["items"] if menu else []
                        renderer.show_menu(
                            items, state["selected_index"],
                            title=menu.get("title") if menu else None,
                        )
                    elif state["current_state"] == STATE_SONG_PICK:
                        # 选歌屏：主界面短按 上/下 呼出（彩屏大字版 / OLED 走兜底版式）
                        renderer.show_song_list(
                            state["song_list"], state["song_index"])
                    elif state["current_state"] == STATE_WIFI_SCANING:
                        renderer.show_scanning(state["scan_animation_frame"])
                    elif state["current_state"] == STATE_WIFI_SCAN:
                        renderer.show_wifi_scan(
                            wifi_manager.networks, state["wifi_scan_selected"],
                        )
                    elif state["current_state"] == STATE_WIFI_CONN:
                        renderer.show_message(["正在连接WiFi..."])
                    update_state("display_dirty", False)
                    update_state("force_redraw", False)

            # ── 显示自愈（Z 2026-10-11「白屏根治」）──
            #   彩屏每隔几秒**无复位、无闪烁**地补发一次关键显示状态
            #   （INVON 颜色反转 / 正常显示模式 / Display ON）。面板被干扰
            #   复位后颜色会整体翻转成"白底"，补发即自动回正，肉眼无感。
            #   OLED 渲染器没有这个方法 → getattr 兜底，静默跳过。
            _display_selfcheck = getattr(renderer, "display_selfcheck", None)
            if _display_selfcheck is not None:
                try:
                    _display_selfcheck()
                except Exception:
                    pass

            time.sleep(0.03)

    except KeyboardInterrupt:
        exit_reason = "keyboard_interrupt"
        _log.info("收到 Ctrl-C，开始退出")
    except Exception as e:
        exit_reason = "main_loop_exception"
        _log.error("主循环异常: %s", e, exc_info=True)
    finally:
        # ★ B13：停机必须留痕。以前这里什么都不写，导致"这次运行是怎么结束的"
        #   事后完全查不到（SIGINT 那次 96 秒不退出，日志里一条记录都没有）。
        #   session_end 必须在 log_runtime.shutdown() **之前**发，否则 writer 已经
        #   被 request_stop，submit 会直接返回 False。
        # ★ B14：ai_ctx.stop() 只是置停止标志，但 AI 线程的收尾可能很慢；这里给它
        #   一个上限并计时，超时就记进 session_end 的 details，不再让退出无限期挂着。
        _shutdown_marks = []
        _t0 = time.monotonic()

        def _mark(name):
            now = time.monotonic()
            _shutdown_marks.append({"phase": name,
                                    "ms": round((now - _t0) * 1000.0, 3)})

        if ai_ctx:
            try:
                ai_ctx.stop()
            except Exception as exc:
                _log.warning("ai_ctx.stop() 异常: %s", exc)
        _mark("ai_stop")
        _submit_session_end(exit_reason, _shutdown_marks)
        # 日志链路收尾：停线程、flush、关文件（失败不影响退出）
        try:
            import log_runtime
            log_runtime.shutdown()
        except Exception:
            pass


def _submit_session_end(exit_reason, marks=None):
    """写一条 ``session_end``：这次运行**为什么、什么时候**结束的（B13）。

    走关键保留槽，避免洪峰时被普通事件挤掉。失败绝不影响退出。
    """
    try:
        import log_runtime
        writer = log_runtime.get_writer()
    except Exception:
        writer = None
    if writer is None or not hasattr(writer, "submit"):
        return False
    details = {"exit_reason": exit_reason, "shutdown_ms": marks or []}
    try:
        st = writer.status()
        if isinstance(st, dict):
            details["durable_host_seq"] = st.get("durable_host_seq")
            details["queue_depth"] = st.get("queue_depth")
            details["integrity_complete"] = st.get("integrity_complete")
    except Exception:
        pass
    try:
        return bool(writer.submit({
            "source": "tspi_app", "component": "guitar.main",
            "event_type": "session_end", "phase": "shutdown",
            "severity": "INFO" if exit_reason == "normal" else "WARN",
            "message": "应用退出: %s" % exit_reason,
            "details": details, "flags": {},
        }, critical=True))
    except Exception:
        return False


if __name__ == "__main__":
    main()