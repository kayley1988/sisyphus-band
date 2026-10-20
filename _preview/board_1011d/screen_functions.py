#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
功能屏幕模块 — 所有功能级屏幕实现 (WiFi / 播放 / 校准 / 调音器 / AI设置)

从 oled.py 提取的独立功能屏幕，不包含硬件初始化和主循环逻辑。
所有外部依赖通过 init_screens() 注入，消除循环导入。

功能列表:
  - WiFi:        show_connected_wifi(), scan_wifi(), connect_selected_wifi()
  - 舵机校准:     strum_servo_calibration(), chord_servo_calibration()
  - 舵机测试:     servo_test_program()
  - 吉他调音器:   guitar_tuner_ui()
  - 演奏控制:     play_song(song_name, source_dir)
  - AI 设置:      set_wake_word(), set_wake_method(), set_asr_engine(), set_tts_engine()
  - 音量设置:     set_mic_volume(), set_speaker_volume()
  - 电源监控:     power_monitor_ui()
  - 设备信息:     device_info()

工具函数:
  - read_sensors_safe()  — 安全读取电源数据
  - wait_for_esc()       — 阻塞等待 ESC 按键
  - generic_confirm()     — 通用确认对话框
"""
from logger import get_logger
_log = get_logger("screen")

import time
import os
import threading
import subprocess
import numpy as np
from typing import Optional

# 模块级常量：舵机配置文件绝对路径（避免 CWD 依赖）
_SERVO_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "config", "servo_config.csv")

# 整轮校准结束后 OLED 报告停留时长（秒），ESC 可提前跳过
CALIB_REPORT_HOLD_S = 3.0

from PIL import ImageDraw

# ──────────── 本地模块引用（延迟，避免循环导入） ────────────
# 所有外部依赖通过模块级变量注入
_renderer = None
_wifi_manager = None
_power_monitor = None
_ai_ctx = None
_strumming_module = None
_voice_player = None
_ai_available = False
_vosk_available = False
_wake_words = ["小爱同学"]
_i2c = None
_fonts = None

# 从 data.py / AI_voice_assistant 来的全局变量可通过 init 设置
# 按键引用由 hardware 模块提供


def init_screens(*, renderer, wifi_manager, power_monitor, i2c, fonts,
                 ai_ctx, strumming_module, ai_available, vosk_available, wake_words,
                 voice_player=None):
    """在 main.py 中调用，注入所有依赖。"""
    global _renderer, _wifi_manager, _power_monitor
    global _ai_ctx, _strumming_module, _voice_player
    global _ai_available, _vosk_available, _wake_words
    global _i2c, _fonts
    _renderer = renderer
    _wifi_manager = wifi_manager
    _power_monitor = power_monitor
    _ai_ctx = ai_ctx
    _strumming_module = strumming_module
    _voice_player = voice_player
    _ai_available = ai_available
    _vosk_available = vosk_available
    _wake_words = wake_words
    _i2c = i2c
    _fonts = fonts


# ──────────── 工具函数 ────────────
from state import get_state, update_state, STATE_MAIN, STATE_MENU, STATE_FUNCTION
from state import STATE_WIFI_SCAN, STATE_WIFI_CONN, STATE_WIFI_SCANING
from state import STATE_CALIBRATION, STATE_TUNER
from hardware import up_button, down_button, set_button, esc_button, is_pressed
from servo_controller import ServoController
from menu_system import MENU_ITEMS, get_music_folders


def _font_small():
    return _fonts["font_small"]


def _font_tiny():
    return _fonts["font_tiny"]


def wait_for_esc():
    """等待短按 Esc 退出（阻塞）"""
    while True:
        if is_pressed("esc"):
            time.sleep(0.05)
            if is_pressed("esc"):
                while is_pressed("esc"):
                    time.sleep(0.01)
                break
        time.sleep(0.05)


def read_sensors_safe():
    try:
        vbat = _power_monitor.battery_voltage()
    except Exception:
        vbat = None
    try:
        temp = _power_monitor.board_temperature()
    except Exception:
        temp = None
    # ★ v2.8.0: 通道无效（哨兵 0xFFFF / safe mode）时返回 None。
    #   这里只做显示收口（无读数 → 0.0），绝不参与任何物理量换算；
    #   校准等计算路径走 chord_servo_current()/read_chord_current_fresh()，已独立防护。
    return (0.0 if vbat is None else vbat), (0.0 if temp is None else temp)


def get_wifi_display_info():
    info = _wifi_manager.get_connected_wifi_info()
    if info:
        signal = _wifi_manager.get_signal_strength()
        return info.get("ssid", ""), info.get("ip_address", ""), (signal + "%" if signal else "0%")
    return "", "", "0%"


# ──────────── 通用确认对话框 ────────────
def _is_lcd_renderer() -> bool:
    """当前渲染器是不是 240×240 彩屏（LCDRenderer）。

    探针刻意选「只存在于彩屏渲染器的方法」（show_servo_adjust）：
    OLED 小屏也有同名的 show_message 之类，但版式与签名不同，直接按名字
    duck-typing 会踩坑。
    """
    return hasattr(_renderer, "show_servo_adjust")


def generic_confirm(title, message):
    """通用确认对话框（"做之前先问一句"）。

    彩屏（LCDRenderer）走 240×240 主题大字版式、只推一帧不闪；
    只有 OLED 小屏时才回退到原来的 128×64 自绘版式（坐标写死 0~127）。
    """
    update_state("running_function", True)
    selected_option = 0
    last_up, last_down, last_set = False, False, False
    esc_press_start = 0
    _on_lcd = _is_lcd_renderer()
    try:
        while True:
            if _on_lcd:
                # 彩屏：主题大字确认框（舵机归位 / 释放力矩等都在用）
                _renderer.show_yesno_dialog(title, message, selected_option)
            else:
                _renderer.clear()
                img = _renderer.new_image()
                draw = ImageDraw.Draw(img)
                draw.text((2, 2), title[:18], font=_font_small(), fill=255)
                draw.text((2, 15), message[:22], font=_font_small(), fill=255)
                options = ["是 (确认)", "否 (返回)"]
                for i, opt in enumerate(options):
                    y = 28 + i * 16
                    if i == selected_option:
                        draw.rectangle((0, y, 127, y + 15), fill=255)
                        color = 0
                    else:
                        color = 255
                    draw.text((6, y + 2), opt, font=_font_small(), fill=color)
                _renderer.show(img)

            up_pressed = is_pressed("up")
            down_pressed = is_pressed("down")
            set_pressed = is_pressed("set")
            esc_pressed = is_pressed("esc")

            if up_pressed and not last_up:
                selected_option = (selected_option - 1) % 2
                time.sleep(0.15)
            if down_pressed and not last_down:
                selected_option = (selected_option + 1) % 2
                time.sleep(0.15)

            if set_pressed and not last_set:
                if selected_option == 0:
                    while is_pressed("set"):
                        time.sleep(0.02)
                    return True
                else:
                    return False
            if esc_pressed:
                if esc_press_start == 0:
                    esc_press_start = time.time()
                elif time.time() - esc_press_start >= 0.15:
                    return False
            else:
                esc_press_start = 0

            last_up, last_down, last_set = up_pressed, down_pressed, set_pressed
            time.sleep(0.02)
    finally:
        update_state("running_function", False)


# ──────────── 舵机校准/测试功能 ────────────
def _update_calibration_progress(progress, status):
    update_state("calibration_progress", progress)
    update_state("calibration_status", status)
    update_state("display_dirty", True)


def _calibration_thread_wrapper(method_name, method_kwargs, title, stop_event):
    # 整轮校准正常完成标志：仅正常完成时在结束前短暂停留显示 OLED 报告
    completed_ok = False
    # 暂停 AI 语音助手以释放音频设备（拨弦校准需要独占麦克风）
    ai_was_paused = False
    if _ai_ctx and method_name == "calibrate_all_strum_servos":
        try:
            _ai_ctx.set_control("pause", True)
            _ai_ctx.set_control("stop_flag", True)
            ai_was_paused = True
            _log.info("已暂停 AI 语音助手")
            time.sleep(0.5)
        except Exception as e:
            _log.warning("暂停 AI 失败: %s", e)

    controller = None
    pm_paused = False    # 确保 finally 块中可访问
    try:
        try:
            # ★ 复用全局 PowerMonitor 实例，避免重复创建 FrameLayer
            shared_ser = None  # SerialBus 统一管理串口，不再暴露裸串口对象
            controller = ServoController(_SERVO_CONFIG_PATH,
                                         power_monitor=_power_monitor)
            controller.enable_debug_commands()
        except Exception as e:
            _log.error("舵机控制器初始化失败: %s", e)
            _update_calibration_progress(0.0, f"初始化失败: {e}")
            return

        # ★ 注: V3 模式下 PowerMonitor 仅被动接收 ADC_REPORT 帧，
        # 不主动发送串口命令，无需 pause。保留 pm_paused 变量兼容 finally 块。
        pm_paused = False

        func = getattr(controller, method_name, None)
        if func is None or not callable(func):
            _log.error("ServoController 缺少方法: %s", method_name)
            _update_calibration_progress(0.0, f"未知方法: {method_name}")
            return
        kwargs = {
            "progress_callback": lambda p, s: _update_calibration_progress(p, s),
            "stop_event": stop_event,
        }
        kwargs.update(method_kwargs)
        func(**kwargs)
        completed_ok = True

        # ★ V4: 校准参数仅保存在 Python 本地 servo_config.csv，
        # ESP32 不需要校准数据（角度由 Python 预计算）。
        # esp32_send_servo_config 保留用于兼容旧固件，发送失败不影响功能。
        try:
            import esp32_player
            esp32_player.esp32_send_servo_config(
                shared_ser=shared_ser, servo_controller=controller
            )
        except Exception as e:
            _log.debug("校准后 ESP32 同步跳过 (V4不需要): %s", e)
        _update_calibration_progress(100.0, "校准完成 ✓")

    except Exception as e:
        _log.error("校准/测试出错: %s", e, exc_info=True)
        _update_calibration_progress(0.0, f"错误: {e}")
    finally:
        if controller is not None:
            try:
                controller.disable_debug_commands()
            except Exception:
                pass
            try:
                controller.cleanup()
            except Exception as e:
                _log.warning("cleanup 出错: %s", e)
        # 恢复 PowerMonitor ADC 读取
        if pm_paused and _power_monitor:
            try:
                _power_monitor.resume()
                _log.info("已恢复 PowerMonitor ADC 读取")
            except Exception as e:
                _log.warning("恢复 PowerMonitor 失败: %s", e)
        # 恢复 AI 语音助手
        if ai_was_paused and _ai_ctx:
            try:
                _ai_ctx.set_control("pause", False)
                _ai_ctx.set_control("stop_flag", False)
                _ai_ctx.start()
                _log.info("已恢复 AI 语音助手")
            except Exception as e:
                _log.warning("恢复 AI 失败: %s", e)

        # ★ 整轮校准报告：OLED 停留 ~3s 展示精简结果（放 finally 尾部：
        #   cleanup/恢复已完成，屏幕定格在报告而非运动画面；ESC 可提前跳过）。
        #   仅整轮和弦/拨弦校准的正常完成路径进入；测试程序/异常路径维持原行为。
        if completed_ok and method_name in ("calibrate_all_chord_servos",
                                            "calibrate_all_strum_servos"):
            try:
                import servo_controller as _sc_mod
                kind = "chord" if method_name == "calibrate_all_chord_servos" else "strum"
                text = _sc_mod.calib_report_short_text(kind)
                _update_calibration_progress(100.0, text)
                _log.info("校准结果(OLED): %s", text)
                report_deadline = time.time() + CALIB_REPORT_HOLD_S
                while time.time() < report_deadline:
                    if stop_event and stop_event.is_set():
                        break
                    time.sleep(0.25)
            except Exception as e:
                _log.warning("校准报告展示失败: %s", e)


def _start_calibration(title, method_name, method_kwargs, skip_confirm=False):
    """通用启动校准/测试：确认 -> 启动线程 -> 进入校准状态。
    skip_confirm=True 时跳过内部确认对话框（调用方已自行确认）。"""
    if not skip_confirm:
        if not generic_confirm(title, "是否开始？"):
            update_state("current_state", STATE_MENU)
            update_state("display_dirty", True)
            return

    stop_event = threading.Event()
    update_state("calibration_stop_event", stop_event)
    update_state("calibration_title", title)
    update_state("calibration_progress", 0.0)
    update_state("calibration_status", "准备中...")
    update_state("current_state", STATE_CALIBRATION)
    update_state("running_function", True)
    update_state("display_dirty", True)

    t = threading.Thread(
        target=_calibration_thread_wrapper,
        args=(method_name, method_kwargs, title, stop_event),
        daemon=True,
    )
    update_state("calibration_thread", t)
    t.start()


# ──────────── 拨弦校准辅助函数 ────────────

def _pause_ai_for_calibration() -> bool:
    """暂停 AI 语音助手以释放音频设备。返回是否成功暂停。"""
    if not _ai_ctx:
        return False
    try:
        _ai_ctx.set_control("pause", True)
        _ai_ctx.set_control("stop_flag", True)
        _log.info("已暂停 AI 语音助手（释放音频设备）")
        time.sleep(0.5)
        return True
    except Exception as e:
        _log.warning("暂停 AI 失败: %s", e)
        return False


def _resume_ai_after_calibration():
    """恢复 AI 语音助手。"""
    if not _ai_ctx:
        return
    try:
        _ai_ctx.set_control("pause", False)
        _ai_ctx.set_control("stop_flag", False)
        _log.info("已恢复 AI 语音助手")
    except Exception as e:
        _log.warning("恢复 AI 失败: %s", e)


# ──────────── Web 单舵机拨弦预检助手（纯新增，OLED 流程不调用） ────────────

def pause_ai() -> bool:
    """暂停 AI 语音助手（Web 拨弦噪音测量/校准前调用，与 OLED wrapper 序列一致）。"""
    if not _ai_ctx:
        return False
    try:
        _ai_ctx.set_control("pause", True)
        _ai_ctx.set_control("stop_flag", True)
        time.sleep(0.5)
        return True
    except Exception as e:
        _log.warning("暂停 AI 失败: %s", e)
        return False


def resume_ai():
    """恢复 AI 语音助手（含 start()，与 OLED wrapper 恢复序列一致；勿用不带 start 的旧助手）。"""
    if not _ai_ctx:
        return
    try:
        _ai_ctx.set_control("pause", False)
        _ai_ctx.set_control("stop_flag", False)
        _ai_ctx.start()
    except Exception as e:
        _log.warning("恢复 AI 失败: %s", e)


def measure_noise_floor() -> Optional[float]:
    """采集 2s 环境音返回噪音基准（无任何 OLED 渲染，供 Web 拨弦预检）。

    与 _pre_check_noise 的采集核心一致（pyaudio 设备0 + calibrate_noise），
    但不做 OLED 倒计时/提示画面；调用前需先 pause_ai() 释放麦克风。
    """
    import pyaudio
    from guitar_tuner import calibrate_noise, SAMPLE_RATE, CHUNK_FAST

    p = None
    try:
        p = pyaudio.PyAudio()
        # 显式使用硬件设备 0 (rockchip,rk809-codec)，避免 PulseAudio "default" 设备冲突
        device_index = 0
        noise_floor = calibrate_noise(p, device_index, SAMPLE_RATE, CHUNK_FAST, duration=2.0)
        _log.info("环境噪音测量(Web): %.1f", noise_floor)
        return noise_floor
    except Exception as e:
        _log.error("噪音测量失败: %s", e)
        return None
    finally:
        if p is not None:
            try:
                p.terminate()
            except Exception:
                pass


def _pre_check_noise() -> Optional[float]:
    """预检环境噪音水平。
    先 OLED 倒计时 3s 让环境安静，再采集 2s 背景音计算 RMS 中值。
    返回 noise_floor (float)，音频设备不可用时返回 None。
    必须在主线程调用（pyaudio 操作）。"""
    import pyaudio
    from guitar_tuner import calibrate_noise, SAMPLE_RATE, CHUNK_FAST, FORMAT, CHANNELS

    # ── 3s 静音等待，直接渲染倒计时 ──
    # 注：本函数在主线程同步执行（阻塞主循环），必须直接渲染而非通过状态变量
    #     彩屏走 240×240 主题大字（show_message 同步推帧），OLED 小屏回退自绘
    _on_lcd = _is_lcd_renderer()
    for i in range(3, 0, -1):
        if _on_lcd:
            _renderer.show_message(["拨弦舵机自动校准", "请保持安静... %ds" % i],
                                   hint="按 ESC 退出")
        else:
            _renderer.clear()
            img = _renderer.new_image()
            draw = ImageDraw.Draw(img)
            draw.text((2, 2), "拨弦舵机自动校准", font=_font_small(), fill=255)
            draw.text((2, 22), f"请保持安静... {i}s", font=_font_small(), fill=255)
            draw.text((2, 46), "按Esc退出", font=_font_small(), fill=128)
            _renderer.show(img)
        time.sleep(1)

    # ── 采集 2s 环境音 ──
    if _on_lcd:
        _renderer.show_message(["拨弦舵机自动校准", "正在采集环境音..."])
    else:
        _renderer.clear()
        img = _renderer.new_image()
        draw = ImageDraw.Draw(img)
        draw.text((2, 2), "拨弦舵机自动校准", font=_font_small(), fill=255)
        draw.text((2, 22), "正在采集环境音...", font=_font_small(), fill=255)
        _renderer.show(img)

    p = None
    try:
        p = pyaudio.PyAudio()
        # 显式使用硬件设备 0 (rockchip,rk809-codec)，避免 PulseAudio "default" 设备冲突
        device_index = 0
        noise_floor = calibrate_noise(p, device_index, SAMPLE_RATE, CHUNK_FAST, duration=2.0)
        _log.info("环境噪音预检: %.1f", noise_floor)
        return noise_floor
    except Exception as e:
        _log.error("噪音预检失败: %s", e)
        return None
    finally:
        if p is not None:
            try:
                p.terminate()
            except Exception:
                pass


def _restore_strum_angles(saved_angles):
    """恢复拨弦舵机到保存的角度（出错时静默忽略）。"""
    if not saved_angles:
        return
    try:
        shared_ser = _power_monitor._ser if _power_monitor else None
        sc = ServoController(_SERVO_CONFIG_PATH, existing_ser=shared_ser)
        sc._update_strum_channels([a[0] for a in saved_angles], delay=0.5)
        sc.cleanup()
    except Exception as e:
        _log.warning("恢复拨弦角度失败: %s", e)


# ──────────── 拨弦舵机自动校准（完整流程） ────────────

def strum_servo_calibration():
    """拨弦舵机自动校准:
       步骤1: 拨片位置视觉检查（确认机械安装）
       步骤2: 3s 静音等待
       步骤3: 采集 2s 环境音
       步骤4: 噪音判断 — ≤300 直接校准，>300 提示用户二次确认
    """
    HIGH_NOISE_THRESHOLD = 300

    # ═══ 步骤1: 拨片位置视觉检查 ═══
    saved_angles = None
    temp_sc = None
    try:
        shared_ser = _power_monitor._ser if _power_monitor else None
        temp_sc = ServoController(_SERVO_CONFIG_PATH, existing_ser=shared_ser)
        saved_angles = [row[:] for row in temp_sc._strum_angles]
        # 移动全部 6 个拨弦舵机到 90°
        temp_sc._update_strum_channels([90.0] * 6, delay=1.0)

        if not generic_confirm("拨片位置检查", "黑色拨片尖是否在弦上方？"):
            # 取消：恢复到原来的 point_a
            temp_sc._update_strum_channels([a[0] for a in saved_angles], delay=0.5)
            temp_sc.cleanup()
            update_state("current_state", STATE_MENU)
            update_state("display_dirty", True)
            return
        temp_sc.cleanup()
    except Exception as e:
        _log.error("拨片检查失败: %s", e)
        if temp_sc:
            try:
                temp_sc.cleanup()
            except Exception:
                pass
        generic_confirm("错误", "舵机通信失败")
        update_state("current_state", STATE_MENU)
        update_state("display_dirty", True)
        return

    # ═══ 步骤2+3: 暂停 AI → 3s 静音等待 + 2s 采集环境音 ═══
    # 暂停 AI 语音助手以释放麦克风（拨弦校准需要独占音频设备）
    # 注意：AI 暂停后不在此时恢复，由校准线程的 _calibration_thread_wrapper 统一管理恢复
    _pause_ai_for_calibration()
    noise_floor = _pre_check_noise()

    if noise_floor is None:
        _resume_ai_after_calibration()
        generic_confirm("错误", "无法访问麦克风设备！")
        _restore_strum_angles(saved_angles)
        update_state("current_state", STATE_MENU)
        update_state("display_dirty", True)
        return

    # ═══ 步骤4: 噪音判断 ═══
    noise_override = None
    if noise_floor > HIGH_NOISE_THRESHOLD:
        if not generic_confirm(
            f"环境噪音({int(noise_floor)})偏高",
            "仍要继续？将使用实测值"
        ):
            _resume_ai_after_calibration()
            _restore_strum_angles(saved_angles)
            update_state("current_state", STATE_MENU)
            update_state("display_dirty", True)
            return
        noise_override = noise_floor
        _log.info("用户确认高噪音环境校准，noise_floor 覆写为 %.1f", noise_floor)
    # noise_floor ≤ 300 → noise_override 保持 None，校准内部自行采集

    # ═══ 启动校准（跳过 _start_calibration 的内部确认）═══
    _start_calibration(
        "拨弦舵机自动校准",
        "calibrate_all_strum_servos",
        {"noise_floor_override": noise_override},
        skip_confirm=True,
    )


def chord_servo_calibration():
    _start_calibration(
        "和弦舵机自动校准",
        "calibrate_all_chord_servos",
        {"stable_duration": 0.5},
    )


def servo_test_program():
    _start_calibration("舵机测试程序", "test_all_strings_with_progress", {"chord_delay": 0.9, "strum_delay": 0.4})


def servo_home_ui():
    """一键归位（装机用）：所有舵机回到收缩位并保持力矩。

    和弦舵机回到配置里的「装机角度」（默认 90°）、拨弦舵机回到 point_a ——
    齿条收回、不顶琴弦，这样才能把装置套到琴弦上。归位后保持使能，齿条不会自己跑位。
    想改这个角度 → 菜单里的「装机角度微调」。
    """
    try:
        from servo_controller import load_home_angles
        ang = int(round(load_home_angles()["chord"]))
    except Exception:
        ang = 90
    if not generic_confirm("舵机归位", "所有舵机回收缩位？"):
        update_state("current_state", STATE_MENU)
        update_state("display_dirty", True)
        return

    _renderer.show_message(["舵机归位中...", "和弦→%d°  拨弦→收缩" % ang], duration=0)
    ok, msg = False, "未连接"
    try:
        import esp32_player as _ep
        ok, msg = _ep.esp32_home_servos(hold=True)
    except Exception as e:
        msg = str(e)
    _log.info("设备端一键归位: ok=%s msg=%s", ok, msg)
    _renderer.show_message(["✅ 归位完成" if ok else "❌ 归位失败", str(msg)[:18]],
                           duration=1.8)
    update_state("current_state", STATE_MENU)
    update_state("display_dirty", True)


# 和弦收缩位可调范围（度）：太小可能顶琴弦、太大可能压到弦，60~120 留足余量
_SERVO_HOME_ANGLE_MIN = 60.0
_SERVO_HOME_ANGLE_MAX = 120.0
#: 拨弦收缩位的「统一偏移」量程（与 servo_controller 的常量一致）
_SERVO_STRUM_OFFSET_MIN = -45.0
_SERVO_STRUM_OFFSET_MAX = 45.0


def servo_home_adjust_ui():
    """装机角度微调：UP/DOWN 调角度，**长按 SET 切「和弦 / 拨弦」**，SET 保存退出。

    为什么需要它：每台机器的齿条装配相位略有差异，90° 未必是齿条最平的位置。
    这里边调边把舵机**真的挪过去**——盯着齿条调到正好贴合，SET 存下来长期生效。
    长按 UP/DOWN 会连发（过了 0.4s 每 0.12s 走 1°），省得一直点。

    两组目标（顶部页签显示当前是哪一组）：
      · 和弦 —— 12 路压弦舵机的**绝对**收缩角（默认 90°，量程 60~120）
      · 拨弦 —— 6 路拨弦舵机的**统一偏移**（默认 0°，量程 -45~+45）
                偏移叠在每根弦自己的 point_a 上，保留各弦安装相位，
                不会把某根弦硬顶到琴身上；调到 -20° 就是"六路一起再收 20°"。

    按键（全程不需要第 4 个 ESC 触摸块）：
      UP / DOWN   当前组的 ±1°（按住连调）
      短按 SET    保存当前组并归位退出
      长按 SET    切到另一组（≥1.0s，不会退出，切完可以接着调）
      ESC（如有） 取消退出
    """
    try:
        import esp32_player as _ep
    except Exception as e:
        _renderer.show_message(["舵机模块不可用", str(e)[:16]], duration=2.0)
        return
    try:
        from servo_controller import load_home_angles
        _cfg = load_home_angles()
        chord_saved = float(_cfg["chord"])
        strum_saved = float(_cfg.get("strum_offset", 0.0))
    except Exception:
        chord_saved, strum_saved = 90.0, 0.0
    chord_saved = max(_SERVO_HOME_ANGLE_MIN, min(_SERVO_HOME_ANGLE_MAX, chord_saved))
    strum_saved = max(_SERVO_STRUM_OFFSET_MIN, min(_SERVO_STRUM_OFFSET_MAX, strum_saved))

    update_state("running_function", True)
    target = "chord"
    angle = chord_saved
    last_up = last_dn = False
    up_since = dn_since = 0.0
    up_fire = dn_fire = 0.0
    set_since = esc_since = 0.0
    set_fired = False
    dirty = True
    hint = "上/下:±1°  SET:保存  长按SET:取消"
    try:
        # 进入：切一次 NORMAL 模式 + 把舵机挪到当前角度
        # （连调时不再切模式 —— 模式切换要等 ACK，会跟不上手速）
        ok, msg = _ep.esp32_servo_jog(angle, target="chord", hold=True, ensure_mode=True)
        if not ok:
            _renderer.show_message(["舵机不可用", str(msg)[:16]], duration=2.0)
            return

        while True:
            if dirty:
                # 彩屏走专用「舵机调整」大屏（页签 + 大号角度 + 刻度滑条 + 默认位标线，
                # 专为只有 UP/SET/DOWN 三个触摸键设计）；OLED 老屏退回通用滑条。
                if hasattr(_renderer, "show_servo_adjust"):
                    if target == "chord":
                        _renderer.show_servo_adjust(
                            angle, _SERVO_HOME_ANGLE_MIN, _SERVO_HOME_ANGLE_MAX, 90.0,
                            tabs=["和弦", "拨弦"], tab_index=0,
                            hint1="UP / DOWN 调整 · 按住连调",
                            hint2="长按SET 切换组 · SET 保存退出")
                    else:
                        _renderer.show_servo_adjust(
                            angle, _SERVO_STRUM_OFFSET_MIN, _SERVO_STRUM_OFFSET_MAX, 0.0,
                            signed=True, default_label="默认位",
                            tabs=["和弦", "拨弦"], tab_index=1,
                            hint1="UP / DOWN 调整 · 按住连调",
                            hint2="长按SET 切换组 · SET 保存退出")
                else:
                    if target == "chord":
                        lo, hi, ttl = (_SERVO_HOME_ANGLE_MIN, _SERVO_HOME_ANGLE_MAX,
                                       "装机角度（和弦收缩位）")
                    else:
                        lo, hi, ttl = (_SERVO_STRUM_OFFSET_MIN, _SERVO_STRUM_OFFSET_MAX,
                                       "装机偏移（拨弦收缩位）")
                    pct = int(round((angle - lo) / (hi - lo) * 100))
                    _renderer.show_volume_slider(
                        ttl, pct,
                        value_text=("%+d°" % int(round(angle)) if target == "strum"
                                    else "%d°" % int(round(angle))),
                        hint=hint)
                dirty = False

            now = time.time()
            up = is_pressed("up")
            dn = is_pressed("down")
            st = is_pressed("set")
            es = is_pressed("esc")

            # ── 上/下：单击 ±1°，按住 0.4s 后每 0.12s 连发 ──
            if target == "chord":
                lo_a, hi_a = _SERVO_HOME_ANGLE_MIN, _SERVO_HOME_ANGLE_MAX
            else:
                lo_a, hi_a = _SERVO_STRUM_OFFSET_MIN, _SERVO_STRUM_OFFSET_MAX
            step = 0
            if up and not last_up:
                step, up_since, up_fire = +1, now, now
            elif up and up_since and (now - up_since) >= 0.4 and (now - up_fire) >= 0.12:
                step, up_fire = +1, now
            elif not up:
                up_since = 0.0
            if dn and not last_dn:
                step, dn_since, dn_fire = -1, now, now
            elif dn and dn_since and (now - dn_since) >= 0.4 and (now - dn_fire) >= 0.12:
                step, dn_fire = -1, now
            elif not dn:
                dn_since = 0.0

            if step:
                angle = max(lo_a, min(hi_a, angle + step))
                _ep.esp32_servo_jog(angle, target=target, hold=True, ensure_mode=False)
                dirty = True

            # ── SET：短按=保存并归位退出；按住 ≥1.0s=切 和弦/拨弦（不退出）──
            if st and not set_since:
                set_since = now
                set_fired = False
            elif st and set_since and (not set_fired) and (now - set_since) >= 1.0:
                set_fired = True
                if target == "chord":
                    target = "strum"
                    angle = strum_saved
                    _ep.esp32_servo_jog(angle, target="strum", hold=True, ensure_mode=False)
                else:
                    target = "chord"
                    angle = chord_saved
                    _ep.esp32_servo_jog(angle, target="chord", hold=True, ensure_mode=False)
                up_since = dn_since = 0.0          # 别把切换前攒的连发带过来
                _log.info("[servo] 装机角度微调切换到 %s 组（当前 %.0f）", target, angle)
                dirty = True
            elif (not st) and set_since:
                if not set_fired:
                    if target == "chord":
                        chord_saved = angle
                        _ep.esp32_home_servos(hold=True, chord_angle=angle, save=True)
                        _renderer.show_message(
                            ["✅ 已保存", "和弦收缩角 %d°" % int(round(angle))],
                            duration=1.6)
                    else:
                        strum_saved = angle
                        _ep.esp32_home_servos(hold=True, strum_offset=angle,
                                              save_strum=True)
                        _renderer.show_message(
                            ["✅ 已保存", "拨弦偏移 %+d°" % int(round(angle))],
                            duration=1.6)
                    break
                set_since = 0.0
                set_fired = False

            # ── ESC：按下 0.15s 取消（第 4 个触摸模块到货后可用）──
            if es:
                if not esc_since:
                    esc_since = now
                elif (now - esc_since) >= 0.15:
                    _ep.esp32_home_servos(hold=True)
                    _renderer.show_message(["已取消", "未改动"], duration=1.2)
                    break
            else:
                esc_since = 0.0

            last_up, last_dn = up, dn
            time.sleep(0.02)
    finally:
        update_state("running_function", False)
        update_state("current_state", STATE_MENU)
        update_state("display_dirty", True)


def servo_release_ui():
    """释放力矩：让舵机全部失能（松开保持力矩），可用手推动齿轮/齿条微调。"""
    if not generic_confirm("释放力矩", "舵机将松力可手推？"):
        update_state("current_state", STATE_MENU)
        update_state("display_dirty", True)
        return
    _renderer.show_message(["释放力矩中..."], duration=0)
    ok, msg = False, "未连接"
    try:
        import esp32_player as _ep
        ok, msg = _ep.esp32_servo_release()
    except Exception as e:
        msg = str(e)
    _log.info("设备端释放力矩: ok=%s msg=%s", ok, msg)
    _renderer.show_message(["✅ 已松力可手推" if ok else "❌ 失败", str(msg)[:18]],
                           duration=1.8)
    update_state("current_state", STATE_MENU)
    update_state("display_dirty", True)


# ──────────── 吉他调音器功能 ────────────
def guitar_tuner_ui():
    """吉他调音器界面：在后台线程运行 PrecisionGuitarTuner，实时更新 OLED。"""
    import guitar_tuner
    import pyaudio

    GUITAR_STRINGS = guitar_tuner.GUITAR_STRINGS
    update_state("running_function", True)

    # 注意：AI 语音引擎由 main.py 的 page_active 机制自动管理，
    # 当用户从主页面进入菜单/调音器时 AI 已自动暂停，音频设备已释放，
    # 因此不再需要手动停止/重启 AI。

    stop_event = threading.Event()
    update_state("tuner_stop_event", stop_event)
    update_state("tuner_display_text", "初始化中...")
    update_state("tuner_target_string", "")
    update_state("tuner_cents", 0.0)
    update_state("tuner_locked", False)
    update_state("current_state", STATE_TUNER)
    update_state("display_dirty", True)

    def tuner_thread_fn():
        p = None
        stream = None
        try:
            p = pyaudio.PyAudio()

            device_index = None
            for i in range(p.get_device_count()):
                info = p.get_device_info_by_index(i)
                name = info.get("name", "")
                if info["maxInputChannels"] > 0:
                    if name == "default" and device_index is None:
                        device_index = i
            if device_index is None:
                try:
                    default = p.get_default_input_device_info()
                    device_index = default["index"]
                except OSError:
                    pass
            if device_index is None:
                for i in range(p.get_device_count()):
                    if p.get_device_info_by_index(i)["maxInputChannels"] > 0:
                        device_index = i
                        break
            if device_index is None:
                update_state("tuner_display_text", "无音频输入设备")
                return
            _log.info("调音器音频设备: 索引=%d", device_index)

            # 环境噪声校准
            update_state("tuner_display_text", "校准环境音...")
            stream = p.open(
                format=pyaudio.paInt16, channels=1, rate=44100,
                input=True, input_device_index=device_index,
                frames_per_buffer=2048,
            )
            rms_vals = []
            for _ in range(int(44100 / 2048 * 1.0)):
                try:
                    data = stream.read(2048, exception_on_overflow=False)
                except IOError:
                    continue
                samples = np.frombuffer(data, dtype=np.int16).astype(np.float32)
                rms_vals.append(float(np.sqrt(np.mean(samples ** 2))))
            noise_floor = float(np.median(rms_vals)) if rms_vals else 50.0
            stream.stop_stream()
            stream.close()
            time.sleep(0.5)

            CHUNK_PRECISE = 8192
            SAMPLE_RATE = 44100
            MIN_FREQ = 70.0
            MAX_FREQ = 420.0

            for retry in range(5):
                try:
                    stream = p.open(
                        format=pyaudio.paInt16, channels=1, rate=SAMPLE_RATE,
                        input=True, input_device_index=device_index,
                        frames_per_buffer=CHUNK_PRECISE,
                    )
                    break
                except OSError:
                    if retry < 4:
                        time.sleep(0.3)
                    else:
                        raise

            update_state("tuner_display_text", "等待弹奏...")
            update_state("tuner_target_string", "")
            update_state("tuner_cents", 0.0)
            update_state("tuner_locked", False)

            smooth_freq = None
            smooth_alpha = 0.3
            freq_buffer = []
            lock_end_time = 0
            lock_msg_freq = 0.0
            lock_msg_cents = 0.0
            lock_msg_target = ""
            STABLE_FRAMES = 4
            STABLE_FREQ_TOL = 0.8
            stable_history = []

            # 复用 guitar_tuner.PrecisionGuitarTuner 中的 YIN 实现，避免代码重复
            _yin_tuner = guitar_tuner.PrecisionGuitarTuner(device_index=None)
            _yin_tuner.min_freq = MIN_FREQ
            _yin_tuner.max_freq = MAX_FREQ
            _yin_tuner.yin_threshold = 0.15
            yin_pitch = _yin_tuner._yin_pitch

            while not stop_event.is_set():
                if lock_end_time > 0:
                    if time.time() < lock_end_time:
                        display_text = f"{lock_msg_target} {lock_msg_freq:.1f}Hz"
                        update_state("tuner_display_text", display_text)
                        update_state("tuner_cents", lock_msg_cents)
                        update_state("tuner_locked", True)
                        update_state("display_dirty", True)
                        time.sleep(0.05)
                        continue
                    else:
                        lock_end_time = 0
                        smooth_freq = None
                        freq_buffer.clear()
                        stable_history.clear()

                try:
                    data = stream.read(CHUNK_PRECISE, exception_on_overflow=False)
                except IOError:
                    continue

                samples = np.frombuffer(data, dtype=np.int16).astype(np.float32)
                rms = float(np.sqrt(np.mean(samples ** 2)))

                if noise_floor is None:
                    noise_floor = rms
                elif rms < noise_floor * 1.5:
                    noise_floor = 0.99 * noise_floor + 0.01 * rms

                threshold_val = max(noise_floor * 1.2, 3.0)
                if rms < threshold_val:
                    if lock_end_time == 0:
                        update_state("tuner_display_text", "等待弹奏...")
                        update_state("tuner_cents", 0.0)
                        update_state("tuner_locked", False)
                        update_state("display_dirty", True)
                    smooth_freq = None
                    freq_buffer.clear()
                    stable_history.clear()
                    time.sleep(0.05)
                    continue

                samples = samples - samples.mean()
                windowed = samples * np.hanning(len(samples))
                best_freq, best_conf = yin_pitch(windowed, SAMPLE_RATE)

                for _, target_freq in GUITAR_STRINGS:
                    period = int(round(SAMPLE_RATE / target_freq))
                    if period < 1 or period > len(windowed) // 2:
                        continue
                    enhanced = windowed.copy()
                    enhanced[period:] += 0.5 * windowed[:-period]
                    efreq, econf = yin_pitch(enhanced, SAMPLE_RATE)
                    if efreq is not None and econf > (best_conf or 0) and abs(efreq - target_freq) / target_freq < 0.15:
                        best_freq, best_conf = efreq, econf

                if best_freq is None or not (MIN_FREQ <= best_freq <= MAX_FREQ) or best_conf < 0.15:
                    if lock_end_time == 0:
                        update_state("tuner_display_text", f"搜索中... {best_freq:.1f}Hz" if best_freq else "搜索中...")
                        update_state("tuner_cents", 0.0)
                        update_state("tuner_locked", False)
                        update_state("display_dirty", True)
                    freq_buffer.clear()
                    stable_history.clear()
                    time.sleep(0.05)
                    continue

                if smooth_freq is None:
                    smooth_freq = best_freq
                else:
                    smooth_freq = smooth_alpha * smooth_freq + (1 - smooth_alpha) * best_freq

                freq_buffer.append(smooth_freq)
                if len(freq_buffer) > 7:
                    freq_buffer.pop(0)
                stable_freq = float(np.median(freq_buffer)) if len(freq_buffer) >= 3 else smooth_freq

                diffs = [(abs(stable_freq - t), n, t) for n, t in GUITAR_STRINGS]
                _, best_name, best_target = min(diffs)
                cents_dev = 1200.0 * np.log2(stable_freq / best_target)

                stable_history.append(stable_freq)
                if len(stable_history) > STABLE_FRAMES:
                    stable_history.pop(0)
                is_stable = (
                    len(stable_history) == STABLE_FRAMES
                    and max(stable_history) - min(stable_history) <= STABLE_FREQ_TOL
                )

                target_str = f"目标:{best_name}"
                update_state("tuner_target_string", target_str)
                update_state("tuner_cents", cents_dev)

                if is_stable and lock_end_time == 0:
                    lock_end_time = time.time() + 2.0
                    lock_msg_freq = stable_freq
                    lock_msg_cents = cents_dev
                    lock_msg_target = best_name
                    update_state("tuner_locked", True)
                else:
                    update_state("tuner_locked", lock_end_time > 0)

                update_state("display_dirty", True)
                time.sleep(0.05)

        except Exception as e:
            _log.error("调音器线程异常: %s", e, exc_info=True)
            import traceback
            traceback.print_exc()
            update_state("tuner_display_text", f"错误: {str(e)[:16]}")
            update_state("display_dirty", True)
        finally:
            if stream:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
            if p:
                try:
                    p.terminate()
                except Exception:
                    pass

    t = threading.Thread(target=tuner_thread_fn, daemon=True)
    update_state("tuner_thread", t)
    t.start()


# ──────────── WiFi 功能 ────────────
def show_connected_wifi():
    info = _wifi_manager.get_connected_wifi_info()
    signal = _wifi_manager.get_signal_strength() if info else None
    _renderer.show_wifi_info(info, signal)
    wait_for_esc()


def scan_wifi():
    _log.info("进入 WiFi 扫描模式")
    # ★ AP 模式（热点开启，单射频互斥）：禁止扫描，引导用户用 Web 页手动输入连接
    if _wifi_manager.is_hotspot_active():
        _log.info("AP 模式：无法扫描，提示改用 Web 页面手动输入")
        update_state("current_state", STATE_MENU)
        update_state("display_dirty", True)
        _renderer.show_message(
            ["🔒 AP 模式无法扫描", "请在电脑/手机连接本热点后", "打开 Web 页面手动输入", "WiFi 名称与密码连接"],
            duration=4)
        return
    update_state("current_state", STATE_WIFI_SCANING)
    for _ in range(3):
        _renderer.show_scanning(get_state("scan_animation_frame"))
        update_state("scan_animation_frame", (get_state("scan_animation_frame") + 1) % 4)
        time.sleep(0.5)

    networks = _wifi_manager.scan_wifi_networks()
    _wifi_manager.networks = networks
    update_state("current_state", STATE_WIFI_SCAN)
    update_state("wifi_scan_selected", 0)

    last_up = False
    last_down = False
    last_set = False
    esc_press_start = 0

    try:
        while get_state("current_state") == STATE_WIFI_SCAN:
            selected = get_state("wifi_scan_selected")
            _renderer.show_wifi_scan(_wifi_manager.networks, selected)

            up_pressed = is_pressed("up")
            down_pressed = is_pressed("down")
            set_pressed = is_pressed("set")
            esc_pressed = is_pressed("esc")

            if up_pressed and not last_up:
                if _wifi_manager.networks:
                    update_state("wifi_scan_selected", (selected - 1) % len(_wifi_manager.networks))
                time.sleep(0.1)
            if down_pressed and not last_down:
                if _wifi_manager.networks:
                    update_state("wifi_scan_selected", (selected + 1) % len(_wifi_manager.networks))
                time.sleep(0.1)

            if set_pressed and not last_set:
                if _wifi_manager.networks and len(_wifi_manager.networks) > 0:
                    idx = get_state("wifi_scan_selected")
                    if idx < len(_wifi_manager.networks):
                        wifi = _wifi_manager.networks[idx]
                        _wifi_manager.selected_wifi_ssid = wifi["ssid"]
                        _log.info("选择 WiFi: %s", wifi['ssid'])

                        from input_method import InputMethod
                        im = InputMethod(up_button, down_button, set_button, esc_button, _renderer,
                                         on_connect=lambda: update_state("current_state", STATE_WIFI_CONN))
                        result = im.run()
                        _wifi_manager.wifi_password = im.input_string

                        if result == "connect":
                            connect_selected_wifi()
                            break

            if esc_pressed:
                if esc_press_start == 0:
                    esc_press_start = time.time()
                elif time.time() - esc_press_start >= 0.15:
                    _wifi_manager.scanning = False
                    update_state("current_state", STATE_MENU)
                    update_state("display_dirty", True)
                    break
            else:
                esc_press_start = 0

            last_up, last_down, last_set = up_pressed, down_pressed, set_pressed
            time.sleep(0.02)
    except Exception as e:
        _log.error("WiFi 扫描异常: %s", e)
    finally:
        if get_state("current_state") not in [STATE_MENU, STATE_MAIN]:
            update_state("current_state", STATE_MENU)
            update_state("display_dirty", True)


def connect_selected_wifi():
    ssid = _wifi_manager.selected_wifi_ssid
    password = _wifi_manager.wifi_password
    if not ssid:
        return
    _renderer.show_message(["连接中...", ssid[:12]], duration=1)
    success = _wifi_manager.connect_to_wifi(ssid, password)
    if success:
        info = _wifi_manager.get_connected_wifi_info()
        ip = info["ip_address"] if info else "N/A"
        _renderer.show_message(["连接成功", f"IP: {ip}"], duration=3)
    else:
        _renderer.show_message(["连接失败"], duration=3)


# ──────────── 演奏功能 ────────────
def play_song(song_name, source_dir=None, skip_confirm=False):
    """演奏一首歌。

    ``skip_confirm=True``：跳过「确认 / 返回」对话框直接开弹 —— 选歌屏入口用，
    版本已由 song_library 自动挑好，多一步确认反而碍事；演奏结束回**主界面**。
    菜单入口（默认 False）保持原行为：确认框 + 结束回菜单。
    """
    update_state("running_function", True)
    selected_option = 0
    last_up, last_down, last_set = False, False, False
    esc_press_start = 0

    try:
        while not skip_confirm:
            _renderer.show_confirm_dialog(song_name, selected_option)
            up_pressed = is_pressed("up")
            down_pressed = is_pressed("down")
            set_pressed = is_pressed("set")
            esc_pressed = is_pressed("esc")

            if up_pressed and not last_up:
                selected_option = (selected_option - 1) % 2
                time.sleep(0.15)
            if down_pressed and not last_down:
                selected_option = (selected_option + 1) % 2
                time.sleep(0.15)

            if set_pressed and not last_set:
                if selected_option == 0:
                    while is_pressed("set"):
                        time.sleep(0.02)
                    break
                else:
                    return
            if esc_pressed:
                if esc_press_start == 0:
                    esc_press_start = time.time()
                elif time.time() - esc_press_start >= 0.15:
                    return
            else:
                esc_press_start = 0

            last_up, last_down = up_pressed, down_pressed
            time.sleep(0.02)

        _log.info("开始演奏: %s", song_name)

        # 使用 ESP32 独立演奏
        import esp32_player
        import servo_controller

        # 共享 PowerMonitor 的串口连接，避免重复打开 /dev/ttyS3
        shared_ser = _power_monitor._ser if _power_monitor else None
        controller = servo_controller.ServoController(_SERVO_CONFIG_PATH, existing_ser=shared_ser)
        # ★ 不再预先 cleanup(): playback::start() 内部统一处理 enable + reset_position，
        # cleanup() 的 DISABLE→re-enable 序列是多余的舵机抖动

        # ── 彩屏「演奏主界面」数据：整曲时间轴一次算好塞进 state ──
        #   ★ 2026-10-11：此前只有 web_ui/api.py 的 /api/songs/play 建时间轴，
        #     导致**板载选歌屏开弹**看不到「音浪 + 大字」演奏主界面（退化成
        #     OLED 老式小字进度条）。这里与 web 端同口径补上（唯一实现见
        #     perform_timeline.py），渲染侧 lcd_renderer.show_progress 每帧只读。
        #     失败只降级为彩色进度条，绝不拦演奏。
        try:
            import perform_timeline as _ptl
            _tl = _ptl.load_song(song_name, controller, source_dir=source_dir)
            update_state("perform_timeline", _tl)
            if _tl is not None:
                _log.info("演奏屏幕时间轴就绪: %d 音符 / %.0fs / 和弦变化 %d 处",
                          len(_tl), _tl.total_ms / 1000.0, _tl.chord_changes)
            else:
                _log.warning("未找到曲谱 CSV，彩屏退回进度条: %s", song_name)
        except Exception as _e:
            try:
                update_state("perform_timeline", None)
            except Exception:
                pass
            _log.warning("演奏屏幕时间轴构建失败（降级进度条）: %s", _e)

        started = esp32_player.esp32_play_song(song_name, servo_controller=controller,
                                                shared_ser=shared_ser, source_dir=source_dir,
                                                power_monitor=_power_monitor)

        if not started:
            _renderer.show_message(["演奏启动失败", "请检查 ESP32 连接"], duration=2)
            return

        esc_press_start = 0
        # ★ 2026-10-11：ESC 悬空（第 4 个 TTP223 未接）期间演奏**无法手动停止**——
        #   选错歌只能干等整曲弹完。补一条「长按 DOWN ≥1s 立即停」兜底；
        #   ESC 接上后两条都能用（DOWN 在演奏循环里本无其它用途，不冲突）。
        down_hold_start = 0.0
        # ★ 双击 SET → **演奏中也能切**「全自动 / 人机配合」（Z 2026-10-10）。
        #   诚实边界：整曲是**一次性预计算下发**的（esp32_player._send_binary_fast），
        #   所以这次切换**对当前这首不生效**（拨弦帧已发完），下一首才生效 ——
        #   提示里如实标注，绝不让操作者以为当场变了。
        from mode_toggle import DoubleClickDetector, toggle_performance_mode
        _set_dbl = DoubleClickDetector()
        # ★ 2026-10-11（Z：想要"好几个界面模式"）：SET **长按 0.8s** = 循环切换
        #   演奏界面模式（音浪 → 赛博霓虹 → 深海海浪 → 琥珀 CRT → …），见 theme.py。
        #   与上面「双击 SET 切全自动/人机配合」共用一颗键但互不干扰：
        #     · 双击识别器遇到 >0.4s 的按下会主动清掉候选；
        #     · 长按识别器只在"按满阈值那一刻"触发一次，按住不放不会连环切。
        #   演奏期间按键线程正阻塞在本循环里，SET 的 push-to-talk 只在主界面生效
        #   （见 button_handler.cs==STATE_MAIN 分支），所以这里独占 SET，无冲突。
        try:
            from theme import HoldDetector as _HoldDet, cycle_theme as _cycle_theme
            _set_hold = _HoldDet(0.8)
        except Exception:
            _set_hold = None
            _cycle_theme = None
        toast_until = 0.0
        last_prog_render = 0.0
        while esp32_player.esp32_is_playing():
            now = time.time()

            # 进度条仍按 ~5Hz 刷（保持原节奏）；但按键采样必须更细，
            # 否则一次 ~100ms 的短按会被 200ms 的采样周期整个漏掉 → 双击永远识别不到。
            if now - last_prog_render >= 0.2:
                last_prog_render = now
                if now >= toast_until:
                    _renderer.show_progress(song_name, esp32_player.esp32_get_progress())

            if set_button is not None and _set_dbl.feed(is_pressed("set"), now):
                res = toggle_performance_mode()
                lines = list(res.get("lines") or [])
                if res.get("ok"):
                    lines.append("本曲不生效·下一首")
                try:
                    _renderer.show_message(lines, duration=0)
                except Exception:
                    pass
                toast_until = time.time() + 2.2

            # ★ 长按 SET：循环切换演奏界面模式（配色 + 波形），立即生效、断电记住
            if _set_hold is not None and _set_hold.feed(is_pressed("set"), now):
                key = _cycle_theme()
                try:
                    from theme import theme_name as _theme_name
                    lines = ["界面模式: %s" % _theme_name(key), "长按 SET 继续切换"]
                except Exception:
                    lines = ["界面模式已切换", ""]
                try:
                    _renderer.show_message(lines, duration=0)
                except Exception:
                    pass
                toast_until = time.time() + 1.6
                _log.info("界面模式切换 → %s", key)

            if is_pressed("esc"):
                if esc_press_start == 0:
                    esc_press_start = time.time()
                elif time.time() - esc_press_start >= 0.15:
                    _log.info("用户中断演奏")
                    esp32_player.esp32_stop_song()
                    break
            else:
                esc_press_start = 0

            # ★ 2026-10-11：ESC 缺席期间的停止手段（长按 DOWN 1s），见循环前注释。
            if is_pressed("down"):
                if down_hold_start == 0.0:
                    down_hold_start = time.time()
                elif time.time() - down_hold_start >= 1.0:
                    _log.info("用户长按 DOWN 中断演奏")
                    esp32_player.esp32_stop_song()
                    break
            else:
                down_hold_start = 0.0
            time.sleep(0.05)

    finally:
        # ★ 安全网: 确保 PowerMonitor 被恢复 (防止 _notify_playback_end 因
        # _player._power_monitor 为 None 而静默跳过 resume)
        if _power_monitor and _power_monitor.is_paused:
            try:
                _power_monitor.resume()
                _log.info("PowerMonitor ADC 读取已恢复 (play_song finally)")
            except Exception as e:
                _log.error("恢复 PowerMonitor 失败: %s", e)

        update_state("running_function", False)
        # ★ 2026-10-11：收起演奏主界面数据（时间轴只在本曲有效），
        #   否则下一首/校准会误用上一曲的谱面。
        try:
            update_state("perform_timeline", None)
        except Exception:
            pass
        # 选歌屏入口回主界面；菜单入口回菜单（原行为不变）
        update_state("current_state", STATE_MAIN if skip_confirm else STATE_MENU)
        update_state("display_dirty", True)
        if skip_confirm:
            # 让主循环立刻重画主界面，擦掉进度条残影
            update_state("force_redraw", True)

        # ★ 立即强制渲染菜单，避免进度条在 OLED 上残留（仅菜单入口需要）
        if not skip_confirm:
            try:
                from menu_system import MENU_ITEMS
                menu = MENU_ITEMS.get(get_state("current_menu"))
                items = menu["items"] if menu else []
                _renderer.show_menu(
                    items, get_state("selected_index"),
                    title=menu.get("title") if menu else None,
                )
            except Exception:
                pass  # 渲染失败不影响状态恢复


# ──────────── AI 设置功能 ────────────
def set_wake_word():
    if not _ai_ctx:
        _renderer.show_message(["AI功能不可用", "缺少依赖库"], hint="按Esc返回")
        wait_for_esc()
        return
    update_state("running_function", True)
    words = _wake_words
    current_wake_word = _ai_ctx.get_config("wake_word", "小爱同学")
    try:
        selected_idx = words.index(current_wake_word)
    except ValueError:
        selected_idx = 0

    last_up, last_down, last_set = False, False, False
    esc_press_start = 0

    try:
        while True:
            _renderer.show_menu(
                [{"name": w} for w in words], selected_idx,
                title="选择唤醒词",
            )
            up_pressed = is_pressed("up")
            down_pressed = is_pressed("down")
            set_pressed = is_pressed("set")
            esc_pressed = is_pressed("esc")

            if up_pressed and not last_up:
                selected_idx = (selected_idx - 1) % len(words)
                time.sleep(0.1)
            if down_pressed and not last_down:
                selected_idx = (selected_idx + 1) % len(words)
                time.sleep(0.1)

            if set_pressed and not last_set:
                new_word = words[selected_idx]
                _ai_ctx.set_config("wake_word", new_word)
                _renderer.show_message([f"唤醒词已设置", f"「{new_word}」"], duration=1.5)
                _ai_ctx.restart()
                break

            if esc_pressed:
                if esc_press_start == 0:
                    esc_press_start = time.time()
                elif time.time() - esc_press_start >= 0.15:
                    break
            else:
                esc_press_start = 0

            last_up, last_down, last_set = up_pressed, down_pressed, set_pressed
            time.sleep(0.02)
    finally:
        update_state("running_function", False)
        update_state("current_state", STATE_MENU)
        update_state("display_dirty", True)


def set_wake_method():
    if not _ai_ctx:
        _renderer.show_message(["AI功能不可用", "缺少依赖库"], hint="按Esc返回")
        wait_for_esc()
        return
    update_state("running_function", True)
    methods = ["语音唤醒(单次)", "按键说话(按住SET)"]
    method_values = ["voice", "button"]
    current_method = _ai_ctx.get_config("wake_method", "voice")
    try:
        selected_idx = method_values.index(current_method)
    except ValueError:
        selected_idx = 0

    last_up, last_down, last_set = False, False, False
    esc_press_start = 0

    try:
        while True:
            _renderer.show_menu(
                [{"name": m} for m in methods], selected_idx,
                title="唤醒方式",
            )
            up_pressed = is_pressed("up")
            down_pressed = is_pressed("down")
            set_pressed = is_pressed("set")
            esc_pressed = is_pressed("esc")

            if up_pressed and not last_up:
                selected_idx = (selected_idx - 1) % len(methods)
                time.sleep(0.1)
            if down_pressed and not last_down:
                selected_idx = (selected_idx + 1) % len(methods)
                time.sleep(0.1)

            if set_pressed and not last_set:
                new_method = method_values[selected_idx]
                _ai_ctx.set_config("wake_method", new_method)
                _renderer.show_message([f"唤醒方式已设置", f"「{methods[selected_idx]}」"], duration=1.5)
                _ai_ctx.restart()
                break

            if esc_pressed:
                if esc_press_start == 0:
                    esc_press_start = time.time()
                elif time.time() - esc_press_start >= 0.15:
                    break
            else:
                esc_press_start = 0

            last_up, last_down, last_set = up_pressed, down_pressed, set_pressed
            time.sleep(0.02)
    finally:
        update_state("running_function", False)
        update_state("current_state", STATE_MENU)
        update_state("display_dirty", True)


def set_asr_engine():
    if not _ai_ctx:
        _renderer.show_message(["AI功能不可用", "缺少依赖库"], hint="按Esc返回")
        wait_for_esc()
        return
    update_state("running_function", True)

    engines = ["Whisper (默认)", "Vosk (离线)"]
    engine_values = ["whisper", "vosk"]
    if not _vosk_available:
        engines[1] = "Vosk (未安装)"
    current = _ai_ctx.get_config("asr_engine", "whisper")
    try:
        selected_idx = engine_values.index(current)
    except ValueError:
        selected_idx = 0

    last_up, last_down, last_set = False, False, False
    esc_press_start = 0

    try:
        while True:
            _renderer.show_menu(
                [{"name": e} for e in engines], selected_idx,
                title="ASR引擎选择",
            )
            up_pressed = is_pressed("up")
            down_pressed = is_pressed("down")
            set_pressed = is_pressed("set")
            esc_pressed = is_pressed("esc")

            if up_pressed and not last_up:
                selected_idx = (selected_idx - 1) % len(engines)
                time.sleep(0.1)
            if down_pressed and not last_down:
                selected_idx = (selected_idx + 1) % len(engines)
                time.sleep(0.1)

            if set_pressed and not last_set:
                if selected_idx == 1 and not _vosk_available:
                    _renderer.show_message(
                        ["Vosk 未安装", "请先: pip install vosk", "并下载中文模型到",
                         f"{os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'models', 'vosk', 'vosk-model-small-cn-0.22')}"],
                        duration=3,
                    )
                    continue
                new_engine = engine_values[selected_idx]
                _ai_ctx.set_config("asr_engine", new_engine)
                _renderer.show_message([f"ASR引擎已切换", f"「{engines[selected_idx]}」"], duration=1.5)
                _ai_ctx.restart()
                break

            if esc_pressed:
                if esc_press_start == 0:
                    esc_press_start = time.time()
                elif time.time() - esc_press_start >= 0.15:
                    break
            else:
                esc_press_start = 0

            last_up, last_down, last_set = up_pressed, down_pressed, set_pressed
            time.sleep(0.02)
    finally:
        update_state("running_function", False)
        update_state("current_state", STATE_MENU)
        update_state("display_dirty", True)


def set_tts_engine():
    if not _ai_ctx:
        _renderer.show_message(["AI功能不可用", "缺少依赖库"], hint="按Esc返回")
        wait_for_esc()
        return
    update_state("running_function", True)

    engines = ["默认 TTS", "eSpeak (离线)"]
    engine_values = ["default", "espeak"]
    current = _ai_ctx.get_config("tts_engine", "default")
    try:
        selected_idx = engine_values.index(current)
    except ValueError:
        selected_idx = 0

    last_up, last_down, last_set = False, False, False
    esc_press_start = 0

    try:
        while True:
            _renderer.show_menu(
                [{"name": e} for e in engines], selected_idx,
                title="TTS引擎选择",
            )
            up_pressed = is_pressed("up")
            down_pressed = is_pressed("down")
            set_pressed = is_pressed("set")
            esc_pressed = is_pressed("esc")

            if up_pressed and not last_up:
                selected_idx = (selected_idx - 1) % len(engines)
                time.sleep(0.1)
            if down_pressed and not last_down:
                selected_idx = (selected_idx + 1) % len(engines)
                time.sleep(0.1)

            if set_pressed and not last_set:
                new_engine = engine_values[selected_idx]
                _ai_ctx.set_config("tts_engine", new_engine)
                _renderer.show_message([f"TTS引擎已切换", f"「{engines[selected_idx]}」"], duration=1.5)
                _ai_ctx.restart()
                break

            if esc_pressed:
                if esc_press_start == 0:
                    esc_press_start = time.time()
                elif time.time() - esc_press_start >= 0.15:
                    break
            else:
                esc_press_start = 0

            last_up, last_down, last_set = up_pressed, down_pressed, set_pressed
            time.sleep(0.02)
    finally:
        update_state("running_function", False)
        update_state("current_state", STATE_MENU)
        update_state("display_dirty", True)


# ──────────── 麦克风/扬声器音量设置 ────────────
def _volume_slider_ui(title, config_key, apply_func, default_val=80,
                       min_val=0, max_val=100, step=5, unit="%",
                       display_fmt=None):
    """通用音量滑动条 UI：上/下键调节，SET 确认，ESC 取消。"""
    if not _ai_ctx:
        _renderer.show_message(["AI功能不可用", "缺少依赖库"], hint="按Esc返回")
        wait_for_esc()
        return
    update_state("running_function", True)

    current = _ai_ctx.get_config(config_key, default_val)
    last_up, last_down, last_set = False, False, False
    esc_press_start = 0

    if display_fmt is None:
        def display_fmt(v):
            return v

    try:
        while True:
            # 将实际值映射到 0-100 供滑动条显示
            if max_val > min_val:
                display_pct = int((current - min_val) / (max_val - min_val) * 100)
            else:
                display_pct = 0
            _renderer.show_volume_slider(
                f"{title} ({display_fmt(current)}{unit})",
                display_pct,
            )

            up_pressed = is_pressed("up")
            down_pressed = is_pressed("down")
            set_pressed = is_pressed("set")
            esc_pressed = is_pressed("esc")

            if up_pressed and not last_up:
                current = min(max_val, current + step)
                time.sleep(0.1)
            if down_pressed and not last_down:
                current = max(min_val, current - step)
                time.sleep(0.1)

            if set_pressed and not last_set:
                _ai_ctx.set_config(config_key, current)
                try:
                    apply_func(current)
                except Exception as e:
                    _log.warning("音量应用失败: %s", e)
                _renderer.show_message(
                    [f"{title}已设置", f"当前: {display_fmt(current)}{unit}"],
                    duration=1.5,
                )
                break

            if esc_pressed:
                if esc_press_start == 0:
                    esc_press_start = time.time()
                elif time.time() - esc_press_start >= 0.15:
                    break
            else:
                esc_press_start = 0

            last_up, last_down, last_set = up_pressed, down_pressed, set_pressed
            time.sleep(0.02)
    finally:
        update_state("running_function", False)
        update_state("current_state", STATE_MENU)
        update_state("display_dirty", True)


def set_mic_volume():
    """麦克风增益设置"""
    from ai import audio_utils

    def fmt(v):
        return f"{v:.1f}"

    _volume_slider_ui(
        "麦克风增益", "mic_gain", audio_utils.set_mic_gain,
        default_val=1.5, min_val=1.0, max_val=5.0, step=0.5,
        unit="x", display_fmt=fmt,
    )


def set_speaker_volume():
    """扬声器音量设置"""
    from ai import audio_utils
    _volume_slider_ui("扬声器音量", "speaker_volume", audio_utils.set_speaker_volume)

def device_info():
    """设备信息 — 应用版本 + ESP32 硬件/固件版本。

    ★ v2.8.0: 原实现是写死的占位文本
    （固件版本: v1.0 / 硬件版本: v1.1），现在改为真实数据：
      - 应用版本取 VERSION 文件
      - ESP32 硬件版本取 0xFA CTRL_HW_INFO_ACK（开机主动上报 + 连接时补查）
      - ESP32 固件版本取 0xF8 固件信息缓存
    """
    lines = []
    ver = "?"
    try:
        _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        with open(os.path.join(_root, "VERSION"), "r", encoding="utf-8") as f:
            ver = f.read().strip().replace("!locked", "")
    except Exception:
        pass
    lines.append("吉他 " + ver)

    try:
        import esp32_player as _ep
        _p = getattr(_ep, "_player", None)
        info = getattr(_p, "hw_info", None) if _p is not None else None
        if info:
            lines.append("硬件 %s id=%s" % (info.get("name", "?"), info.get("board_id", "?")))
        else:
            lines.append("硬件 未上报")
        fw = getattr(_p, "_firmware_info_data", None) if _p is not None else None
        if _p is not None and (not fw or not fw.get("version")):
            # 未缓存时现场查询一次（这个界面本来就会阻塞等待 Esc）
            try:
                fw = _p.get_firmware_info()
            except Exception:
                fw = None
        lines.append("固件 " + (str(fw.get("version")) if fw and fw.get("version") else "--"))
        if info and info.get("safe_mode"):
            lines.append("SAFE 舵机已切断")
        elif info:
            lines.append("safe 正常 caps=%02X" % (int(info.get("caps", 0)) & 0xFF))
    except Exception:
        lines.append("ESP32 读取失败")

    _renderer.show_message(lines[:4])
    wait_for_esc()


def power_monitor_ui():
    update_state("running_function", True)
    try:
        while True:
            try:
                vbat = _power_monitor.battery_voltage()
                chord_cur = _power_monitor.chord_servo_current()
                strum_cur = _power_monitor.strum_servo_current()
                temp = _power_monitor.board_temperature()
            except Exception as e:
                _log_ = globals().get("_log")
                if _log_ is not None:
                    _log_.debug("电源读数获取失败: %s", e)
                vbat = chord_cur = strum_cur = temp = None
            try:
                _renderer.show_power_monitor(vbat, chord_cur, strum_cur, temp)
            except Exception as e:
                _log_ = globals().get("_log")
                if _log_ is not None:
                    _log_.warning("电源监控渲染失败: %s", e)
                time.sleep(0.3)
            if is_pressed("esc"):
                t0 = time.time()
                while is_pressed("esc"):
                    time.sleep(0.02)
                if time.time() - t0 >= 0.15:
                    break
            time.sleep(0.1)
    finally:
        update_state("running_function", False)
        update_state("current_state", STATE_MENU)
        update_state("display_dirty", True)


# ──────────── 她的演奏（无障碍改造 · 手动兜底入口）────────────
def her_performance_ui():
    """「她的演奏」手动触发界面 —— 触摸条失效时的兜底入口（答辩保险）。

    为什么要有它：主舞台触发源是她的**触摸条**（T000 硬规则①），但万一现场
    触摸条/GPIO/接线出问题，必须有一个**不依赖触摸条**的方式把和弦压下去，
    否则整套设计当场归零。本界面用泰山派自己的 4 个按键完成触发。

    按键：
      UP / DOWN  选择和弦（列表来自 data/chord_definitions.csv，最多滚动）
      SET        压住当前和弦并**保持**（再按同一项 = 松开）
      ESC        松开并退出（硬规则③：她随时能停）

    无硬件时（her_performance 未装配 / ESP32 未连）也安全：只提示，不崩。
    """
    update_state("running_function", True)
    hp = None
    try:
        from trigger_integration import get_her_performance
        hp = get_her_performance()
    except Exception as e:
        _log.warning("她的演奏入口取实例失败: %s", e)

    if hp is None:
        try:
            _renderer.show_message(["她的演奏", "未初始化", "(无硬件/未装配)", "ESC 返回"])
        except Exception:
            pass
        wait_for_esc()
        update_state("running_function", False)
        update_state("current_state", STATE_MENU)
        update_state("display_dirty", True)
        return

    chords = hp.chord_names() or ["C", "G", "Am", "F"]
    sel = 0
    holding = None
    last_up = last_down = last_set = False
    esc_start = 0.0
    try:
        while True:
            # ── 渲染（4 行窗口）──
            try:
                _renderer.clear()
                img = _renderer.new_image()
                draw = ImageDraw.Draw(img)
                # 第一行：**优先**显示守护层提示（和弦已就位 / 即将自动松弦），
                # 无提示时退回模式标题。她演奏中需要的是"能不能拨了"，
                # 而不是"现在是什么模式"。
                hint = ""
                try:
                    hint = hp.hint_text()
                except Exception:
                    hint = ""
                if hint:
                    draw.text((0, 0), hint[:21], font=_font_tiny(), fill=255)
                else:
                    draw.text((0, 0), "她的演奏 %s/%s" % (
                        getattr(hp, "mode", "?")[:5],
                        getattr(hp, "perf_mode", "?")), font=_font_tiny(), fill=255)
                rows = 4
                top = 0
                if len(chords) > rows:
                    top = max(0, min(sel - 1, len(chords) - rows))
                for r, i in enumerate(range(top, min(top + rows, len(chords)))):
                    y = 12 + r * 12
                    name = chords[i]
                    if i == sel:
                        draw.rectangle((0, y - 1, 127, y + 10), fill=255)
                        color = 0
                    else:
                        color = 255
                    mark = " *" if holding == name else ""
                    draw.text((2, y), ("%s%s" % (name, mark))[:15],
                              font=_font_small(), fill=color)
                _renderer.show(img)
            except Exception as e:
                _log.debug("她的演奏渲染失败: %s", e)

            # ── 按键（阻塞期间由本函数独占，见 button_handler.check_buttons 的
            #    running_function 早退）──
            up_p = is_pressed("up")
            down_p = is_pressed("down")
            set_p = is_pressed("set")
            esc_p = is_pressed("esc")

            if up_p and not last_up:
                sel = (sel - 1) % len(chords)
                time.sleep(0.15)
            if down_p and not last_down:
                sel = (sel + 1) % len(chords)
                time.sleep(0.15)
            if set_p and not last_set:
                name = chords[sel]
                try:
                    if holding == name:
                        hp.release()
                        holding = None
                    else:
                        hp.press(name)     # 切换/压住；压弦权始终在操作者手里
                        holding = name
                except Exception as e:
                    _log.error("她的演奏触发失败: %s", e)
                while set_button is not None and is_pressed("set"):
                    time.sleep(0.02)

            if esc_p:
                if esc_start == 0.0:
                    esc_start = time.time()
                elif time.time() - esc_start >= 0.15:
                    break
            else:
                esc_start = 0.0

            last_up, last_down, last_set = up_p, down_p, set_p
            time.sleep(0.02)
    finally:
        # 退出一律松弦（硬规则③：绝不留下无人控制的余音）
        try:
            if holding is not None:
                hp.release()
        except Exception:
            pass
        update_state("running_function", False)
        update_state("current_state", STATE_MENU)
        update_state("display_dirty", True)


def performance_mode_ui():
    """「演奏模式」切换界面 —— 全盘模式 / 人机配合（运行时切换，不重启程序）。

    Z 2026-10-01 定调：**两种模式并存**。
      full 全盘模式  = 机器压弦 + 机器拨弦 + 换把（= 抄《拨云》全集）
                       用途：调试 / 自检 / 机械能力演示。
                       ⚠️ **不作交付形态** —— 这一格叫"吉他演奏机"（弦真、人没在弹）。
      her  人机配合  = 机器只压弦，拨弦·节奏·力度全归她
                       用途：交付形态 / 答辩主场景（硬规则②：右手零改造）。

    按键：UP / DOWN 选模式 · SET 切到选中模式 · ESC 长按 0.15s 返回。
    切换**立即生效**（只改内存里的动作集，不写配置文件、不重启程序）；
    切换**不松弦**——不会打断她正压着的和弦。
    """
    update_state("running_function", True)
    hp = None
    try:
        from trigger_integration import get_her_performance
        hp = get_her_performance()
    except Exception as e:
        _log.warning("演奏模式入口取实例失败: %s", e)

    if hp is None or not hasattr(hp, "set_mode"):
        try:
            _renderer.show_message(["演奏模式", "未初始化", "(无硬件/未装配)", "ESC 返回"])
        except Exception:
            pass
        wait_for_esc()
        update_state("running_function", False)
        update_state("current_state", STATE_MENU)
        update_state("display_dirty", True)
        return

    try:
        modes = list(hp.mode_names())
    except Exception:
        modes = []
    if not modes:
        modes = [("full", "全盘模式"), ("her", "人机配合")]

    sel = 0
    for i, item in enumerate(modes):
        if item[0] == getattr(hp, "perf_mode", None):
            sel = i
            break

    last_up = last_down = last_set = False
    esc_start = 0.0
    try:
        while True:
            try:
                _renderer.clear()
                img = _renderer.new_image()
                draw = ImageDraw.Draw(img)
                cur = getattr(hp, "perf_mode", "?")
                draw.text((0, 0), "演奏模式 当前:%s" % cur, font=_font_tiny(), fill=255)
                for r, item in enumerate(modes[:4]):
                    y = 14 + r * 16
                    if r == sel:
                        draw.rectangle((0, y - 2, 127, y + 12), fill=255)
                        color = 0
                    else:
                        color = 255
                    mark = " *" if item[0] == cur else ""
                    draw.text((2, y), ("%s%s" % (item[1], mark))[:12],
                              font=_font_small(), fill=color)
                _renderer.show(img)
            except Exception as e:
                _log.debug("演奏模式渲染失败: %s", e)

            up_p = is_pressed("up")
            down_p = is_pressed("down")
            set_p = is_pressed("set")
            esc_p = is_pressed("esc")

            if up_p and not last_up:
                sel = (sel - 1) % len(modes)
                time.sleep(0.15)
            if down_p and not last_down:
                sel = (sel + 1) % len(modes)
                time.sleep(0.15)
            if set_p and not last_set:
                try:
                    _log.info("演奏模式切到 %s", hp.set_mode(modes[sel][0]))
                except Exception as e:
                    _log.error("演奏模式切换失败: %s", e)
                while set_button is not None and is_pressed("set"):
                    time.sleep(0.02)

            if esc_p:
                if esc_start == 0.0:
                    esc_start = time.time()
                elif time.time() - esc_start >= 0.15:
                    break
            else:
                esc_start = 0.0

            last_up, last_down, last_set = up_p, down_p, set_p
            time.sleep(0.02)
    finally:
        # 切模式**不松弦**（她的和弦继续压着）；只有退出界面不影响演奏状态
        update_state("running_function", False)
        update_state("current_state", STATE_MENU)
        update_state("display_dirty", True)


# ──────────── 演奏小结（无障碍改造 · 弹完当场看反馈）────────────
def session_summary_ui():
    """「演奏小结」界面 —— 她弹完抬头就能看到"这一场打了什么 / 哪里卡壳 / 怎么办"。

    ★ 数据在**板子本地当场算**（不走服务器）：读本机档案 `rec.summarize()` →
    `session_summary.build_practice_pages()` → 三屏文字。屏幕反馈绝不能等 22:00
    定时上传再回显，否则她当场什么都看不到。

    三屏（UP/DOWN 翻页 · ESC 返回）：
      1 这一场   —— 弹了多久 / 换和弦次数 / 用了几个和弦
      2 哪里要练 —— 保持超时的和弦；无超时则报最长保持
      3 建议     —— 一句人话

    无曲谱基准时走本"练习数据版"；将来接上跟练模式（GuidePlan）后，这里应改走
    教练版 `build_summary_pages(summary, advice)`（判 漏 / 误按 / 早晚）。
    无硬件 / 档案未启用时安全降级：只提示，不崩。
    """
    update_state("running_function", True)
    pages = None
    try:
        from trigger_integration import get_her_performance
        hp = get_her_performance()
        rec = getattr(hp, "recorder", None) if hp is not None else None
        if rec is None:
            pages = [{"title": "演奏小结",
                      "lines": ["演奏档案未启用", "(config: trigger.record", ".enabled 打开后再看)"]}]
        else:
            from session_summary import build_practice_pages
            pages = build_practice_pages(rec.summarize() or {})
    except Exception as e:
        _log.warning("演奏小结构建失败: %s", e)
        pages = [{"title": "演奏小结", "lines": ["读取失败", "ESC 返回"]}]

    if not pages:
        pages = [{"title": "演奏小结", "lines": ["暂无数据"]}]

    total = len(pages)
    idx = 0
    last_up = last_down = False
    esc_start = 0.0
    try:
        while True:
            page = pages[min(idx, total - 1)]
            try:
                # 不传底部提示：3 行正文已占满，页码在右上角已表明可翻页
                _renderer.show_session_summary(page, idx, total)
            except Exception as e:
                _log.debug("演奏小结渲染失败: %s", e)

            up_p = is_pressed("up")
            down_p = is_pressed("down")
            esc_p = is_pressed("esc")

            if up_p and not last_up:
                if total > 1:
                    idx = (idx - 1) % total
                time.sleep(0.15)
            if down_p and not last_down:
                if total > 1:
                    idx = (idx + 1) % total
                time.sleep(0.15)

            if esc_p:
                if esc_start == 0.0:
                    esc_start = time.time()
                elif time.time() - esc_start >= 0.15:
                    break
            else:
                esc_start = 0.0

            last_up, last_down = up_p, down_p
            time.sleep(0.02)
    finally:
        update_state("running_function", False)
        update_state("current_state", STATE_MENU)
        update_state("display_dirty", True)


# ──────────── 函数注册表 ────────────
# 供 menu_system / button_handler 通过 globals() 查找
__all__ = [name for name in dir() if callable(globals().get(name)) and not name.startswith("_")]