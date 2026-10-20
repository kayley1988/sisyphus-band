#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ESP32 演奏控制模块
==================
通过串口总线（SerialBus）将 CSV 乐谱和舵机配置发送到 ESP32，由 ESP32 独立演奏。
同时支持通过串口对 ESP32 进行 OTA 固件更新。
"""

import time
import os
import csv
import threading
from typing import Optional, Callable, List
from logger import get_logger
from serial_bus import SerialBus

# ── 二进制帧协议 ──
from frame_codec import (
    Frame, make_song_end,
    make_play_control, make_ota_begin, make_ota_chunk,
    make_config, make_chord_def, make_config_save, make_mode_switch,
    make_servo_batch, make_servo_ctrl,
    parse_song_ack, parse_play_event, parse_ota_chunk_ack,
    parse_hw_info_ack, parse_adc_report_ext, make_hw_info,
)
from frame_layer import FrameLayer
from frame_types import FrameType, PlayControl, PlayEvent, EspMode

_log = get_logger("esp32")


class ESP32Player:
    """ESP32 演奏控制器 — V3 二进制帧优先，ASCII 回退"""

    def __init__(self, port: str = "/dev/ttyS3", baud: int = 921600,
                 timeout: float = 1.0, existing_ser=None, error_collector=None,
                 power_monitor=None):
        self._bus = SerialBus.instance()
        if not self._bus.is_open:
            self._bus.open(port, baud, timeout)
        self._playing = False
        self._progress = 0
        self._progress_callback: Optional[Callable[[int], None]] = None
        self._error_collector = error_collector
        self._power_monitor = power_monitor
        self._playback_end_callback: Optional[Callable[[], None]] = None

        # ── V3 二进制帧层 ──
        self._frame_layer: Optional[FrameLayer] = None
        self._registered_handlers: list = []  # ★ 记录已注册的 handler，用于 close 时清理
        if self._bus.is_open:
            try:
                self._frame_layer = FrameLayer(self._bus)
                self._frame_layer.on_frame(FrameType.PLAY_SONG_ACK, self._on_binary_ack)
                self._frame_layer.on_frame(FrameType.PLAY_EVENT, self._on_binary_event)
                self._frame_layer.on_frame(FrameType.PLAY_PROGRESS, self._on_binary_progress)
                self._frame_layer.on_frame(FrameType.OTA_CHUNK_ACK, self._on_ota_ack)
                self._frame_layer.on_frame(FrameType.CTRL_MODE_SWITCH_ACK, self._on_mode_switch_ack)
                self._frame_layer.on_frame(FrameType.CTRL_FIRMWARE_INFO_ACK, self._on_firmware_info_ack)
                self._frame_layer.on_frame(FrameType.CTRL_ERROR, self._on_ctrl_error)
                self._frame_layer.on_frame(FrameType.CTRL_ASCII_PUSH, self._on_ascii_push)
                self._frame_layer.on_frame(FrameType.CTRL_HW_INFO_ACK, self._on_hw_info_ack)
                self._frame_layer.on_frame(FrameType.NORMAL_ADC_REPORT_EXT, self._on_adc_report_ext)
                # ★ 记录所有注册的 (frame_type, callback) 以便 close 时清理
                self._registered_handlers = [
                    (FrameType.PLAY_SONG_ACK, self._on_binary_ack),
                    (FrameType.PLAY_EVENT, self._on_binary_event),
                    (FrameType.PLAY_PROGRESS, self._on_binary_progress),
                    (FrameType.OTA_CHUNK_ACK, self._on_ota_ack),
                    (FrameType.CTRL_MODE_SWITCH_ACK, self._on_mode_switch_ack),
                    (FrameType.CTRL_FIRMWARE_INFO_ACK, self._on_firmware_info_ack),
                    (FrameType.CTRL_ERROR, self._on_ctrl_error),
                    (FrameType.CTRL_ASCII_PUSH, self._on_ascii_push),
                ]
                _log.debug("V3 FrameLayer 已初始化（演奏+OTA 二进制协议就绪）")
            except Exception as e:
                _log.warning("V3 FrameLayer 初始化失败: %s，回退 ASCII", e)

        # ── 二进制帧响应同步 ──
        self._binary_ack_event = threading.Event()
        self._binary_ack_data = {}
        self._binary_event_data = {}

        # ── OTA ACK 同步（独立通道，与 CSV 传输隔离）──
        self._ota_ack_event = threading.Event()
        self._ota_ack_data = {}

        # ── 模式切换同步 ──
        self._mode_switch_event = threading.Event()
        self._mode_switch_data = {}
        self._mode_switch_lock = threading.RLock()     # ★ 防重入: 确保单一切换线程 (RLock 允许归位线程重入 _ensure_normal_mode)
        self._mode_switch_pending = False              # ★ 标记是否有切换进行中

        # ── 播放结束去重 ──
        self._playback_ended = False                   # ★ 防止 DONE/STOPPED 双路径重复处理

        # ── 固件信息查询同步 ──
        self._firmware_info_event = threading.Event()
        self._firmware_info_data = {}

        # ── 半自动 / 全动作 状态（Z 定调：先全抄《拨云》，跑通后再减法）──
        # 拨弦翻转初值 = 1：与 ESP32 固件 chord_engine.cpp 的 strum_flip_state[6]={1,...}
        # 严格一致 —— 复位位是 point_a，首次拨弦必须走 point_b 才有有效行程，
        # 否则第一下是空动作（2026-10-01 修正）。
        self._strum_flip = [1] * 6
        self._last_chord_angles = None      # 最近一次压弦的 12 路角度（strum() 保持和弦用）
        self._position = 0                  # v3 电动换把当前位置（占位）

    # ── V3 二进制帧回调 ──

    def _on_binary_ack(self, frame: Frame):
        try:
            self._binary_ack_data = parse_song_ack(frame.payload)
            self._binary_ack_event.set()       # 单ACK等待 (END)
        except Exception:
            pass

    def _on_ota_ack(self, frame: Frame):
        """OTA_CHUNK_ACK 回调 — 在读线程中同步执行（快速返回，不阻塞）"""
        try:
            self._ota_ack_data = parse_ota_chunk_ack(frame.payload)
            self._ota_ack_event.set()
        except Exception:
            pass

    def _on_binary_event(self, frame: Frame):
        try:
            self._binary_event_data = parse_play_event(frame.payload)
            evt = self._binary_event_data.get('event')
            if evt == PlayEvent.DONE:
                if self._playback_ended:
                    return
                self._playback_ended = True
                self._playing = False
                self._progress = 100
                if self._progress_callback:
                    self._progress_callback(100)
                self._notify_playback_end()
                threading.Thread(target=self._reset_and_switch_to_normal, daemon=True).start()
            elif evt == PlayEvent.STOPPED:
                if self._playback_ended:
                    return
                self._playback_ended = True
                self._playing = False
                if self._progress_callback:
                    self._progress_callback(-1)
                self._notify_playback_end()
                threading.Thread(target=self._reset_and_switch_to_normal, daemon=True).start()
        except Exception:
            pass

    def _on_binary_progress(self, frame: Frame):
        try:
            from frame_codec import parse_play_progress
            data = parse_play_progress(frame.payload)
            pct = data.get('percent', 0)
            self._progress = pct
            if self._progress_callback:
                self._progress_callback(pct)
        except Exception:
            pass

    def _on_mode_switch_ack(self, frame: Frame):
        """CTRL_MODE_SWITCH_ACK 回调 — 在读线程中同步执行"""
        try:
            from frame_codec import parse_mode_switch_ack
            data = parse_mode_switch_ack(frame.payload)
            self._mode_switch_data = data
            self._mode_switch_event.set()
        except Exception:
            pass

    def _on_firmware_info_ack(self, frame: Frame):
        """CTRL_FIRMWARE_INFO_ACK 回调 — 在读线程中同步执行"""
        try:
            from frame_codec import parse_firmware_info_ack
            data = parse_firmware_info_ack(frame.payload)
            self._firmware_info_data = data
            self._firmware_info_event.set()
        except Exception:
            pass

    def _on_ctrl_error(self, frame: Frame):
        """CTRL_ERROR 回调 — 记录 ESP32 发来的错误通知"""
        try:
            from frame_codec import parse_error
            data = parse_error(frame.payload)
            code = data.get('code', -1)
            mode = data.get('mode', '?')
            msg = data.get('message', '')
            if code == 0x01:  # MODE_MISMATCH
                _log.error("ESP32 帧被拒绝: 模式不匹配 (当前模式=%s)", mode)
            else:
                _log.warning("ESP32 错误: code=%d mode=%s msg=%s", code, mode, msg)
        except Exception:
            pass

    def _on_ascii_push(self, frame: Frame):
        """CTRL_ASCII_PUSH 回调 — 解析 ESP32 播放引擎推送的 ASCII 事件。

        ESP32 的 playback_engine 通过 cmd::push_event() 发送事件，经 txQueue
        包装为 CTRL_ASCII_PUSH (0xF6) 帧。载荷格式: '!' + ASCII文本。
        例如: "!PLAYING", "!PROGRESS 50", "!DONE", "!STOPPED"

        此回调在 SerialBus 读线程中同步执行，必须快速返回。
        """
        try:
            text = frame.payload.decode('utf-8', errors='ignore').strip()
            if not text or len(text) < 1:
                return
            # 去掉 '!' 前缀
            if text[0] == '!':
                text = text[1:]

            if text.startswith("PROGRESS"):
                # "PROGRESS 50"
                try:
                    pct = int(text.split()[1])
                    self._progress = pct
                    if self._progress_callback:
                        self._progress_callback(pct)
                except (IndexError, ValueError):
                    pass
            elif text == "DONE":
                if self._playback_ended:
                    return
                self._playback_ended = True
                self._playing = False
                self._progress = 100
                if self._progress_callback:
                    self._progress_callback(100)
                self._notify_playback_end()
                # ★ V4: 归位+模式切换在后台线程中顺序执行
                threading.Thread(target=self._reset_and_switch_to_normal, daemon=True).start()
            elif text == "STOPPED":
                if self._playback_ended:
                    return
                self._playback_ended = True
                self._playing = False
                if self._progress_callback:
                    self._progress_callback(-1)
                self._notify_playback_end()
                threading.Thread(target=self._reset_and_switch_to_normal, daemon=True).start()
            elif text == "PLAYING":
                _log.debug("ESP32 演奏引擎已启动")
            # OVERCURRENT 事件由 SerialBus._overcurrent_cb 单独处理，此处忽略
        except Exception:
            pass

    # ── 模式切换辅助方法 ──

    def _ensure_normal_mode(self):
        """切换 ESP32 到 NORMAL 模式，等待 ACK 确认。

        ★ 持 _mode_switch_lock 串行化：防止与 _ensure_play_mode 等并发时
        互相消费对方的 ACK（单 Event 无请求关联）。RLock 允许
        _reset_and_switch_to_normal 持锁重入。
        """
        with self._mode_switch_lock:
            fl = self._frame_layer
            if fl is None:
                return  # FrameLayer 不可用则跳过（pre-V3 固件不需要模式切换）
            self._mode_switch_event.clear()
            fl.send_frame(make_mode_switch(EspMode.NORMAL))
            if not self._mode_switch_event.wait(timeout=2.0):
                raise RuntimeError("CTRL_MODE_SWITCH (NORMAL) ACK 超时")
            if not self._mode_switch_data.get('ok', False):
                raise RuntimeError(f"NORMAL 模式切换失败: {self._mode_switch_data}")
            _log.debug("ESP32 已切换到 NORMAL 模式")

    def _ensure_play_mode(self):
        """切换 ESP32 到 PLAY 模式，等待 ACK 确认。"""
        with self._mode_switch_lock:
            fl = self._frame_layer
            if fl is None:
                raise RuntimeError("FrameLayer 不可用，无法切换模式")
            self._mode_switch_event.clear()
            fl.send_frame(make_mode_switch(EspMode.PLAY))
            if not self._mode_switch_event.wait(timeout=2.0):
                raise RuntimeError("CTRL_MODE_SWITCH (PLAY) ACK 超时")
            if not self._mode_switch_data.get('ok', False):
                raise RuntimeError(f"PLAY 模式切换失败: {self._mode_switch_data}")
            _log.debug("ESP32 已切换到 PLAY 模式")

    def _ensure_ota_mode(self):
        """切换 ESP32 到 OTA 模式，等待 ACK 确认。"""
        with self._mode_switch_lock:
            fl = self._frame_layer
            if fl is None:
                raise RuntimeError("FrameLayer 不可用，无法切换模式")
            self._mode_switch_event.clear()
            fl.send_frame(make_mode_switch(EspMode.OTA))
            if not self._mode_switch_event.wait(timeout=2.0):
                raise RuntimeError("CTRL_MODE_SWITCH (OTA) ACK 超时")
            if not self._mode_switch_data.get('ok', False):
                raise RuntimeError(f"OTA 模式切换失败: {self._mode_switch_data}")
            _log.debug("ESP32 已切换到 OTA 模式")

    def _reset_and_switch_to_normal(self):
        """V4: 演奏结束后归位舵机 + 切回 NORMAL 模式。
        顺序: ① 切 NORMAL → ② BATCH归位 → ③ 等350ms → ④ DISABLE
        必须在后台线程中执行 (不能阻塞 reader 线程)。"""
        global _stop_reset_pending
        with self._mode_switch_lock:
            if self._playing or self._mode_switch_pending:
                return
            # 手动停止(ESC/过流)已触发 _esp32_reset_servos，跳过二次归位
            if _stop_reset_pending:
                _stop_reset_pending = False
                _log.debug("手动停止已归位，跳过重复归位")
                return
            self._mode_switch_pending = True
            try:
                from servo_controller import ServoController
                from frame_codec import make_servo_batch, make_servo_ctrl

                fl = self._frame_layer
                if fl is None:
                    return

                # ★ 等待 ESP32 完成播放引擎内部清理（释放 CSV 缓冲、停止定时器等）
                # 避免 ESP32 尚在清理时收到模式切换帧而无法响应 ACK
                time.sleep(0.2)

                # ① 切回 NORMAL 模式（BATCH 帧才能被 tick_normal 消费）
                self._ensure_normal_mode()

                # ② 发送归位 BATCH（NORMAL 模式下立即处理）
                config_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "config", "servo_config.csv")
                sc = ServoController(config_file, send_init_commands=False)
                chord_90 = [90] * 12
                strum_point_a = [int(sc._strum_angles[i][0]) for i in range(6)]
                batch = chord_90 + strum_point_a
                fl.send_frame(make_servo_batch(batch))

                # ③ 等待舵机物理移动到目标位置
                time.sleep(0.35)

                # ④ 失能
                fl.send_frame(make_servo_ctrl(0x02))  # DISABLE
                _log.debug("V4 归位完成: 和弦→90°, 拨弦→point_a, 已失能")
            except Exception as e:
                _log.warning("V4 归位/模式切换失败: %s（下次操作会自动重试）", e)
            finally:
                self._mode_switch_pending = False

    def _deferred_switch_to_normal(self):
        """在后台线程中切回 NORMAL 模式（避免 reader 线程死锁）。
        ★ 防重入: 用一把轻量锁确保同一时刻只有一个切换线程运行。"""
        with self._mode_switch_lock:
            # 已经处于切换中，或已被其他线程完成 → 跳过
            if self._playing or self._mode_switch_pending:
                return
            self._mode_switch_pending = True
            try:
                self._ensure_normal_mode()
            except Exception as e:
                _log.warning("延迟切回 NORMAL 模式失败: %s（下次操作会自动重试）", e)
            finally:
                self._mode_switch_pending = False

    # ── v2.8.0: 硬件版本 / 扩展 ADC 回调 ──

    def _on_hw_info_ack(self, frame: Frame):
        """0xFA CTRL_HW_INFO_ACK — 缓存硬件版本/safe mode，并同步换算参数。"""
        try:
            info = parse_hw_info_ack(frame.payload)
        except Exception:
            return
        if not info:
            return
        self._hw_info = info
        pm = self._power_monitor
        if pm is not None:
            try:
                pm.set_board_id(info.get("board_id", 0))
            except Exception as e:
                _log.debug("同步 board_id 到 power_monitor 失败: %s", e)
        if info.get("safe_mode"):
            _log.warning(
                "ESP32 处于 SAFE MODE：硬件版本未确认，舵机输出已物理切断 "
                "(board_id=%s status=%s pin_map_guessed=%s)，请不要下发演奏/舵机命令",
                info.get("board_id"), info.get("status"), info.get("pin_map_guessed"))
        else:
            _log.info("ESP32 硬件版本: board_id=%s name=%s caps=0x%02X adc_ch=%s",
                      info.get("board_id"), info.get("name"),
                      info.get("caps", 0), info.get("adc_ch_count"))

    def _on_adc_report_ext(self, frame: Frame):
        """0x09 NORMAL_ADC_REPORT_EXT — V6.0 电池放电电流（无效时为 None）。"""
        try:
            data = parse_adc_report_ext(frame.payload)
        except Exception:
            return
        self._adc_ext = data.get("adc_bat_current")

    @property
    def hw_info(self):
        """最近一次收到的硬件版本信息（未收到时为 None）。"""
        return getattr(self, "_hw_info", None)

    @property
    def safe_mode(self) -> bool:
        info = getattr(self, "_hw_info", None)
        return bool(info and info.get("safe_mode"))

    @property
    def adc_bat_current(self):
        return getattr(self, "_adc_ext", None)

    def esp32_query_hw_info(self, timeout: float = 2.0):
        """主动查询硬件版本 (0xF9)。固件开机也会主动上报一次。"""
        if self._frame_layer is None:
            return None
        try:
            self._frame_layer.send_frame(make_hw_info())
        except Exception as e:
            _log.warning("查询硬件版本失败: %s", e)
            return None
        deadline = time.time() + timeout
        while time.time() < deadline:
            info = getattr(self, "_hw_info", None)
            if info:
                return info
            time.sleep(0.05)
        return getattr(self, "_hw_info", None)

    def _wait_esp32_ready(self, timeout: float = 2.5) -> bool:
        """等待 ESP32 启动完成再下发命令。

        v2.8.0 固件把 UART1 的初始化后置到硬件版本检测之后（约 0.5~0.7s），
        这段窗口内主机发出的帧会被物理丢弃。以"收到首帧"作为就绪判据；
        超时后主动查询一次固件信息作为兜底探测。
        """
        fl = self._frame_layer
        if fl is None:
            time.sleep(0.8)
            return False

        def _seen() -> bool:
            return getattr(fl, "rx_frames", 0) > 0

        deadline = time.time() + timeout
        while time.time() < deadline:
            if _seen():
                return True
            time.sleep(0.04)
        _log.info("启动窗口内未收到首帧，主动查询固件信息探测 ESP32 ...")
        try:
            self.esp32_get_firmware_info(timeout=1.0)
        except Exception:
            pass
        deadline = time.time() + 1.0
        while time.time() < deadline:
            if _seen():
                return True
            time.sleep(0.04)
        _log.warning("未收到任何 ESP32 帧：可能未连接、未烧录，或固件异常")
        return False

    def connect(self) -> bool:
        if self._bus.is_open:
            self._wait_esp32_ready()
            # ★ v2.8.0: 固件的 0xFA 只在开机主动上报一次。
            #   若主机在固件启动之后才连上（或固件先于主机上电），
            #   就永远收不到那一帧，必须主动补查一次。
            #   不补查的后果: power_monitor 会一直用 V5.1 的增益/NTC/温度通道，
            #   V6.0 上会把 AIN3（电池电流）当成温度读。
            if getattr(self, "_hw_info", None) is None:
                try:
                    self.esp32_query_hw_info(timeout=1.5)
                except Exception as e:
                    _log.debug("补查硬件版本失败: %s", e)
            # 固件版本同样只在被查询时才有缓存；启动时取一次，供 Web/OLED 的"设备信息"展示
            if not (getattr(self, "_firmware_info_data", None) or {}).get("version"):
                try:
                    self.get_firmware_info()
                except Exception as e:
                    _log.debug("查询固件版本失败: %s", e)
        return self._bus.is_open

    def close(self):
        """清理 FrameLayer 回调，防止旧实例的回调持续累积。"""
        self._unregister_frame_handlers()

    def _unregister_frame_handlers(self):
        """从 FrameLayer 单例中移除本实例注册的所有回调。"""
        fl = self._frame_layer
        if fl is None:
            return
        count = len(self._registered_handlers)
        for frame_type, callback in self._registered_handlers:
            try:
                fl.remove_handler(frame_type, callback)
            except Exception:
                pass
        self._registered_handlers.clear()
        _log.debug("FrameLayer handler 已清理 (%d 个回调)", count)

    # ---------- CSV 传输 (V4 快速二进制) ----------
    def send_csv(self, csv_path: str) -> int:
        if not os.path.exists(csv_path):
            raise FileNotFoundError(f"CSV 文件不存在: {csv_path}")

        self._playback_ended = False  # ★ 新一轮演奏开始，重置结束标志
        return self._send_binary_fast(csv_path)

    def _send_binary_fast(self, csv_path: str) -> int:
        """V4 快速传输: Python 预计算角度 → 流水线二进制发送 → 单次 ACK。
        传输 632 音符约 260ms (对比 V3 的 ~9s)。"""
        from servo_controller import ServoController
        from frame_codec import make_song_binary, make_song_end, make_song_meta

        self._playback_ended = False  # ★ 新一轮演奏开始，重置结束标志
        fl = self._frame_layer

        # 1. 加载校准参数 + 预计算所有音符角度
        config_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "config", "servo_config.csv")
        sc = ServoController(config_file)
        note_count, notes, time_signature, bpm = sc.precompute_song(csv_path)
        if note_count == 0:
            raise RuntimeError("CSV 解析出 0 个音符")

        # ★ her 人机配合（Z 2026-10-03 拍板）：机器只压弦，拨弦全部静音到 point_a 静止位。
        #   ESP32 播放引擎不改——拨弦舵机每拍都"回到静止位"= 不拨；她的右手按手机谱面拨。
        if not SONG_STRUM_ENABLED:
            rest = [int(sc._strum_angles[i][0]) for i in range(6)]  # point_a 静止位
            notes = mute_strum_in_notes(notes, rest)
            _log.info("[her] 整曲播放=人机配合：拨弦已静音，%d 音符仅保留压弦换把时间线", note_count)

        _log.info("V4 预计算完成: %d 个音符, %d 字节, 拍号 %s, BPM %d",
                  note_count, note_count * 23, time_signature, bpm)

        # ★ 诊断: 打印前 3 个音符的预计算角度，供对比 ESP32 旧版输出
        import struct as _struct
        for di in range(min(3, len(notes))):
            n = notes[di]
            chord = list(n[:12])
            strum_ang = list(n[12:18])
            wait_ms = _struct.unpack('<I', n[18:22])[0]
            sweep = n[22]
            _log.info("[NOTE %d] chord=%s strum=%s wait=%d sweep=%d",
                      di + 1, chord, strum_ang, wait_ms, sweep)

        # 2. ★ 先在 NORMAL 模式下使能舵机 + 归位（NORMAL 帧必须在 NORMAL 模式发送）
        from frame_codec import make_servo_batch, make_servo_ctrl
        chord_90 = [90] * 12
        strum_point_a = [int(sc._strum_angles[i][0]) for i in range(6)]
        batch = chord_90 + strum_point_a
        fl.send_frame(make_servo_ctrl(0x01))     # ENABLE
        time.sleep(0.05)
        fl.send_frame(make_servo_batch(batch))   # 和弦→90°, 拨弦→point_a
        time.sleep(0.05)

        # 3. 确保 ESP32 处于 PLAY 模式（NORMAL→PLAY, cleanup_normal 为空操作）
        self._ensure_play_mode()

        # 4. ★ 发送歌曲元数据 (拍号 + BPM) — 在音符数据之前
        fl.send_frame(make_song_meta(time_signature, bpm))

        # 5. 流水线发送所有音符帧（无 ACK，无 BEGIN — 二进制路径不需要 CSV 缓冲区）
        BATCH = 10  # 每帧最多 10 音符 (3 + 10×23 = 233B ≤ 247B)
        for i in range(0, len(notes), BATCH):
            batch = notes[i:i+BATCH]
            fl.send_frame(make_song_binary(i // BATCH, batch))
            time.sleep(0.001)  # 1ms 微延迟: 防止 playDataQueue 溢出

        # 6. END + 等待 ACK（唯一一次确认往返）
        self._binary_ack_event.clear()
        fl.send_frame(make_song_end())
        if not self._binary_ack_event.wait(timeout=5.0):
            raise RuntimeError("PLAY_SONG_END ACK 超时")

        actual_count = self._binary_ack_data.get('note_count', 0)
        if actual_count != note_count:
            raise RuntimeError(f"音符数不匹配: 预期{note_count}, 实际{actual_count}")

        _log.info("CSV 传输完成 (V4 二进制): %d 个音符", actual_count)
        return actual_count



    # ---------- 配置传输 ----------
    def send_config(self, servo_controller) -> None:
        sc = servo_controller
        self._send_config_binary(sc)

    def _send_config_binary(self, sc) -> None:
        """使用 NORMAL_CONFIG 二进制帧发送配置"""
        fl = self._frame_layer

        # ★ 确保 ESP32 处于 NORMAL 模式（配置帧属于 NORMAL 域）
        self._ensure_normal_mode()

        # null_offset (12 个值)
        for i, v in enumerate(sc.null_offset):
            fl.send_frame(make_config(0, i, 0, float(v)))
            time.sleep(0.002)  # 2ms 足够 ESP32 normalCmdQueue 消费 (8条目 RingBuffer)

        # press_offsets (6弦×4品)
        for s in range(6):
            for f in range(4):
                fl.send_frame(make_config(1, s, f, float(sc._press_offsets[s][f])))
                time.sleep(0.002)  # 2ms 足够 ESP32 normalCmdQueue 消费 (8条目 RingBuffer)

        # strum_angles (6 弦)
        for i in range(6):
            pa, pb = sc._strum_angles[i]
            fl.send_frame(make_config(2, i, 0, float(pa)))
            time.sleep(0.002)  # 2ms 足够 ESP32 normalCmdQueue 消费 (8条目 RingBuffer)
            fl.send_frame(make_config(2, i, 1, float(pb)))
            time.sleep(0.002)  # 2ms 足够 ESP32 normalCmdQueue 消费 (8条目 RingBuffer)

        # ★ 触发 ESP32 NVS 持久化 (V3 二进制协议无 ASCII CONFIG_SAVE)
        fl.send_frame(make_config_save())
        time.sleep(0.05)  # 等待 ESP32 写入 NVS Flash

        # ★ 不在传输期间动舵机，归位由 playback::start() 统一处理
        _log.info("舵机配置发送完成 (V3 二进制) + NVS 已保存")


    def send_chord_defs(self, chord_csv_path: str) -> None:
        if not os.path.exists(chord_csv_path):
            _log.warning("和弦定义文件不存在: %s", chord_csv_path)
            return
        self._send_chord_defs_binary(chord_csv_path)

    def _send_chord_defs_binary(self, chord_csv_path: str) -> None:
        fl = self._frame_layer

        # ★ 确保 ESP32 处于 NORMAL 模式（和弦定义帧属于 NORMAL 域）
        self._ensure_normal_mode()
        with open(chord_csv_path, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            next(reader, None)
            for row in reader:
                if len(row) < 7:
                    continue
                name = row[0].strip()
                if not name:
                    continue
                frets = []
                for j in range(1, 7):
                    val = row[j].strip() if j < len(row) else "0"
                    # 容错: CSV 可能出现 '0.0' 浮点格式
                    try:
                        frets.append(int(float(val)) if val else 0)
                    except ValueError:
                        frets.append(0)
                fl.send_frame(make_chord_def(name, frets))
                time.sleep(0.002)  # 2ms 足够 ESP32 normalCmdQueue 消费 (8条目 RingBuffer)
        _log.info("和弦定义发送完成 (V3)")


    # ---------- 播放控制 ----------
    def play(self) -> bool:
        try:
            self._frame_layer.send_frame(make_play_control(PlayControl.START))
            self._playing = True
            self._progress = 0
            threading.Thread(target=self._monitor_progress_binary, daemon=True).start()
            return True
        except Exception as e:
            _log.error("PLAY 失败: %s", e)
            return False

    def stop(self):
        self._playing = False
        try:
            self._frame_layer.send_frame(make_play_control(PlayControl.STOP))
        except Exception as e:
            _log.error("STOP 失败: %s", e)
        self._notify_playback_end()

    # ---------- 半自动演奏接口（她的触摸条触发）----------
    # ★ 改良点: 原《拨云》只有"自动演奏"(play/play_song) 路径。我们新增
    #   hold_chord / release_chord 两个底层原语，供 PerformanceController
    #   在"她的手"触发源下实时压弦/松弦。拨弦舵机不在此路径——右手归她。
    #   这两个方法只发 NORMAL_SERVO_BATCH 帧，不启动 playback 状态机。

    def _ensure_sc(self, servo_controller=None):
        """返回用于计算角度的 ServoController（懒加载，复用传入实例）。"""
        if servo_controller is not None:
            return servo_controller
        if getattr(self, '_sc_cache', None) is not None:
            return self._sc_cache
        from servo_controller import ServoController
        cfg = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "..", "config", "servo_config.csv")
        sc = ServoController(cfg, send_init_commands=False)
        self._sc_cache = sc
        return sc

    def hold_chord(self, frets: List[int], servo_controller=None,
                   with_strum: bool = False) -> bool:
        """压住一个和弦（她的手触发）。frets 为 6 弦品位表。

        行为: 计算 12 路和弦舵机角度 → 与 6 路拨弦角度拼成 18 路
        NORMAL_SERVO_BATCH 帧 → 发给 ESP32。

        with_strum:
          False（她弹模式，硬规则②）—— 拨弦通道保持 point_a idle，机器只压弦，
                她右手拨。
          True （全抄《拨云》动作）—— 拨弦通道按 A/B 交替给一次拨弦行程，
                机器压弦并拨一下（调试 / 彩蛋 / "先全后减"的全动作阶段）。
        """
        if self._frame_layer is None:
            _log.error("hold_chord: FrameLayer 未就绪")
            return False
        if len(frets) != 6:
            _log.error("hold_chord: frets 长度应为 6，收到 %d", len(frets))
            return False
        try:
            from servo_controller import STRUM_ANGLE_MIN, STRUM_ANGLE_MAX
            sc = self._ensure_sc(servo_controller)
            chord_angles = sc._compute_chord_angles_only(frets)  # 12 路
            batch = [0] * 18
            for i in range(12):
                batch[i] = chord_angles[i]
            if with_strum:
                # 全动作：6 弦一起拨一下（A/B 交替，含限位钳位）
                stroked = sc._compute_strum_angles_only([True] * 6, self._strum_flip)
                for i in range(6):
                    batch[12 + i] = stroked[i]
            else:
                # 她弹模式：拨弦通道恒为 point_a idle（safe 占位，机器不拨）
                for i in range(6):
                    a = int(round(max(STRUM_ANGLE_MIN, min(STRUM_ANGLE_MAX,
                                                           sc._strum_angles[i][0]))))
                    batch[12 + i] = a
            self._last_chord_angles = list(chord_angles)   # 供 strum() 保持和弦
            self._ensure_normal_mode()
            self._frame_layer.send_frame(make_servo_batch(batch))
            return True
        except Exception as e:
            _log.error("hold_chord 失败: %s", e)
            return False

    def strum(self, servo_controller=None, strings: Optional[List[int]] = None) -> bool:
        """单独拨一下（保持当前压弦状态，只动 6 路拨弦舵机）。

        全抄《拨云》动作集的一部分：用于 STRUM 事件 / 单音模式彩蛋。
        strings: 要拨的弦索引列表（0..5），None = 全 6 弦。
        未压过弦时按"开放弦"角度作为背景（避免空帧）。
        """
        if self._frame_layer is None:
            _log.error("strum: FrameLayer 未就绪")
            return False
        try:
            sc = self._ensure_sc(servo_controller)
            sel = [False] * 6
            if strings is None:
                sel = [True] * 6
            else:
                for s in strings:
                    if 0 <= s < 6:
                        sel[s] = True
            stroked = sc._compute_strum_angles_only(sel, self._strum_flip)
            chord12 = self._last_chord_angles
            if chord12 is None:
                chord12 = sc._compute_chord_angles_only([0, 0, 0, 0, 0, 0])
            batch = list(chord12) + list(stroked)
            self._ensure_normal_mode()
            self._frame_layer.send_frame(make_servo_batch(batch))
            return True
        except Exception as e:
            _log.error("strum 失败: %s", e)
            return False

    def set_position(self, position: int, servo_controller=None) -> bool:
        """v3 电动换把（步进+同步带滑轨）—— 机械件未到，当前仅记录，不产生动作。

        位置查表 d(n)=L(1−2^(−n/12))，精度 ±0.5mm。二期接入时在这里发换把帧。
        """
        self._position = int(position)
        _log.info("[position] 记录换把位置=%s（v3 电动换把为二期，本次无动作）",
                  self._position)
        return False

    def get_position(self) -> int:
        return self._position

    def release_chord(self, servo_controller=None) -> bool:
        """松开当前和弦（她的手离开触发区）。

        行为: 计算和弦舵机"开放弦/未压"位置（frets 全 0 → 角度 = 90 + null_offset），
        发 NORMAL_SERVO_BATCH 帧让压弦舵机回到零位。符合硬规则①：
        她没有动作时，一个音都不响。
        """
        if self._frame_layer is None:
            _log.error("release_chord: FrameLayer 未就绪")
            return False
        try:
            from servo_controller import STRUM_ANGLE_MIN, STRUM_ANGLE_MAX
            sc = self._ensure_sc(servo_controller)
            # frets 全 0 → 开放弦位置 = 90 + null_offset（即松弦零位）
            released = sc._compute_chord_angles_only([0, 0, 0, 0, 0, 0])
            batch = [0] * 18
            for i in range(12):
                batch[i] = released[i]
            for i in range(6):
                a = int(round(max(STRUM_ANGLE_MIN, min(STRUM_ANGLE_MAX,
                                                       sc._strum_angles[i][0]))))
                batch[12 + i] = a
            self._ensure_normal_mode()
            self._frame_layer.send_frame(make_servo_batch(batch))
            return True
        except Exception as e:
            _log.error("release_chord 失败: %s", e)
            return False

    def _monitor_progress_binary(self):
        """进度监控 — 看门狗线程。

        进度事件 (PROGRESS/DONE/STOPPED/PLAYING) 由 ESP32 tx_task 通过
        CTRL_ASCII_PUSH (0xF6) 二进制帧推送，Python FrameLayer 解码后
        分发到 _on_ascii_push 回调更新 self._progress。

        本线程作为独立看门狗：
        - 监测 self._progress 的变化（由 _on_ascii_push 更新）
        - 若 30 秒内无任何进度变化，判定 ESP32 通信异常，强制结束播放
        """
        last_progress = self._progress
        last_change_time = time.time()
        while self._playing:
            # ★ 本实例已被新播放替换（init_player 重建实例），看门狗立即退役，
            # 避免旧实例 30s 超时后误杀新演奏的模块级播放状态
            if _player is not self:
                _log.debug("看门狗退役: 旧播放实例已被新播放替换")
                break
            if self._bus.is_paused:
                time.sleep(0.1)
                continue

            if self._progress != last_progress:
                last_progress = self._progress
                last_change_time = time.time()

            # ── 看门狗超时：30 秒无进度变化 → ESP32 通信异常 ──
            if time.time() - last_change_time > 30:
                _log.warning("30s 未收到进度更新，自动结束播放")
                self._playing = False
                if self._progress_callback:
                    self._progress_callback(-1)
                self._notify_playback_end()
                break

            time.sleep(0.2)


    def _notify_playback_end(self):
        if self._power_monitor:
            try:
                # ★ 幂等性: 仅在暂停状态时才恢复，避免重复日志
                if self._power_monitor.is_paused:
                    self._power_monitor.resume()
                    _log.info("PowerMonitor ADC 读取已恢复")
            except Exception as e:
                _log.error("恢复 PowerMonitor 失败: %s", e)
        if self._playback_end_callback:
            try:
                self._playback_end_callback()
            except Exception as e:
                _log.error("播放结束回调执行失败: %s", e)

    @property
    def progress(self) -> int:
        return self._progress

    @property
    def is_playing(self) -> bool:
        return self._playing

    def set_progress_callback(self, cb: Optional[Callable[[int], None]]):
        self._progress_callback = cb

    def set_playback_end_callback(self, cb: Optional[Callable[[], None]]):
        self._playback_end_callback = cb

    # ---------- OTA 固件更新 ----------
    def get_firmware_info(self) -> dict:
        """查询 ESP32 固件版本"""
        from frame_codec import make_firmware_info
        self._firmware_info_event.clear()
        self._firmware_info_data = {}
        self._frame_layer.send_frame(make_firmware_info())
        if self._firmware_info_event.wait(timeout=2.0):
            data = self._firmware_info_data
            if data.get('version'):
                version = data['version']
                build_date = f"{data.get('build_date', '')} {data.get('build_time', '')}".strip()
                return {
                    'version': version,
                    'build_date': build_date,
                    'raw': f"FIRMWARE {version} {build_date}",
                }
        raise RuntimeError("固件信息查询超时")

    def ota_flash(self, firmware_path: str,
                  progress_callback: Optional[Callable[[int, str], None]] = None) -> bool:
        if not os.path.exists(firmware_path):
            raise FileNotFoundError(f"固件文件不存在: {firmware_path}")

        total_size = os.path.getsize(firmware_path)
        if total_size == 0:
            raise ValueError("固件文件为空")

        self._bus.pause()

        def _report(percent: int, status: str):
            if progress_callback:
                progress_callback(percent, status)

        return self._ota_flash_binary(firmware_path, total_size, _report)

    def _ota_flash_binary(self, firmware_path: str, total_size: int,
                          _report) -> bool:
        """V3 二进制 OTA — 每块等待 ACK 确认，确保可靠传输"""
        fl = self._frame_layer
        chunk_size = 240  # 对齐 Flash 4KB 页

        # ★ 确保 ESP32 处于 OTA 模式，否则 OTA 帧会被静默拒绝
        self._ensure_ota_mode()

        _report(0, f"准备刷写 ({total_size} 字节) V3...")

        fl.send_frame(make_ota_begin(total_size, chunk_size))
        _report(2, f"ESP32 就绪, chunk={chunk_size}B (V3 二进制)")

        with open(firmware_path, 'rb') as f:
            seq = 0
            sent_bytes = 0
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break

                self._ota_ack_event.clear()
                fl.send_frame(make_ota_chunk(seq, chunk))

                # 等待 ESP32 确认 (Flash 写入可能需要数毫秒)
                if not self._ota_ack_event.wait(timeout=5.0):
                    raise RuntimeError(
                        f"OTA chunk {seq} ACK 超时 — UART 丢帧或 ESP32 异常"
                    )

                status = self._ota_ack_data.get('status', 99)
                if status != 0:
                    raise RuntimeError(
                        f"OTA chunk {seq} 被 ESP32 拒绝: status={status}"
                    )

                sent_bytes += len(chunk)
                percent = 2 + int((sent_bytes / total_size) * 88)
                _report(percent, f"传输中... {sent_bytes}/{total_size}")
                seq += 1

        _report(92, "等待 ESP32 校验重启...")
        time.sleep(3.0)  # 等待 OTA_COMPLETE + 重启

        _report(100, "刷写完成! ESP32 正在重启")
        _log.info("ESP32 OTA 完成 (V3 二进制, %d 块已验证)", seq)
        return True


    def emergency_stop_servos(self) -> bool:
        try:
            self._bus.raw_write(b"ALL 0\n")
            time.sleep(0.05)
            self._bus.raw_write(b"ALL 0\n")
            time.sleep(0.35)  # 等舵机物理到位
            # 失能所有舵机（二进制帧优先，ASCII 回退）
            if self._frame_layer is not None:
                from frame_codec import make_servo_ctrl
                self._frame_layer.send_frame(make_servo_ctrl(0x02))  # DISABLE
            _log.info("紧急停止: 所有舵机已归零并失能")
            if self._error_collector:
                from error_codes import ErrorCode
                self._error_collector.report(ErrorCode.SERVO_OVERCURRENT_CHORD, detail="紧急停止已执行")
            return True
        except Exception as e:
            _log.error("紧急停止失败: %s", e)
            return False


# ================= 模块级便捷接口 =================
_lock = threading.Lock()
_player: Optional[ESP32Player] = None
_progress = 0
_playing = False
_stop_reset_pending = False  # 手动停止标记，防止 STOPPED 事件二次归位

# ★ her 人机配合模式：整曲播放是否让机器拨弦（True=full 全盘 / False=her 只压弦）。
#   整曲播放走 V4 二进制直发 ESP32，**绕过** PerformanceController 的动作集过滤，
#   所以需要独立开关。必须放模块级——init_player() 每次演奏都会新建 ESP32Player
#   实例，挂实例属性会被重置。由 trigger_integration.HerPerformance.set_mode() 同步。
SONG_STRUM_ENABLED = True


def mute_strum_in_notes(notes, rest_angles):
    """her 模式整曲播放：6 个拨弦舵机角度固定在静止位 point_a、扫弦方向清零。

    note 23 字节布局: chord[12] + strum[6] + wait_ms[4 LE] + sweep_dir[1]。
    只动 strum/sweep，压弦换把时间线原样保留 —— 她的右手按手机谱面拨。
    """
    rest = bytes(int(a) for a in rest_angles)
    return [n[:12] + rest + n[18:22] + b'\x00' for n in notes]


def init_player(port: str = "/dev/ttyS3", baud: int = 921600, shared_ser=None,
                power_monitor=None) -> ESP32Player:
    global _player, _playing
    # ★ 清理旧实例的 FrameLayer 回调，防止 handler 累积
    if _player is not None:
        try:
            _player.close()
        except Exception:
            pass
    _player = ESP32Player(port=port, baud=baud, existing_ser=shared_ser,
                          power_monitor=power_monitor)
    _player.connect()

    def _on_progress(p):
        global _progress, _playing
        with _lock:       # ★ 锁保护: _progress 和 _playing 的更新必须原子化
            _progress = p
            if p < 0:
                _playing = False
            elif p >= 100:
                _playing = False

    _player.set_progress_callback(_on_progress)
    return _player


def get_player() -> Optional[ESP32Player]:
    return _player


def _find_csv_path(song_dir: str, source_dir: str = None) -> Optional[str]:
    if source_dir:
        target = os.path.join(song_dir, source_dir)
    else:
        target = os.path.join(song_dir, "图片源1")
    corrected = os.path.join(target, "输出结果（deepseek纠正版本）.csv")
    if os.path.exists(corrected):
        return corrected
    fallback = os.path.join(target, "输出结果.csv")
    if os.path.exists(fallback):
        _log.warning("未找到 AI 纠正版本 CSV，使用普通输出结果")
        return fallback
    return None


def esp32_play_song(song_name: str, servo_controller=None, shared_ser=None,
                    source_dir: str = None, power_monitor=None) -> bool:
    global _player, _progress, _playing

    # ★ OTA 进行中（串口总线已暂停）拒绝演奏：ESP32 处于 OTA 模式，
    # 模式切换帧会被拒绝，还会干扰 OTA 分块传输
    if SerialBus.instance().is_paused:
        _log.warning("串口总线已暂停（OTA 进行中），拒绝演奏请求: %s", song_name)
        return False

    if power_monitor:
        power_monitor.pause()
        _log.info("PowerMonitor ADC 读取已暂停（演奏期间）")

    # ★ 始终通过 init_player 创建/刷新 _player，确保 _progress_callback 已设置
    _player = init_player(shared_ser=shared_ser, power_monitor=power_monitor)

    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    song_dir = os.path.join(project_root, "歌曲列表", song_name)
    if not os.path.isdir(song_dir):
        song_dir = os.path.join(project_root, "data", "songs", song_name)
    csv_path = _find_csv_path(song_dir, source_dir)
    if csv_path is None:
        _log.error("乐谱文件不存在: %s/图片源1/", song_dir)
        if power_monitor:
            power_monitor.resume()
        return False

    if servo_controller is not None:
        _player.send_config(servo_controller)

    try:
        note_count = _player.send_csv(csv_path)
        _log.info("已加载 %d 个音符", note_count)
    except Exception as e:
        _log.error("CSV 传输失败: %s", e)
        if power_monitor:
            power_monitor.resume()
        return False

    _progress = 0
    _playing = True
    try:
        play_ok = _player.play()
    except Exception as e:
        _log.error("PLAY 异常: %s", e)
        _playing = False
        if power_monitor:
            power_monitor.resume()
        return False

    if not play_ok:
        _playing = False
        if power_monitor:
            power_monitor.resume()
        return False

    return True


def esp32_stop_song():
    global _playing, _stop_reset_pending
    _playing = False
    _stop_reset_pending = True  # 标记手动停止，防止 STOPPED 事件触发二次归位
    if _player:
        _player.stop()
        # 归位舵机并失能（后台线程，避免阻塞调用方）
        threading.Thread(target=_esp32_reset_servos, daemon=True).start()


def _esp32_reset_servos():
    """演奏中断后归位舵机：拨弦→point_a，和弦→90°，然后失能。
    必须在 _player.stop() 之后调用。"""
    global _stop_reset_pending
    try:
        time.sleep(0.5)  # 等 ESP32 处理完 STOP
        # ★ 先切换到 NORMAL 模式，再发 NORMAL 帧（否则 rxFrameTask 会拒绝）
        if _player:
            _player._ensure_normal_mode()
        from servo_controller import ServoController
        config_file = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "..", "..", "config", "servo_config.csv")
        sc = ServoController(config_file, send_init_commands=False)
        from frame_codec import make_servo_batch, make_servo_ctrl
        fl = _player._frame_layer if _player else None
        if fl is None:
            return
        chord_90 = [90] * 12
        strum_point_a = [int(sc._strum_angles[i][0]) for i in range(6)]
        batch = chord_90 + strum_point_a
        fl.send_frame(make_servo_batch(batch))
        time.sleep(0.35)  # 等舵机物理到位
        fl.send_frame(make_servo_ctrl(0x02))  # DISABLE
        _log.info("ESC 中断后舵机归位完成: 和弦→90°, 拨弦→point_a, 已失能")
    except Exception as e:
        _log.warning("ESC 中断后舵机归位失败: %s", e)
    finally:
        _stop_reset_pending = False


def esp32_home_servos(hold: bool = True, chord_angle=None, save: bool = False):
    """一键归位：所有舵机回到收缩/待机位（和弦→归位角，拨弦→point_a）。

    装机场景：把装置套到琴弦上时，让齿条全部收回、不顶着琴弦。
    与固件 chord::reset_position() 目标一致（同一组 home 值），
    但和弦归位角可由配置/home_chord_angle 决定（默认 90°，可现场微调）。

    hold=True  归位后保持使能（有保持力矩，装机时齿条不会自己跑位）——默认
    hold=False 归位后立即失能（省电、可手动推动）
    chord_angle 指定和弦归位角（None=读配置文件里的 home_chord_angle）
    save        是否把 chord_angle 写回配置文件（长期生效）

    Returns:
        (ok: bool, msg: str)
    """
    if _player is None:
        return False, "ESP32 未连接"
    if _playing:
        return False, "正在演奏，请先停止"
    try:
        from frame_codec import make_servo_batch, make_servo_ctrl
        from servo_controller import load_home_angles, save_home_chord_angle

        fl = _player._frame_layer
        if fl is None:
            return False, "通信层未就绪"

        # BATCH 帧只在 NORMAL 模式下会被消费
        _player._ensure_normal_mode()

        cfg = load_home_angles()
        angle = float(cfg["chord"]) if chord_angle is None else float(chord_angle)
        angle = max(0.0, min(180.0, angle))
        if cfg["strum_override"] is not None:
            strum_point_a = [int(cfg["strum_override"])] * 6
        else:
            strum_point_a = [int(a) for a in cfg["strum"]]

        if save and chord_angle is not None:
            save_home_chord_angle(angle)

        if hold:
            fl.send_frame(make_servo_ctrl(0x01))          # ENABLE
            time.sleep(0.05)
        fl.send_frame(make_servo_batch([int(round(angle))] * 12 + strum_point_a))
        time.sleep(0.35)                                   # 等舵机物理到位
        if not hold:
            fl.send_frame(make_servo_ctrl(0x02))          # DISABLE
        _log.info("一键归位完成: 和弦→%.0f°, 拨弦→point_a, %s",
                  angle, "保持使能" if hold else "已失能")
        return True, ("已归位(和弦%.0f°)，保持力矩" % angle if hold
                      else "已归位并释放力矩")
    except Exception as e:
        _log.warning("一键归位失败: %s", e)
        return False, "归位失败: %s" % e


def esp32_servo_jog(chord_angle, hold: bool = True, ensure_mode: bool = True):
    """把 12 路和弦舵机实时挪到指定角度（拨弦保持收缩位）。

    供装机角度微调屏幕 / Web 滑块用——每按一次键、每拖一下滑块都会调用，
    因此走 load_home_angles() 轻量读配置，不 new ServoController，
    也不做写盘（写盘由 esp32_home_servos(save=True) 负责）。

    ensure_mode=False 用于连续微调：模式切换要等 ACK（最多 2s），
    只在进入微调界面时切一次即可。

    Returns:
        (ok: bool, msg: str)
    """
    if _player is None:
        return False, "ESP32 未连接"
    if _playing:
        return False, "正在演奏，请先停止"
    try:
        from frame_codec import make_servo_batch, make_servo_ctrl
        from servo_controller import load_home_angles

        fl = _player._frame_layer
        if fl is None:
            return False, "通信层未就绪"

        if ensure_mode:
            _player._ensure_normal_mode()

        cfg = load_home_angles()
        angle = max(0.0, min(180.0, float(chord_angle)))
        if cfg["strum_override"] is not None:
            strum = [int(cfg["strum_override"])] * 6
        else:
            strum = [int(a) for a in cfg["strum"]]

        if hold:
            fl.send_frame(make_servo_ctrl(0x01))          # ENABLE（幂等）
        fl.send_frame(make_servo_batch([int(round(angle))] * 12 + strum))
        return True, "和弦→%.0f°" % angle
    except Exception as e:
        _log.warning("舵机微调失败: %s", e)
        return False, "微调失败: %s" % e


def esp32_servo_release():
    """释放力矩：让舵机失能（松开保持力矩），可用手推动齿轮微调。

    Returns:
        (ok: bool, msg: str)
    """
    if _player is None:
        return False, "ESP32 未连接"
    if _playing:
        return False, "正在演奏，请先停止"
    try:
        from frame_codec import make_servo_ctrl

        fl = _player._frame_layer
        if fl is None:
            return False, "通信层未就绪"

        _player._ensure_normal_mode()
        fl.send_frame(make_servo_ctrl(0x02))              # DISABLE
        _log.info("舵机已释放力矩（全部失能，可手动推动）")
        return True, "已释放力矩，可手推齿轮"
    except Exception as e:
        _log.warning("释放力矩失败: %s", e)
        return False, "释放失败: %s" % e


def esp32_is_playing() -> bool:
    with _lock:
        return _playing


def esp32_get_progress() -> int:
    with _lock:
        return _progress


def esp32_ota_flash(firmware_path: str, shared_ser=None,
                    progress_callback=None) -> bool:
    global _player
    if _player is None:
        _player = ESP32Player(existing_ser=shared_ser)
        _player.connect()
    return _player.ota_flash(firmware_path, progress_callback)


def esp32_send_servo_config(shared_ser=None, servo_controller=None) -> bool:
    """同步舵机校准参数到 ESP32。若提供 servo_controller 则直接复用，否则新建实例。"""
    global _player
    if _player is None:
        _player = ESP32Player(existing_ser=shared_ser)
        _player.connect()
    try:
        if servo_controller is not None:
            sc = servo_controller
        else:
            from servo_controller import ServoController
            config_file = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "..", "..", "config", "servo_config.csv"
            )
            # send_init_commands=False: 不重复发 centering 命令，避免 CONFIG_SAVE
            # 的 disable_all() 之后又被 centering 命令唤醒 PCA9685
            sc = ServoController(config_file, existing_ser=shared_ser,
                                 send_init_commands=False)
        _player.send_config(sc)
        _log.info("舵机校准参数已同步到 ESP32 并持久化到 NVS")
        return True
    except Exception as e:
        _log.warning("舵机校准参数同步失败: %s", e)
        return False


def esp32_get_firmware_info(shared_ser=None) -> dict:
    global _player
    if _player is None:
        _player = ESP32Player(existing_ser=shared_ser)
        _player.connect()
    return _player.get_firmware_info()
