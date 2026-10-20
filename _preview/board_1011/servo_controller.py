#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
舵机控制模块 — 串口控制 ESP32 上的 PCA9685 + LEDC PWM

负责和弦按压舵机 (12个) 与拨弦舵机 (6个) 的校准与控制。
配置参数从 servo_config.csv 文件读取，支持运行时校准并持久化。

核心功能:
  1. **和弦舵机校准** (calibrate_all_chord_servos):
     基于电流检测的自动校准:
     - 快进→慢退找电流临界点算法
     - 去极值滤波 + 长时间稳定验证
     - 自动计算零点偏移和按压距离

  2. **拨弦舵机校准** (calibrate_all_strum_servos):
     基于吉他音频检测的自动校准:
     - FastPluckDetector 实时检测拨弦状态
     - 快速触发即记录，无回退
     - 两点差距 <10° 自动重试

  3. **实时控制** (set_chord / set_strum):
     - 品位→12个和弦舵机角度转换 (含弦4/5/6机械反转)
     - 拨弦交替翻转 (模拟上下拨弦)

硬件连接 (ESP32 侧):
  - LEDC PWM ch0=GPIO40, ch1=GPIO21 (和弦舵机 PWM0/PWM1)
  - PCA9685 16通道 (和弦 PCA1~PCA16, 拨弦 PCA3~PCA8)
  - RK3566 → /dev/ttyS3 @ 921600 → ESP32 UART1

同步维护:
  - _FRET_OFFSETS 须与 ESP32 chord_engine.cpp 中 FRET_OFFSETS 保持一致
  - 默认校准值须与 ESP32 config/params.h 中 DEFAULT_* 保持一致
"""

import time
import csv
import os
import sys
import threading
from typing import List, Tuple, Optional, Callable

import numpy as np
from logger import get_logger
from serial_bus import SerialBus

# ── V3 二进制帧协议 ──
try:
    from frame_codec import (
        make_servo_batch, make_servo_single, make_config,
        Frame,
    )
    from frame_layer import FrameLayer
    from frame_types import FrameType, ConfigType, ServoChType
    _V3_AVAILABLE = True
except ImportError:
    _V3_AVAILABLE = False

_log = get_logger("servo")

# ---------- 直接调用 guitar_tuner 中的快速拨弦检测器 ----------
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from guitar_tuner import FastPluckDetector, calibrate_noise, SAMPLE_RATE, CHUNK_FAST, FORMAT, CHANNELS

# 拨弦舵机安全角度范围（防止机械过冲损坏）
# 与 ESP32 端 servo_driver.cpp set_strum_by_idx() 保持一致
STRUM_ANGLE_MIN = 55.0
STRUM_ANGLE_MAX = 125.0

# 校准期间文件锁：和弦/拨弦校准进行中时，_load_config 跳过 CSV 读取
_calibration_in_progress = False

# ═══════════════════════════════════════════════════════════════
# 整轮校准结果报告（模块级 → OLED/Web/CLI 三种触发方式共享，
# 与跑在哪个 ServoController 实例无关）
#   chord: {ts, kind, stopped, total, ok:[1-based], failed:[], unstable:[]}
#   strum: {ts, kind, stopped, total, ok:[], failed:[],
#           prev:{弦:[lo,hi]}, curr:{弦:[lo,hi]}}   # 幅度一致性 = 纯数据，不判定
# ═══════════════════════════════════════════════════════════════
_LAST_CALIB_REPORT = {"chord": None, "strum": None}
_REPORT_LOCK = threading.Lock()

# 单舵机校准预览结果（模块级，跨实例共享；由 Web apply/discard 消费）
# 键: (kind, idx) -> 预览 dict（status=ok|failed|stopped）
_PENDING_CALIB = {}
_PENDING_LOCK = threading.Lock()


def calib_pending_get(kind: str, idx: int):
    with _PENDING_LOCK:
        return _PENDING_CALIB.get((kind, idx))


def calib_pending_all() -> dict:
    with _PENDING_LOCK:
        return dict(_PENDING_CALIB)


def calib_pending_set(kind: str, idx: int, pending: dict):
    with _PENDING_LOCK:
        _PENDING_CALIB[(kind, idx)] = pending


def calib_pending_clear(kind: str = None, idx: int = None):
    """清除全部(kind=None,idx=None) / 某类型全部(kind指定) / 单个(kind,idx)。"""
    with _PENDING_LOCK:
        if kind is None and idx is None:
            _PENDING_CALIB.clear()
        elif idx is None:
            for k in [k for k in _PENDING_CALIB if k[0] == kind]:
                del _PENDING_CALIB[k]
        else:
            _PENDING_CALIB.pop((kind, idx), None)


def set_last_calib_report(kind: str, report: dict):
    """记录整轮校准报告；同时作废同类型的单舵机预览结果，
    防止旧的单舵机结果在整轮校准之后被 apply，覆盖新校准值。"""
    if kind not in _LAST_CALIB_REPORT:
        return
    report = dict(report)
    report.setdefault("ts", time.time())
    with _REPORT_LOCK:
        _LAST_CALIB_REPORT[kind] = report
    calib_pending_clear(kind)
    _log.info("校准报告[%s] 已记录: 成功=%s 失败=%s",
              kind, report.get("ok") or [], report.get("failed") or [])


def calib_report_short_text(kind: str) -> str:
    """OLED 精简报告文案（≤14 字符，渲染层还会截断；缺失时保持原文案）。"""
    rep = _LAST_CALIB_REPORT.get(kind)
    if not rep:
        return "校准完成 ✓"
    stopped = bool(rep.get("stopped"))
    prefix = "已停止 " if stopped else ""
    failed = rep.get("failed") or []
    ok_list = rep.get("ok") or []
    if kind == "chord":
        unstable = rep.get("unstable") or []
        if not failed and not unstable:
            return prefix + "✓ 全部成功"
        if failed:
            return prefix + "失败: " + ",".join(str(x) for x in failed[:6])
        return prefix + "不稳: " + ",".join(str(x) for x in unstable[:6])
    # strum
    if stopped and not failed and not ok_list:
        return "已停止"
    if not failed:
        return prefix + "✓ 全部成功"
    return prefix + "失败: " + ",".join(str(x) for x in failed[:6])


# ═══════════════════════════════════════════════════════════════
# 装机归位角度（可调 · 持久化）
#   2026-10-11 新增：把装置套上琴弦之前，齿条必须收得足够平。
#   和弦舵机的收缩位默认 90°，但每台机器装配/齿条相位略有差异——
#   允许现场微调（微调屏幕 / Web 滑块），存回 servo_config.csv 长期生效。
# ═══════════════════════════════════════════════════════════════
DEFAULT_HOME_CHORD_ANGLE = 90.0


def _default_config_path() -> str:
    return os.path.abspath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..", "config", "servo_config.csv"))


def load_home_angles(config_file: str = None) -> dict:
    """轻量读取装机归位角度 —— **不实例化 ServoController、不碰串口/电源监控**。

    微调屏幕每次按键都要重新下发角度，若每次都 new 一个 ServoController
    会连带创建 PowerMonitor / 重开串口，代价太大；这里只解析 CSV 两个数。

    Returns:
        {
          "chord": float,               # 和弦舵机归位角（默认 90）
          "strum": [float] * 6,         # 每根弦 point_a（拨弦收缩位）
          "strum_override": float|None, # 配了 home_strum_angle 则用它覆盖 6 路
        }
    """
    if config_file is None:
        config_file = _default_config_path()
    chord = DEFAULT_HOME_CHORD_ANGLE
    # 与 _load_config 的 default_strum_angles 第一列（point_a）保持一致
    strum = [70.0, 60.0, 55.0, 77.0, 30.0, 75.0]
    strum_override = None
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            for row in csv.reader(f):
                if not row:
                    continue
                k = row[0].strip()
                if k == "home_chord_angle" and len(row) >= 2:
                    try:
                        chord = float(row[1])
                    except ValueError:
                        pass
                elif k == "home_strum_angle" and len(row) >= 2:
                    try:
                        strum_override = float(row[1])
                    except ValueError:
                        pass
                elif k.startswith("strum_angles_") and len(row) >= 3:
                    try:
                        idx = int(k.split("_")[2]) - 1
                        if 0 <= idx < 6:
                            strum[idx] = float(row[1])
                    except (IndexError, ValueError):
                        pass
    except FileNotFoundError:
        _log.warning("配置文件不存在，装机角度取默认 %s°", chord)
    except Exception as e:
        _log.warning("读取装机归位角度失败(%s)，使用默认值", e)
    return {"chord": chord, "strum": strum, "strum_override": strum_override}


def save_home_chord_angle(angle: float, config_file: str = None) -> bool:
    """把和弦装机归位角写回 servo_config.csv（保留其它行不动）。

    刻意不走 ServoController._save_config —— 那个会整表重写，
    若控制器内存里的值不是最新（比如别人刚校准过），会覆盖掉别人的结果。
    这里只原地替换/追加 home_chord_angle 一行，最安全。
    """
    if config_file is None:
        config_file = _default_config_path()
    angle = float(angle)
    try:
        lines = []
        found = False
        if os.path.exists(config_file):
            with open(config_file, "r", encoding="utf-8") as f:
                lines = f.read().splitlines()
        for i, ln in enumerate(lines):
            if ln.split(",")[0].strip() == "home_chord_angle":
                lines[i] = "home_chord_angle,%s" % angle
                found = True
                break
        if not found:
            lines.append("home_chord_angle,%s" % angle)
        with open(config_file, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        _log.info("装机归位角已保存: %s° → %s", angle, config_file)
        return True
    except Exception as e:
        _log.error("保存装机归位角失败: %s", e)
        return False


class ServoController:
    """
    吉他自动演奏舵机控制器
    ==========================
    通过串口命令控制 ESP32 上的舵机：
      - PWM <id> <ang>  : id=0(GPIO40) 或 1(GPIO21), 角度 0-180
      - PCA <ch> <ang>  : PCA9685 通道 1-16, 角度 0-180
      - ALL <ang>       : 全部 18 舵机统一角度
    """

    # ---- set_chord 查表：fret -> (ch0偏移方向, ch0偏移索引, ch1偏移方向, ch1偏移索引) ----
    # 方向 0 表示该通道无额外偏移，索引 None 时不读 press_offsets
    # ⚠️ 同步维护：ESP32 固件 chord_engine.cpp 中 FRET_OFFSETS 须与此表保持一致
    _FRET_OFFSETS = {
        0: ( 0, None,  0, None),
        1: (+1,    0,  0, None),
        2: (-1,    1,  0, None),
        3: ( 0, None, +1,    2),
        4: ( 0, None, -1,    3),
    }

    def __init__(self, config_file: str = None,
                 tune_ripple_threshold: float = 0.5,
                 serial_port: str = "/dev/ttyS3",
                 serial_baud: int = 921600,
                 i2c_bus=None,
                 existing_ser=None,   # 已废弃，保留兼容，串口统一由 SerialBus 管理
                 send_init_commands: bool = True,
                 power_monitor=None):  # ★ 可选: 复用已有 PowerMonitor 实例
        if config_file is None:
            config_file = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       "..", "..", "config", "servo_config.csv")
        self._config_path = os.path.abspath(config_file)
        self._chord_press_threshold_ma = 100  # 和弦按压电流阈值(mA)，_load_config 可能覆盖
        self._load_config(self._config_path)
        self._bus = SerialBus.instance()

        # ── V3 二进制帧层 (懒初始化，首次使用时创建) ──
        self._frame_layer: Optional[FrameLayer] = None

        # ── V3 18 通道完整状态追踪 ──
        # 关键：BATCH 帧协议传输全部 18 通道，但上层用部分更新（chord-only / strum-only）。
        # 必须追踪当前完整状态，避免部分更新时把未涉及的通道覆盖为默认值 90°。
        # _current_chord_angles: 12 个和弦舵机的最后一次设定角度 (度)
        self._current_chord_angles = [90.0] * 12

        # _current_strum_angles: 6 个拨弦舵机的最后一次设定角度 (度)
        # 用于 BATCH 帧合并，避免和弦更新时拨弦通道填 0 导致位置追踪丢失
        self._current_strum_angles = [0.0] * 6

        # 初始化舵机位置（仅在串口可用且未禁用时）
        if send_init_commands and self._bus.is_open:
            chord_angles = [90] * 12
            self._update_chord_channels(chord_angles)
            self._reset_strum_to_idle()
            _log.info("初始化: 和弦回中90, 拨弦归位")

        # ----- 电源监控器初始化 -----
        # ★ 优先复用外部传入的 PowerMonitor (避免重复创建 FrameLayer)
        if power_monitor is not None:
            self.power_monitor = power_monitor
            _log.info("复用已有 PowerMonitor 实例")
        else:
            self.power_monitor = None
            try:
                from power_monitor import PowerMonitor
                self.power_monitor = PowerMonitor()
                _log.info("电源监控器初始化成功")
            except Exception as e:
                _log.warning("电源监控器初始化失败: %s", e)

        self.calibration_stable_status = [None] * 12
        self.tune_ripple_threshold = tune_ripple_threshold
        self._file_lock = threading.Lock()  # 保护配置文件读写

        # 拨弦翻转状态（避免 set_strum 每次 hasattr 检查）
        self._strum_flip_state = [0] * 6

        # ── 校准期间 ADC 实时读取模式 ──
        # V3 模式下 ADC_REPORT 帧推送间隔 ~1s，校准需要实时电流反馈。
        # 开启后 _get_filtered_current 使用 AIN 命令主动轮询替代缓存读取。
        self._use_fresh_adc = False

    # ========== 配置加载与保存 ==========

    def _load_config(self, config_file: str):
        """从 CSV 文件加载配置参数，加载完成后重建通道缓存。"""
        # 若配置文件不存在，从 .example.csv 模板复制
        if not os.path.exists(config_file):
            example_file = config_file.replace('.csv', '.example.csv')
            if os.path.exists(example_file):
                import shutil
                shutil.copy(example_file, config_file)
                _log.info("从 %s 创建默认配置", example_file)

        default_null_offset = [0, 8, 0, -2, 0, -2, 0, -5, 0, -12, 5, -5]
        default_strum_angles = [
            [70, 130], [60, 120], [55, 130],
            [77, 145], [30,  95], [75, 155],
        ]
        default_press_offsets = [
            [45, 50, 55, 60],
            [42, 48, 53, 58],
            [40, 46, 51, 56],
            [-38, -44, -49, -54],
            [-35, -41, -47, -52],
            [-32, -38, -44, -50],
        ]
        default_chord_channel_mapping = [
            "PWM1", "PCA15", "PCA13", "PCA11", "PCA9",  "PCA2",
            "PWM0", "PCA16", "PCA14", "PCA12", "PCA10", "PCA1",
        ]
        default_strum_channel_mapping = ["PCA4", "PCA6", "PCA8", "PCA3", "PCA5", "PCA7"]

        self.null_offset              = default_null_offset[:]
        self._strum_angles            = [row[:] for row in default_strum_angles]
        self._press_offsets           = [row[:] for row in default_press_offsets]
        self._chord_channel_mapping   = default_chord_channel_mapping[:]
        self._strum_channel_mapping   = default_strum_channel_mapping[:]
        # 装机归位角（可调）：和弦收缩位 / 拨弦收缩位覆盖（None=用各弦 point_a）
        self.home_chord_angle         = DEFAULT_HOME_CHORD_ANGLE
        self.home_strum_angle         = None


        if not os.path.exists(config_file):
            _log.warning("配置文件 %s 不存在，使用默认值", config_file)
            self._rebuild_channel_cache()
            return

        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                for row in csv.reader(f):
                    if not row:
                        continue
                    param_type = row[0].strip()

                    if param_type == "null_offset":
                        values = self._parse_floats(row[1:13])
                        if len(values) >= 12:
                            self.null_offset = values[:12]

                    elif param_type.startswith("strum_angles_"):
                        try:
                            idx = int(param_type.split("_")[2]) - 1
                            if 0 <= idx < 6 and len(row) >= 3:
                                self._strum_angles[idx] = [float(row[1]), float(row[2])]
                        except (IndexError, ValueError):
                            pass

                    elif param_type.startswith("press_offset_"):
                        parts = param_type.split("_")
                        if len(parts) == 4:
                            try:
                                string_num = int(parts[2]) - 1
                                fret_num   = int(parts[3]) - 1
                                if 0 <= string_num < 6 and 0 <= fret_num < 4:
                                    self._press_offsets[string_num][fret_num] = float(row[1].strip())
                            except ValueError:
                                pass

                    elif param_type == "chord_channel_mapping":
                        values = [v.strip() for v in row[1:13] if v.strip()]
                        if len(values) >= 12:
                            self._chord_channel_mapping = values[:12]

                    elif param_type == "strum_channel_mapping":
                        values = [v.strip() for v in row[1:7] if v.strip()]
                        if len(values) >= 6:
                            self._strum_channel_mapping = values[:6]

                    elif param_type == "chord_press_threshold_ma":
                        try:
                            self.chord_press_threshold_ma = int(float(row[1].strip()))
                        except (ValueError, IndexError):
                            pass

                    elif param_type == "home_chord_angle":
                        try:
                            self.home_chord_angle = float(row[1].strip())
                        except (ValueError, IndexError):
                            pass

                    elif param_type == "home_strum_angle":
                        try:
                            self.home_strum_angle = float(row[1].strip())
                        except (ValueError, IndexError):
                            pass

            _log.info("已加载配置: %s", config_file)
        except Exception as e:
            _log.warning("读取配置失败: %s，使用默认值", e)

        self._rebuild_channel_cache()

    def reload_config(self):
        """从 CSV 文件重新加载配置，用于 Web API 获取外部更新的校准数据。
        校准期间禁止重载，避免 CSV 中的旧值覆盖正在更新的校准结果。"""
        global _calibration_in_progress
        if _calibration_in_progress:
            _log.debug("校准进行中，跳过 reload_config（避免覆盖校准结果）")
            return
        self._load_config(self._config_path)

    def _save_config(self, config_file: str = None):
        if config_file is None:
            config_file = self._config_path
        with self._file_lock:
            try:
                with open(config_file, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)
                    writer.writerow(['null_offset'] + [str(x) for x in self.null_offset])
                    for i in range(6):
                        writer.writerow([f'strum_angles_{i + 1}',
                                         str(self._strum_angles[i][0]),
                                         str(self._strum_angles[i][1])])
                    for string in range(6):
                        for fret in range(4):
                            writer.writerow([f'press_offset_{string + 1}_{fret + 1}',
                                             str(self._press_offsets[string][fret])])
                    writer.writerow(['chord_channel_mapping'] + self._chord_channel_mapping)
                    writer.writerow(['strum_channel_mapping'] + self._strum_channel_mapping)
                    writer.writerow(['chord_press_threshold_ma',
                                     str(self._chord_press_threshold_ma)])
                    writer.writerow(['home_chord_angle', str(self.home_chord_angle)])
                    if self.home_strum_angle is not None:
                        writer.writerow(['home_strum_angle', str(self.home_strum_angle)])
                _log.info("配置已保存: %s", config_file)
            except Exception as e:
                _log.error("保存配置失败: %s", e)

    # ========== 内部辅助 ==========

    @staticmethod
    def _parse_floats(items: List[str]) -> List[float]:
        """将字符串列表安全转换为 float 列表，跳过无效项。"""
        result = []
        for v in items:
            try:
                result.append(float(v.strip()))
            except ValueError:
                pass
        return result

    def _rebuild_channel_cache(self):
        """将通道字符串解析结果缓存为元组列表，避免热路径重复解析。"""
        self._chord_channels: List[Tuple[str, int]] = [
            self._parse_channel(c) for c in self._chord_channel_mapping
        ]
        self._strum_channels: List[Tuple[str, int]] = [
            self._parse_channel(c) for c in self._strum_channel_mapping
        ]

    @property
    def chord_press_threshold_ma(self) -> int:
        """和弦按压电流阈值 (mA)，用于校准时判定是否接触到琴弦。"""
        return self._chord_press_threshold_ma

    @chord_press_threshold_ma.setter
    def chord_press_threshold_ma(self, value_ma: int):
        self._chord_press_threshold_ma = max(10, min(500, value_ma))

    # ========== 电流滤波辅助（和弦校准用） ==========

    def _get_filtered_current(self, samples: int = 10, delay: float = 0.03) -> float:
        """获取和弦舵机平均电流（A），含异常检测和去极值滤波。

        改进:
          - 增加采样数到 10 个，避免单个 ADC 读数窗口的偏倚
          - 异常负值超过 30% 时发出警告
          - 使用 trimmed mean（去首尾各 20%）提高抗噪能力

        ★ 校准模式 (self._use_fresh_adc=True):
          绕过 V3 ADC_REPORT 缓存 (~1Hz 推送)，每次采样通过 AIN 命令
          主动读取 ESP32 ADC，获取实时电流值。单次读取 ~50-100ms。
          采样数应减少 (samples=2~3) 以适应校准时序。
        """
        if self.power_monitor is None:
            return 0.0
        readings = []
        error_count = 0
        for _ in range(samples):
            if self._use_fresh_adc:
                val = self.power_monitor.read_chord_current_fresh()
            else:
                val = self.power_monitor.chord_servo_current()
            if val >= -0.01:
                readings.append(val)
            else:
                error_count += 1
                # 连续 3 次异常 → 提前告警
                if error_count >= 3 and error_count % 3 == 0:
                    _log.warning("电流传感器连续读取异常 (%d次)，请检查 ESP32 连接", error_count)
            time.sleep(delay)
        if not readings:
            return 0.0
        if error_count > samples * 0.3:
            _log.warning("超过30%%的电流读数为异常值(%d/%d)，校准结果可能不准确", error_count, samples)
        # 排序后去首尾各 20% 的极值（trimmed mean）
        arr = sorted(readings)
        trim = max(1, len(arr) // 5)
        trimmed = arr[trim:-trim] if len(arr) > trim * 2 else arr
        return sum(trimmed) / len(trimmed)

    def _get_stable_current_trimmed(self, samples: int = 8, sample_delay: float = 0.03,
                                     wait_stable: float = 0.5) -> Tuple[float, float, int]:
        """去极值滤波后返回 (平均电流, 波动, 有效样本数)，自动过滤异常负值。

        注意: 异常负值超过采样数 30% 时发出警告（与 _get_filtered_current 保持一致）。

        ★ 校准模式 (self._use_fresh_adc=True): 每次采样通过 AIN 命令主动读取。
        """
        if self.power_monitor is None:
            return 0.0, 0.0, 0
        time.sleep(wait_stable)
        raw = []
        error_count = 0
        for _ in range(samples):
            if self._use_fresh_adc:
                val = self.power_monitor.read_chord_current_fresh()
            else:
                val = self.power_monitor.chord_servo_current()
            if val >= -0.01:
                raw.append(val)
            else:
                error_count += 1
            time.sleep(sample_delay)
        if error_count > samples * 0.3:
            _log.warning("_get_stable_current_trimmed 中超过30%%异常读数(%d/%d)", error_count, samples)
        if not raw:
            return 0.0, 0.0, 0
        # 去极值：排序后去掉最大最小各一个（样本 >= 3 时）
        arr = np.array(sorted(raw))
        trimmed = arr[1:-1] if len(arr) >= 3 else arr
        avg    = float(trimmed.mean())
        ripple = float(trimmed.max() - trimmed.min())
        return avg, ripple, len(trimmed)

    def _check_current_in_range_stable(self, lower: float, upper: float,
                                        duration: float, check_interval: float = 0.03) -> bool:
        """连续检测和弦电流是否在区间内（简单滤波）。"""
        if self.power_monitor is None:
            return False
        for _ in range(max(1, int(duration / check_interval))):
            cur = self._get_filtered_current(samples=3, delay=0.02)
            if not (lower <= cur <= upper):
                return False
            time.sleep(check_interval)
        return True

    # ========== 和弦按压舵机校准 ==========

    def _set_single_chord_servo(self, servo_idx: int, angle: float):
        if servo_idx < 0 or servo_idx >= 12:
            _log.error("舵机索引无效: %d (须0-11)", servo_idx)
            return
        angles_12 = [None] * 12
        angles_12[servo_idx] = angle
        self._update_chord_channels(angles_12)
        time.sleep(0.05)

    def _fine_tune_angle(self, servo_idx: int, trigger_angle: float,
                         target_lower: float, target_upper: float,
                         pressure_inc_dir: int, stable_duration: float,
                         max_attempts: int = 8,
                         stop_event: Optional[threading.Event] = None):
        """动态步长精细调整（和弦）。(优化：减少延时和尝试次数)"""
        self._set_single_chord_servo(servo_idx, trigger_angle)
        time.sleep(0.5)

        fine_angle       = trigger_angle
        consecutive_good = 0
        last_angle       = fine_angle
        center           = (target_lower + target_upper) / 2.0

        for attempt in range(max_attempts):
            if stop_event and stop_event.is_set():
                _log.info("停止事件触发，退出精细调整")
                return trigger_angle, False
            if attempt > 0:
                self._set_single_chord_servo(servo_idx, fine_angle)

            avg, ripple, _ = self._get_stable_current_trimmed(
                samples=5, sample_delay=0.03, wait_stable=0.3
            )
            in_range  = target_lower <= avg <= target_upper
            ripple_ok = ripple <= self.tune_ripple_threshold

            _log.debug("精细调整 %d/%d: 角度=%.1f°, 稳态电流=%.3fA, 波动=%.3fA (目标 %.3f~%.3f)",
                       attempt+1, max_attempts, fine_angle, avg, ripple,
                       target_lower, target_upper)

            if in_range:
                if ripple_ok:
                    if self._check_current_in_range_stable(target_lower, target_upper,
                                                           stable_duration):
                        _log.debug("稳定！按压点确定为 %.1f°", fine_angle)
                        return fine_angle, True
                    _log.debug("长时间稳定验证未通过，继续微调...")
                    consecutive_good = 0
                else:
                    consecutive_good += 1
                    _log.debug("电流在区间内但波动大（连续良好 %d/3）", consecutive_good)
                    if consecutive_good >= 3:
                        _log.debug("连续3次在目标区间，视为相对稳定。按压点确定为 %.1f°", fine_angle)
                        return fine_angle, True
                    time.sleep(0.5)
                    continue
            else:
                consecutive_good = 0

            error = center - avg
            step  = min(0.05 + 1.5 * abs(error), 2.0)
            move  = pressure_inc_dir * step if error > 0 else -pressure_inc_dir * step

            fine_angle = max(0.0, min(180.0, fine_angle + move))
            if abs(fine_angle - last_angle) < 0.01 and not in_range:
                _log.debug("角度无法进一步调整，退出精细调整")
                break
            last_angle = fine_angle

        _log.debug("达到最大调整次数，判定为不稳定")
        return trigger_angle, False

    def calibrate_single_chord_servo(self, servo_idx: int, save_to_file: bool = False,
                                     target_current_upper: float = 0.3,
                                     target_current_lower: float = 0.1,
                                     stable_duration: float = 0.5,
                                     stop_event: Optional[threading.Event] = None) -> Optional[float]:
        """校准单个和弦舵机的按压角度（基于电流）。"""
        if self.power_monitor is None:
            _log.error("电源监控器未初始化，无法校准")
            return None
        if servo_idx < 0 or servo_idx >= 12:
            _log.error("舵机索引必须在0-11之间，当前: %d", servo_idx)
            return None
        if stop_event and stop_event.is_set():
            _log.info("停止事件触发，取消校准")
            return None

        # ── 传感器自检：快速读取电流，确认传感器工作正常 ──
        _log.info("  传感器自检...")
        test_readings = []
        # ★ 校准模式使用实时 AIN 读取 (绕过 pause 状态和 V3 缓存)
        read_func = (self.power_monitor.read_chord_current_fresh
                     if self._use_fresh_adc
                     else self.power_monitor.chord_servo_current)
        test_samples = 5 if self._use_fresh_adc else 10
        for _ in range(test_samples):
            val = read_func()
            if val >= -0.01:
                test_readings.append(val)
            time.sleep(0.05 if self._use_fresh_adc else 0.02)
        if not test_readings or all(v == 0.0 for v in test_readings):
            _log.error("  传感器自检失败: 读数全为0或异常")
            return None
        test_avg = sum(test_readings) / len(test_readings)
        test_var = max(test_readings) - min(test_readings) if len(test_readings) >= 2 else 0
        _log.info("  自检通过: 平均=%.4fA, 波动=%.4fA", test_avg, test_var)

        _log.info("-" * 50)
        _log.info("=== 校准和弦舵机 %d/12 ===", servo_idx + 1)
        _log.info("按压电流阈值: %.3fA (快进→慢退找临界点法)", target_current_lower)
        _log.info("-" * 50)

        try:
            self._set_single_chord_servo(servo_idx, 90)
            _log.info("  等待 2s 采集待机电流作为零点...")
            time.sleep(2.0)
            if stop_event and stop_event.is_set():
                return None
            idle_current = self._get_filtered_current(samples=8, delay=0.06)
            _log.info("  待机电流零点: %.3fA", idle_current)

            calibrated_angles = []
            stable_flags      = [False, False]

            def _find_press_boundary(direction_label, step_sign, idle_current):
                """
                快进→慢退找电流临界点（新算法）：
                  1. 从90°快进(5°步长)直到电流明确超过阈值
                  2. 回退5°释放压力
                  3. 慢进(0.5°步长)找到电流刚好超过阈值的临界角度
                  4. 3次确认后返回临界角度
                :param direction_label: 方向描述
                :param step_sign: +1 递增(90°→180°), -1 递减(90°→0°)
                :return: (临界角度, 是否稳定) 或 (None, False)
                """
                _log.debug("  %s", direction_label)
                boundary_angle = None

                # ---- 阶段1：快进（3°步长）直到明确按压 ----
                angle = 90.0
                fast_trigger = None
                while True:
                    if stop_event and stop_event.is_set():
                        return None, False
                    # 边界检查
                    if step_sign > 0 and angle > 180:
                        break
                    if step_sign < 0 and angle < 0:
                        break
                    self._set_single_chord_servo(servo_idx, angle)
                    time.sleep(0.25)
                    current = self._get_filtered_current(samples=5, delay=0.02)
                    net = current - idle_current
                    _log.debug("    快进 角度: %6.1f°, 电流: %.3fA (净值: %.3fA)", angle, current, net)
                    if net > target_current_lower:
                        # 二次确认防止假阳性
                        time.sleep(0.15)
                        confirm = self._get_filtered_current(samples=5, delay=0.02)
                        if (confirm - idle_current) > target_current_lower:
                            fast_trigger = angle
                            _log.debug("    快进触发！角度=%.1f°", angle)
                            break
                    angle += 3.0 * step_sign

                if fast_trigger is None:
                    return None, False

                # ---- 阶段2：回退释放压力 ----
                back_angle = fast_trigger - 3.0 * step_sign
                # 确保回退不越界
                if step_sign > 0:
                    back_angle = max(90.0, back_angle)
                else:
                    back_angle = min(90.0, back_angle)
                _log.debug("    回退至 %.1f° 释放压力...", back_angle)
                self._set_single_chord_servo(servo_idx, back_angle)
                time.sleep(0.35)

                # ---- 阶段3：慢进(0.5°步长)找电流临界点 ----
                angle = back_angle
                while True:
                    if stop_event and stop_event.is_set():
                        return None, False
                    angle += 0.5 * step_sign
                    # 不超过快进触发角度
                    if step_sign > 0 and angle > fast_trigger:
                        angle = fast_trigger
                    if step_sign < 0 and angle < fast_trigger:
                        angle = fast_trigger

                    self._set_single_chord_servo(servo_idx, angle)
                    time.sleep(0.35)
                    current = self._get_filtered_current(samples=5, delay=0.06)
                    net = current - idle_current
                    _log.debug("    临界搜索 角度: %5.1f°, 电流: %.3fA (净值: %.3fA 阈值: %.3fA)",
                               angle, current, net, target_current_lower)

                    if net > target_current_lower:
                        # 找到临界点！做3次确认
                        _log.debug("    候选临界点！确认读数...")
                        confirm_count = 0
                        for ci in range(3):
                            if stop_event and stop_event.is_set():
                                return None, False
                            time.sleep(0.15)
                            c = self._get_filtered_current(samples=5, delay=0.06)
                            c_net = c - idle_current
                            if c_net > target_current_lower:
                                confirm_count += 1
                            _log.debug("      确认 %d/3: %.3fA (净值: %.3fA) %s",
                                       ci+1, c, c_net,
                                       "✓" if c_net > target_current_lower else "✗")
                        if confirm_count >= 2:
                            boundary_angle = angle
                            is_stable = (confirm_count == 3)
                            _log.debug("    临界点确认！按压角度=%.1f° (稳定=%s)", boundary_angle, is_stable)
                            return boundary_angle, is_stable
                        else:
                            _log.debug("    确认失败(%d/3)，继续搜索...", confirm_count)

                    # 到达快进触发角度仍未找到 → 用快进角度作为按压点
                    if angle == fast_trigger:
                        break

                # 回退到快进触发角度
                _log.debug("    未找到更精确临界点，使用快进角度 %.1f°", fast_trigger)
                return fast_trigger, False

            # ===== 递增方向：90°→180° =====
            _log.debug("  第二步：递增方向（90°→180°），寻找按压临界点...")
            if stop_event and stop_event.is_set():
                return None
            # 递增方向空闲基线
            self._set_single_chord_servo(servo_idx, 90)
            time.sleep(1.0)
            if stop_event and stop_event.is_set():
                return None
            idle_inc = self._get_filtered_current(samples=8, delay=0.06)
            if idle_inc < 0.001:
                idle_inc = idle_current
                _log.warning("  递增方向基线异常，回退使用全局基线")
            _log.info("  递增方向空闲基线: %.3fA", idle_inc)
            press_angle_1, stable_flags[0] = _find_press_boundary("递增方向", step_sign=+1, idle_current=idle_inc)
            if press_angle_1 is None:
                _log.error("  递增方向未找到按压点，跳过此舵机（保留原有配置）")
                self.calibration_stable_status[servo_idx] = False
                return None
            calibrated_angles.append(press_angle_1)
            _log.info("  递增按压点: %.1f°, 稳定=%s", press_angle_1, stable_flags[0])

            # ===== 回到90度并测量递减基线 =====
            _log.debug("  第三步：回到90°位置，测量递减基线...")
            if stop_event and stop_event.is_set():
                return None
            self._set_single_chord_servo(servo_idx, 90)
            time.sleep(1.0)
            if stop_event and stop_event.is_set():
                return None
            idle_dec = self._get_filtered_current(samples=8, delay=0.06)
            if idle_dec < 0.001:
                idle_dec = idle_current
                _log.warning("  递减方向基线异常，回退使用全局基线")
            _log.info("  递减方向空闲基线: %.3fA", idle_dec)

            # ===== 递减方向：90°→0° =====
            _log.debug("  第四步：递减方向（90°→0°），寻找按压临界点...")
            press_angle_2, stable_flags[1] = _find_press_boundary("递减方向", step_sign=-1, idle_current=idle_dec)
            if press_angle_2 is None:
                _log.error("  递减方向未找到按压点，跳过此舵机（保留原有配置）")
                self.calibration_stable_status[servo_idx] = False
                return None
            calibrated_angles.append(press_angle_2)
            _log.info("  递减按压点: %.1f°, 稳定=%s", press_angle_2, stable_flags[1])

            # ===== 回到90度 =====
            _log.debug("  第五步：回到90°位置...")
            if stop_event and stop_event.is_set():
                return None
            self._set_single_chord_servo(servo_idx, 90)
            time.sleep(0.5)

            # ===== 计算零点偏移（两个方向都已确认找到按压点）=====
            real_zero_angle = (calibrated_angles[0] + calibrated_angles[1]) / 2.0
            _log.info("  递增按压点: %.1f°", calibrated_angles[0])
            _log.info("  递减按压点: %.1f°", calibrated_angles[1])
            _log.info("  机械零点: %.1f°", real_zero_angle)

            # 更新 null_offset
            new_null_offset = real_zero_angle - 90.0
            old_offset = self.null_offset[servo_idx]
            self.null_offset[servo_idx] = new_null_offset
            _log.info("  零点偏移: %+.2f → %+.2f", old_offset, new_null_offset)

            # ===== 更新按压偏移量 (press_offset) =====
            # 计算从零点到触发点的按压距离
            press_dist_1 = abs(calibrated_angles[0] - real_zero_angle)
            press_dist_2 = abs(calibrated_angles[1] - real_zero_angle)
            _log.info("  按压距离: 方向1=%.1f°, 方向2=%.1f°", press_dist_1, press_dist_2)

            string_idx = servo_idx // 2  # 舵机对应的弦索引
            is_even = (servo_idx % 2 == 0)  # 偶数舵机是 ch0，奇数舵机是 ch1

            if is_even:
                # 偶数舵机 (ch0): 控制 fret 1(+) 和 fret 2(-)
                old_p0 = self._press_offsets[string_idx][0]
                old_p1 = self._press_offsets[string_idx][1]
                self._press_offsets[string_idx][0] = press_dist_1  # fret 1 (+方向)
                self._press_offsets[string_idx][1] = press_dist_2  # fret 2 (-方向)
                _log.info("  第%d弦 press_offset 更新: 1品 %.0f→%.0f, 2品 %.0f→%.0f",
                          string_idx+1, old_p0, press_dist_1, old_p1, press_dist_2)
            else:
                # 奇数舵机 (ch1): 控制 fret 3(+) 和 fret 4(-)
                old_p2 = self._press_offsets[string_idx][2]
                old_p3 = self._press_offsets[string_idx][3]
                self._press_offsets[string_idx][2] = press_dist_1  # fret 3 (+方向)
                self._press_offsets[string_idx][3] = press_dist_2  # fret 4 (-方向)
                _log.info("  第%d弦 press_offset 更新: 3品 %.0f→%.0f, 4品 %.0f→%.0f",
                          string_idx+1, old_p2, press_dist_1, old_p3, press_dist_2)

            overall_stable = stable_flags[0] and stable_flags[1]
            self.calibration_stable_status[servo_idx] = overall_stable
            _log.info("  本次校准判定为%s", "稳定" if overall_stable else "不稳定")

            # 简单验证
            test_angle = 90 + new_null_offset
            self._set_single_chord_servo(servo_idx, test_angle)
            time.sleep(0.5)
            cur_empty = self._get_filtered_current(3)
            _log.info("  空弦验证电流: %.3fA", cur_empty)

            if save_to_file:
                self._save_config()
                _log.info("  配置已保存")
            return new_null_offset
        finally:
            self._set_single_chord_servo(servo_idx, 90)
            time.sleep(0.1)

    # ═══════════════════════════════════════════════════════════════
    # 单舵机校准包装（Web 专属入口；结果只返回不落盘，预览后由 Web apply 保存）
    # ═══════════════════════════════════════════════════════════════

    def run_single_chord_calibration(self, servo_idx: int,
                                      target_current_lower: float = None,
                                      stop_event: Optional[threading.Event] = None,
                                      progress_callback: Optional[Callable[[float, str], None]] = None) -> dict:
        """单独校准一个和弦舵机（0-11），与整轮校准同一算法/同一起点条件。

        返回结构: {kind:'chord', idx:舵机号1-12, status: ok|failed|stopped,
                   reason, old:{…}, new:{…}, stable, message}
        不写 CSV：结束时 _load_config 还原内存 + 还原稳定标志，结果仅存在于返回值（Web pending）。
        """
        global _calibration_in_progress
        if _calibration_in_progress:
            return {"kind": "chord", "idx": servo_idx + 1, "status": "failed",
                    "reason": "CONCURRENT_CALIBRATION", "message": "已有校准在进行中"}
        if not (0 <= servo_idx < 12):
            return {"kind": "chord", "idx": servo_idx + 1, "status": "failed",
                    "reason": "BAD_INDEX", "message": f"和弦舵机索引无效: {servo_idx}"}

        snapshot_done = False
        result = None
        try:
            # 以 CSV 为准重新载入（防止本实例内存与最近一次保存不一致）
            self.reload_config()
            string_idx = servo_idx // 2
            is_even = (servo_idx % 2 == 0)
            press_slots = [0, 1] if is_even else [2, 3]  # 偶数→品1/2, 奇数→品3/4
            old_off = self.null_offset[servo_idx]
            old_press = [round(self._press_offsets[string_idx][s], 2) for s in press_slots]
            old_stable = self.calibration_stable_status[servo_idx]
            snapshot_done = True

            _calibration_in_progress = True
            if progress_callback:
                progress_callback(5.0, "归位和弦舵机...")
            # 与整轮流程一致：全部 12 个先回到 90+null_offset
            empty_angles = [90 + offset for offset in self.null_offset]
            self._update_chord_channels(empty_angles)
            time.sleep(2.0)
            if stop_event and stop_event.is_set():
                return {"kind": "chord", "idx": servo_idx + 1, "status": "stopped",
                        "reason": "STOPPED", "message": "已停止"}

            # ★ 电流数据可用性预检：区分「无电流数据/读数全0」（ESP32离线、供电断、
            #   未接线）与「有电流但未触到弦」，避免把数据通道问题误导成机械问题。
            pm = self.power_monitor
            if pm is not None:
                try:
                    age = float(pm.cache_age)
                    fresh_vals = [pm.chord_servo_current() for _ in range(3)]
                    time.sleep(0.05)
                except Exception:
                    age, fresh_vals = None, []
                if age is not None and age > 2.0:
                    return {"kind": "chord", "idx": servo_idx + 1, "status": "failed",
                            "reason": "NO_CURRENT_DATA",
                            "message": "2秒内无电流数据：ESP32 可能离线或未在正常模式——检查串口/固件状态，细节见日志"}
                if not fresh_vals or all(v == 0.0 for v in fresh_vals):
                    return {"kind": "chord", "idx": servo_idx + 1, "status": "failed",
                            "reason": "NO_CURRENT_DATA",
                            "message": "电流读数全为0：舵机供电未开或该路未接线（也可能是电流检测链路异常）——检查供电与接线，细节见日志"}

            if progress_callback:
                progress_callback(20.0, "扫描按压临界点...")
            r = self.calibrate_single_chord_servo(
                servo_idx,
                save_to_file=False,
                target_current_upper=0.3,
                target_current_lower=(target_current_lower
                                       if target_current_lower is not None
                                       else self._chord_press_threshold_ma / 1000.0),
                stable_duration=0.5,
                stop_event=stop_event,
            )
            if r is None:
                if stop_event and stop_event.is_set():
                    result = {"kind": "chord", "idx": servo_idx + 1, "status": "stopped",
                              "reason": "STOPPED", "message": "已停止"}
                else:
                    result = {"kind": "chord", "idx": servo_idx + 1, "status": "failed",
                              "reason": "NO_PRESS_POINT",
                              "message": "未找到按压点（未触弦）——检查机械行程、琴弦状态或按压电流阈值，细节见日志"}
                return result

            new_press = [round(self._press_offsets[string_idx][s], 2) for s in press_slots]
            stable = self.calibration_stable_status[servo_idx]
            result = {
                "kind": "chord", "idx": servo_idx + 1, "status": "ok", "reason": "OK",
                "old": {"null_offset": round(old_off, 2), "press_offsets": old_press},
                "new": {"null_offset": round(float(r), 2), "press_offsets": new_press},
                "stable": bool(stable), "message": "校准完成",
            }
            if progress_callback:
                progress_callback(95.0, "校准完成")
            return result
        except Exception as e:
            _log.error("单舵机校准(和弦%d)异常: %s", servo_idx + 1, e, exc_info=True)
            return {"kind": "chord", "idx": servo_idx + 1, "status": "failed",
                    "reason": "ERROR", "message": f"校准异常: {e}"}
        finally:
            # 顺序敏感：物理归位失能 → 还原内存(CSV) → 还原稳定标志 → 最后清校准锁
            try:
                self.disable_all_servos()
            except Exception as e:
                _log.warning("单舵机校准收尾 disable 异常: %s", e)
            try:
                self._load_config(self._config_path)
            except Exception as e:
                _log.warning("单舵机校准收尾还原配置异常: %s", e)
            if snapshot_done:
                # _load_config 不重置 calibration_stable_status，需显式还原
                self.calibration_stable_status[servo_idx] = old_stable
            _calibration_in_progress = False

    def _store_chord_calib_report(self, results: dict, stop_event) -> None:
        """整轮和弦校准收尾：按 results/stable_status 生成报告（仅追加，不改逻辑）。"""
        ok_idx, failed_idx, unstable_idx = [], [], []
        for servo_idx in range(12):
            if servo_idx in results:
                ok_idx.append(servo_idx + 1)
                if self.calibration_stable_status[servo_idx] is not True:
                    unstable_idx.append(servo_idx + 1)
            else:
                failed_idx.append(servo_idx + 1)
        set_last_calib_report("chord", {
            "kind": "chord", "stopped": bool(stop_event and stop_event.is_set()),
            "total": 12, "ok": ok_idx, "failed": failed_idx, "unstable": unstable_idx,
        })

    def _store_strum_calib_report(self, results: dict, stop_event,
                                  prev_angles: dict) -> None:
        """整轮拨弦校准收尾：按 results 生成报告（幅度一致性=逐弦数据，不判定）。"""
        ok_idx, failed_idx, curr_angles = [], [], {}
        for idx in range(6):
            if idx in results:
                ok_idx.append(idx + 1)
                pa, pb = results[idx]
                curr_angles[idx + 1] = [round(float(pa), 2), round(float(pb), 2)]
            else:
                failed_idx.append(idx + 1)
        set_last_calib_report("strum", {
            "kind": "strum", "stopped": bool(stop_event and stop_event.is_set()),
            "total": 6, "ok": ok_idx, "failed": failed_idx,
            "prev": prev_angles, "curr": curr_angles,
        })
        # 逐弦行程数据落日志，便于无 Web 时核对（幅度一致性由人判断）
        for idx in range(6):
            s = idx + 1
            if s in curr_angles:
                lo, hi = curr_angles[s]
                old = prev_angles.get(s)
                old_w = abs(old[1] - old[0]) if old else None
                _log.info("行程 弦%d: 本次 %.1f°~%.1f°(宽 %.1f°), 上次宽 %s°",
                          s, lo, hi, abs(hi - lo),
                          "%.1f" % old_w if old_w is not None else "-")

    def calibrate_all_chord_servos(self, save_to_file: bool = True,
                                    target_current_upper: float = 0.3,
                                    target_current_lower: float = None,
                                    stable_duration: float = 0.5,
                                    progress_callback: Optional[Callable[[float, str], None]] = None,
                                    stop_event: Optional[threading.Event] = None) -> dict:
        """校准所有12个和弦舵机（基于电流）。"""
        if target_current_lower is None:
            target_current_lower = self._chord_press_threshold_ma / 1000.0
        _log.info("-" * 50)
        _log.info("=== 开始校准 12 个和弦舵机 ===")
        _log.info("目标电流范围: %.3fA ~ %.3fA, 稳定时间: %.1fs",
                  target_current_lower, target_current_upper, stable_duration)
        _log.info("-" * 50)

        # ★ 注: _use_fresh_adc 保持 False，V3 二进制 UART 不支持 ASCII AIN 命令。
        # 校准依赖 ADC_REPORT 帧推送缓存 (~1Hz)，校准算法的延时和多次确认可容忍此延迟。
        # 未来 ESP32 固件增加二进制 ADC_REQUEST 帧后可启用实时读取。

        # ★ 校准期间独占 CSV 文件，禁止 _load_config 读取
        global _calibration_in_progress
        _calibration_in_progress = True

        results = {}
        empty_angles = [90 + offset for offset in self.null_offset]
        self._update_chord_channels(empty_angles)
        time.sleep(2.0)

        # 校准超时熔断：单个舵机最多 180 秒，总校准最多 30 分钟
        _calibration_start = time.time()
        _MAX_TOTAL_SECONDS = 1800       # 30 分钟总超时
        _MAX_SINGLE_SERVO_SECONDS = 180  # 单个舵机 3 分钟超时

        total_servos = 12
        for servo_idx in range(total_servos):
            # 总超时检查
            if time.time() - _calibration_start > _MAX_TOTAL_SECONDS:
                _log.warning("校准总超时(%ds)，已校准 %d/12 个舵机，中止",
                             _MAX_TOTAL_SECONDS, len(results))
                break

            _servo_start = time.time()
            if stop_event and stop_event.is_set():
                _log.info("停止事件触发，终止校准")
                break
            if progress_callback:
                progress = (servo_idx / total_servos) * 100
                status = f"和弦舵机校准 {servo_idx+1}/{total_servos}"
                progress_callback(progress, status)

            _log.info("%s 舵机 %d/12 %s", "=" * 20, servo_idx + 1, "=" * 20)
            result = self.calibrate_single_chord_servo(
                servo_idx,
                save_to_file=False,
                target_current_upper=target_current_upper,
                target_current_lower=target_current_lower,
                stable_duration=stable_duration,
                stop_event=stop_event,
            )
            # 单个舵机超时检查
            _servo_elapsed = time.time() - _servo_start
            if _servo_elapsed > _MAX_SINGLE_SERVO_SECONDS:
                _log.warning("舵机 %d 校准超时(%.0fs > %ds)，跳过",
                             servo_idx+1, _servo_elapsed, _MAX_SINGLE_SERVO_SECONDS)
            elif result is not None:
                results[servo_idx] = result
            if progress_callback:
                progress = ((servo_idx + 1) / total_servos) * 100
                status = f"和弦舵机校准 {servo_idx+1}/{total_servos} 完成"
                progress_callback(progress, status)
            time.sleep(1.5)

        if progress_callback and not (stop_event and stop_event.is_set()):
            progress_callback(100.0, "校准完成")
        _log.info("-" * 50)
        _log.info("=== 和弦舵机校准全部完成 ===")
        _log.info("-" * 50)
        _log.info("校准结果汇总:")
        _log.info("-" * 50)
        for servo_idx in range(12):
            status = self.calibration_stable_status[servo_idx]
            status_str = "稳定" if status else ("不稳定" if status is False else "未校准")
            if servo_idx in results:
                offset = results[servo_idx]
                _log.info("舵机 %2d: 零点偏移 = %+.2f°, 实际零点 = %.2f°, 状态: %s",
                          servo_idx + 1, offset, 90 + offset, status_str)
            else:
                _log.info("舵机 %2d: 校准失败，状态: %s", servo_idx + 1, status_str)

        # ★ 整轮报告（模块级，OLED/Web 收尾点读取）
        self._store_chord_calib_report(results, stop_event)

        if save_to_file and results:
            self._save_config()
            _log.info("所有校准结果已保存到 servo_config.csv")

        # ★ 校准完成后归位拨弦并失能，确保 PCA9685 所有寄存器持有有效角度再入睡
        self.disable_all_servos()

        _calibration_in_progress = False
        return results

    # ========== 拨弦舵机行程校准（调用 guitar_tuner 检测器） ==========

    def _set_single_strum_servo(self, servo_idx: int, angle: float):
        """控制单个拨弦舵机到指定角度，其他拨弦舵机保持不动。"""
        if servo_idx < 0 or servo_idx >= 6:
            _log.error("拨弦舵机索引无效: %d (须0-5)", servo_idx)
            return
        angle = max(STRUM_ANGLE_MIN, min(STRUM_ANGLE_MAX, angle))
        angles_6 = [None] * 6
        angles_6[servo_idx] = angle
        self._update_strum_channels(angles_6)
        time.sleep(0.1)

    def _detector_init(self, noise_floor_override: Optional[float] = None) -> FastPluckDetector:
        """初始化 FastPluckDetector（手动管理资源，不进入 run 循环）。

        noise_floor_override:
          - None: 检测器内部调用 calibrate_noise() 自行采集 2s 环境音
          - float: 使用外部预检值（strum_servo_calibration 的 _pre_check_noise 结果）

        freeze_noise 保持 False（默认），允许 50 帧中位数窗口自适应。
        检测参数在 calibrate_all_strum_servos() 中逐弦通过 configure_for_string() 切换。
        """
        detector = FastPluckDetector()
        if noise_floor_override is not None:
            detector.noise_floor = noise_floor_override
            _log.info("使用预检噪音基准: %.1f", noise_floor_override)
        else:
            detector.noise_floor = calibrate_noise(
                detector.p, detector.device_index, SAMPLE_RATE, CHUNK_FAST, duration=2.0
            )
            _log.info("检测器自行校准噪音基准: %.1f", detector.noise_floor)
        detector.noise_history.extend([detector.noise_floor] * detector.noise_history.maxlen)
        detector.stream = detector.p.open(
            format=FORMAT, channels=CHANNELS, rate=SAMPLE_RATE,
            input=True, input_device_index=detector.device_index,
            frames_per_buffer=CHUNK_FAST,
        )
        return detector

    def _detector_close(self, detector: FastPluckDetector):
        """停止 detector 并释放资源。"""
        if detector is not None:
            detector.stop()

    def _calibrate_strum_one_direction(self, servo_idx: int,
                                        start_angle: float,
                                        target_angle: float,
                                        detector: FastPluckDetector,
                                        direction_sign: int,
                                        stop_event: Optional[threading.Event] = None) -> Optional[float]:
        """
        沿一个方向快速搜索拨弦触发点，步长5.0°。
        检测到 plucking 立即将当前角度作为最终结果，无回退、无慢速。
        :return: 找到的角度，失败返回 None
        """
        angle = start_angle
        step_abs = 5.0
        # 用夹紧边界限制 range 检查，避免在 125°/55° 死循环
        if direction_sign > 0:
            effective_limit = min(target_angle, STRUM_ANGLE_MAX)
            in_range = lambda a: a < effective_limit
        else:
            effective_limit = max(target_angle, STRUM_ANGLE_MIN)
            in_range = lambda a: a > effective_limit

        while in_range(angle):
            if stop_event and stop_event.is_set():
                _log.info("停止事件触发，终止搜索")
                return None
            self._set_single_strum_servo(servo_idx, angle)
            time.sleep(0.15)

            for _ in range(5):
                detector.update()
                time.sleep(0.02)
            plucking = detector.current_status
            _log.debug("    角度: %6.1f°, 拨弦状态: %s", angle, "是" if plucking else "否")

            if plucking:
                final_angle = max(STRUM_ANGLE_MIN, min(STRUM_ANGLE_MAX, angle + direction_sign * 2.0))
                _log.debug("    触发！记录点: %.1f°", final_angle)
                return final_angle

            angle = max(STRUM_ANGLE_MIN, min(STRUM_ANGLE_MAX, angle + direction_sign * step_abs))

        _log.debug("    未触发拨弦，使用边界角度")
        return None

    def calibrate_single_strum_servo(self, servo_idx: int,
                                     idle_angle: float = 115.0,
                                     detector: Optional[FastPluckDetector] = None,
                                     save_to_file: bool = False,
                                     stop_event: Optional[threading.Event] = None,
                                     reverse_search: bool = False) -> Optional[List[float]]:
        """
        校准单个拨弦舵机的行程（两个极限位置），基于音频检测。
        默认：从 idle_angle 先向低搜索 → point_a，再向高搜索 → point_b。
        reverse_search=True：先向高搜索 → point_b，再向低搜索 → point_a（ab 交换）。
        若两点差距 < 10° 则自动重试（最多3次）。
        校准完成后，舵机将自动停在 point_a 待命。
        :param servo_idx: 舵机索引 (0-5)
        :param idle_angle: 起始角度（默认115°）
        :param detector: guitar_tuner 检测器实例（若为None则自动创建临时实例）
        :param save_to_file: 是否立即保存到配置文件
        :param stop_event: 停止事件
        :param reverse_search: 是否反转搜索顺序（先高后低，结果赋值交换）
        :return: [point_a, point_b] 成功返回，否则 None
        """
        if servo_idx < 0 or servo_idx >= 6:
            _log.error("拨弦舵机索引必须在0-5之间，当前: %d", servo_idx)
            return None
        if stop_event and stop_event.is_set():
            return None

        internal_detector = None
        if detector is None:
            internal_detector = self._detector_init()
            detector = internal_detector
            _log.info("已创建临时音频检测器。")

        try:
            _log.info("-" * 50)
            _log.info("=== 校准拨弦舵机 %d/6 ===", servo_idx + 1)
            _log.info("起始角度=%.1f°, 搜索模式=%s, 使用 guitar_tuner 音频检测",
                      idle_angle,
                      "先低后高" if not reverse_search else "先高后低(ab交换)")
            _log.info("-" * 50)

            MAX_RETRIES = 3
            point_a = point_b = None
            for attempt in range(MAX_RETRIES):
                if stop_event and stop_event.is_set():
                    return None
                if attempt > 0:
                    _log.info("--- 重新校准（第 %d/%d 次）---", attempt + 1, MAX_RETRIES)

                # 1. 仅将当前舵机移动到起始角度
                _log.info("将当前舵机移动到起始角度 %.1f° ...", idle_angle)
                self._set_single_strum_servo(servo_idx, idle_angle)
                time.sleep(1.5)
                if stop_event and stop_event.is_set():
                    return None

                # 2. 让检测器适应环境
                for _ in range(10):
                    detector.update()
                    time.sleep(0.02)

                # 3. 根据搜索模式确定两次搜索的方向
                if reverse_search:
                    search1_target, search1_sign, search1_label = 130.0, +1, "向高角度"
                else:
                    search1_target, search1_sign, search1_label = 50.0, -1, "向低角度"

                _log.info("%s搜索（从 %.1f° 向 %.0f°）...", search1_label, idle_angle, search1_target)
                point_1 = self._calibrate_strum_one_direction(
                    servo_idx, idle_angle, search1_target, detector,
                    direction_sign=search1_sign, stop_event=stop_event
                )
                if stop_event and stop_event.is_set():
                    return None
                if point_1 is None:
                    _log.warning("舵机 %d: 未找到第一个触发点（已达边界），跳过该弦", servo_idx + 1)
                    return None
                else:
                    _log.info("第一触发点: %.1f°", point_1)

                # 4. 等待 3s（期间持续调用 detector.update() 耗尽残留的拨弦保持状态）
                _log.info("等待 3s（清除检测器残留状态）...")
                wait_deadline = time.time() + 3.0
                while time.time() < wait_deadline:
                    if stop_event and stop_event.is_set():
                        return None
                    detector.update()
                    time.sleep(0.05)

                # 5. 第二次搜索
                if reverse_search:
                    search2_target, search2_sign, search2_label = 50.0, -1, "向低角度"
                else:
                    search2_target, search2_sign, search2_label = 130.0, +1, "向高角度"

                _log.info("%s搜索（从 %.1f° 向 %.0f°）...", search2_label, point_1, search2_target)
                point_2 = self._calibrate_strum_one_direction(
                    servo_idx, point_1, search2_target, detector,
                    direction_sign=search2_sign, stop_event=stop_event
                )
                if stop_event and stop_event.is_set():
                    return None
                if point_2 is None:
                    _log.warning("舵机 %d: 未找到第二个触发点（已达边界），跳过该弦", servo_idx + 1)
                    return None
                else:
                    _log.info("第二触发点: %.1f°", point_2)

                # 根据模式赋值 point_a / point_b（始终 [低点, 高点]）
                if reverse_search:
                    point_b, point_a = point_1, point_2   # 交换：第一次→b, 第二次→a
                else:
                    point_a, point_b = point_1, point_2   # 正常：第一次→a, 第二次→b

                # 检查两点差距是否足够
                gap = abs(point_a - point_b)
                if gap >= 10.0:
                    _log.info("两点差距 %.1f° >= 10°，校准合格", gap)
                    break
                _log.info("两点差距仅 %.1f° < 10°，需要重新校准", gap)
            else:
                _log.warning("%d 次校准后两点差距仍 < 10°，使用最后结果", MAX_RETRIES)

            # 6. 更新行程: [point_a, point_b]
            self._strum_angles[servo_idx] = [point_a, point_b]
            _log.info("行程更新为: [%.1f°, %.1f°]", point_a, point_b)

            if save_to_file:
                self._save_config()
                _log.info("配置已保存")

            # 7. 移动至第一个触发点待命
            _log.info("校准完成，将舵机移动至第一个点 %.1f° 待命", point_a)
            if stop_event and stop_event.is_set():
                return None
            self._set_single_strum_servo(servo_idx, point_a)
            return [point_a, point_b]
        finally:
            if internal_detector is not None:
                self._detector_close(internal_detector)

    def run_single_strum_calibration(self, servo_idx: int,
                                      noise_floor_override: float = None,
                                      stop_event: Optional[threading.Event] = None,
                                      progress_callback: Optional[Callable[[float, str], None]] = None) -> dict:
        """单独校准一个拨弦舵机（0-5，即弦1-6），环境与整轮校准一致（全组摆位+共检测器）。

        返回结构: {kind:'strum', idx:弦号1-6, status: ok|failed|stopped,
                   reason, old:{lo,hi}, new:{lo,hi}, stable, message}
        不写 CSV：结束时 _load_config 还原内存，结果仅存在于返回值（Web pending）。
        注意：调用前需先暂停 AI 语音助手（独占麦克风由 api 路由层负责）。
        """
        global _calibration_in_progress
        if _calibration_in_progress:
            return {"kind": "strum", "idx": servo_idx + 1, "status": "failed",
                    "reason": "CONCURRENT_CALIBRATION", "message": "已有校准在进行中"}
        if not (0 <= servo_idx < 6):
            return {"kind": "strum", "idx": servo_idx + 1, "status": "failed",
                    "reason": "BAD_INDEX", "message": f"拨弦舵机索引无效: {servo_idx}"}

        detector = None
        snapshot_done = False
        result = None
        try:
            self.reload_config()
            old_angles = [round(float(self._strum_angles[servo_idx][0]), 2),
                          round(float(self._strum_angles[servo_idx][1]), 2)]
            snapshot_done = True

            _calibration_in_progress = True
            if progress_callback:
                progress_callback(5.0, "摆位拨弦舵机...")
            # 与整轮流程一致：全部 6 臂先摆到奇偶起始角度（保证音频环境/结果可比）
            init_angles = [115.0 if i % 2 == 0 else 65.0 for i in range(6)]
            self._update_strum_channels(init_angles)
            time.sleep(5.0)
            if stop_event and stop_event.is_set():
                return {"kind": "strum", "idx": servo_idx + 1, "status": "stopped",
                        "reason": "STOPPED", "message": "已停止"}

            if progress_callback:
                progress_callback(20.0, "初始化音频检测器...")
            detector = self._detector_init(noise_floor_override=noise_floor_override)
            if stop_event and stop_event.is_set():
                return {"kind": "strum", "idx": servo_idx + 1, "status": "stopped",
                        "reason": "STOPPED", "message": "已停止"}
            detector.configure_for_string(servo_idx)

            if progress_callback:
                progress_callback(35.0, "扫描拨弦触发点...")
            if servo_idx % 2 == 0:
                angles = self.calibrate_single_strum_servo(
                    servo_idx, idle_angle=115.0, detector=detector,
                    save_to_file=False, stop_event=stop_event,
                )
            else:
                angles = self.calibrate_single_strum_servo(
                    servo_idx, idle_angle=65.0, detector=detector,
                    save_to_file=False, stop_event=stop_event,
                    reverse_search=True,
                )
            if angles is None:
                if stop_event and stop_event.is_set():
                    result = {"kind": "strum", "idx": servo_idx + 1, "status": "stopped",
                              "reason": "STOPPED", "message": "已停止"}
                else:
                    result = {"kind": "strum", "idx": servo_idx + 1, "status": "failed",
                              "reason": "NO_PLUCK_DETECTED",
                              "message": "未检测到拨弦声——请检查拨片是否在弦上方、琴弦与麦克风状态，或环境噪音是否过高（细节见日志）"}
                return result

            lo, hi = (round(float(angles[0]), 2), round(float(angles[1]), 2))
            result = {
                "kind": "strum", "idx": servo_idx + 1, "status": "ok", "reason": "OK",
                "old": {"lo": old_angles[0], "hi": old_angles[1]},
                "new": {"lo": lo, "hi": hi},
                "stable": True, "message": "校准完成",
            }
            if progress_callback:
                progress_callback(95.0, "校准完成")
            return result
        except Exception as e:
            _log.error("单舵机校准(拨弦%d)异常: %s", servo_idx + 1, e, exc_info=True)
            return {"kind": "strum", "idx": servo_idx + 1, "status": "failed",
                    "reason": "ERROR", "message": f"校准异常: {e}"}
        finally:
            # 顺序敏感：先关检测器 → 物理归位失能 → 还原内存(CSV) → 最后清校准锁
            try:
                if detector is not None:
                    self._detector_close(detector)
            except Exception as e:
                _log.warning("单舵机校准收尾关检测器异常: %s", e)
            try:
                self.disable_all_servos()
            except Exception as e:
                _log.warning("单舵机校准收尾 disable 异常: %s", e)
            try:
                self._load_config(self._config_path)
            except Exception as e:
                _log.warning("单舵机校准收尾还原配置异常: %s", e)
            if snapshot_done:
                self._strum_angles[servo_idx] = [old_angles[0], old_angles[1]]
            _calibration_in_progress = False

    def calibrate_all_strum_servos(self, idle_angle: float = 115.0,
                                    save_to_file: bool = True,
                                    progress_callback: Optional[Callable[[float, str], None]] = None,
                                    stop_event: Optional[threading.Event] = None,
                                    noise_floor_override: Optional[float] = None) -> dict:
        """
        校准所有6个拨弦舵机（按顺序 1,2,3,4,5,6），所有弦共用一个音频检测器。
        每个舵机从115°向下搜索第一个触发点，等待3s后向上搜索第二个触发点。
        校准完成后停在第一个触发点。

        每根弦校准前调用 shared_detector.configure_for_string(idx) 切换检测参数：
          弦1-5(idx=0-4): 保守参数，高门槛防舵机噪声误触发
          弦6(idx=5):     宽松参数，低门槛确保粗弦弱振动可检测
        :param noise_floor_override: 若提供则跳过内部噪音校准，直接使用该值作为噪音基准
        """
        string_to_idx = [0, 1, 2, 3, 4, 5]  # 1,2,3,4,5,6 对应的索引

        # ★ 本轮开始前（任何写入前）的旧行程快照，供 Web 报告展示新旧对比
        prev_angles = {i + 1: [round(float(self._strum_angles[i][0]), 2),
                               round(float(self._strum_angles[i][1]), 2)]
                       for i in range(6)}

        _log.info("-" * 50)
        _log.info("=== 开始校准 6 个拨弦舵机（基于 guitar_tuner 音频检测）===")
        _log.info("校准顺序: 弦1,2,3,4,5,6 (索引 %s)", string_to_idx)
        _log.info("起始角度=%.1f°", idle_angle)
        _log.info("-" * 50)

        # 先将所有拨弦舵机移动到各自的起始角度（奇偶分离）
        if stop_event and stop_event.is_set():
            self._store_strum_calib_report({}, stop_event, prev_angles)
            return {}
        _log.info("将所有拨弦舵机移动到起始角度（1,3,5=115° / 2,4,6=65°）...")
        init_angles = [115.0 if idx % 2 == 0 else 65.0 for idx in range(6)]
        self._update_strum_channels(init_angles)
        time.sleep(5.0)

        shared_detector = self._detector_init(noise_floor_override=noise_floor_override)
        if stop_event and stop_event.is_set():
            self._store_strum_calib_report({}, stop_event, prev_angles)
            self._detector_close(shared_detector)
            return {}
        _log.info("音频检测器已就绪，开始逐弦校准。")

        global _calibration_in_progress
        _calibration_in_progress = True

        results = {}
        total_servos = len(string_to_idx)
        try:
            for i, idx in enumerate(string_to_idx):
                if stop_event and stop_event.is_set():
                    break
                if progress_callback:
                    progress = (i / total_servos) * 100
                    status = f"校准拨弦舵机 {idx+1}/6"
                    progress_callback(progress, status)

                _log.info("%s 舵机 %d/6 %s", "=" * 20, idx + 1, "=" * 20)
                _log.info(">>> 舵机%d: idle=%.0f°, reverse_search=%s",
                          idx + 1, 115.0 if idx % 2 == 0 else 65.0,
                          "否" if idx % 2 == 0 else "是")
                shared_detector.configure_for_string(idx)
                try:
                    # 奇数码(0,2,4): idle=115, 先低后高（默认）
                    # 偶数码(1,3,5): idle=65,  先高后低（交换 ab）
                    if idx % 2 == 0:
                        angles = self.calibrate_single_strum_servo(
                            idx, idle_angle=115.0, detector=shared_detector,
                            save_to_file=False, stop_event=stop_event,
                        )
                    else:
                        angles = self.calibrate_single_strum_servo(
                            idx, idle_angle=65.0, detector=shared_detector,
                            save_to_file=False, stop_event=stop_event,
                            reverse_search=True,
                        )
                    if angles:
                        results[idx] = angles
                except Exception as e:
                    _log.warning("舵机 %d 校准异常: %s，跳过继续下一个", idx+1, e)
                    # 不中断，继续下一个舵机
                if progress_callback:
                    progress = ((i + 1) / total_servos) * 100
                    status = f"拨弦舵机 {idx+1}/6 完成"
                    progress_callback(progress, status)
                time.sleep(3.0)

            if progress_callback and not (stop_event and stop_event.is_set()):
                progress_callback(100.0, "拨弦校准完成")
            _log.info("-" * 50)
            _log.info("=== 拨弦舵机校准全部完成 ===")
            _log.info("-" * 50)
            _log.info("校准结果汇总:")
            _log.info("-" * 40)
            for idx in range(6):
                if idx in results:
                    point_a, point_b = results[idx]
                    _log.info("舵机 %d: 第一个点=%.1f°, 第二个点=%.1f°  待命: %.1f°",
                              idx + 1, point_a, point_b, point_a)
                else:
                    _log.info("舵机 %d: 校准失败", idx + 1)

            # ★ 整轮报告（模块级，OLED/Web 收尾点读取；幅度一致性=逐弦数据不判定）
            self._store_strum_calib_report(results, stop_event, prev_angles)

            # 统一将所有拨弦舵机移动到各自第一个触发点待命
            _log.info("最终确认：所有拨弦舵机移动到第一个触发点待命...")
            if not stop_event or not stop_event.is_set():
                for idx in range(6):
                    if idx in results:
                        point_a, _ = results[idx]
                        self._set_single_strum_servo(idx, point_a)
                    else:
                        self._set_single_strum_servo(idx, 90.0)  # 失败回到中性位
                time.sleep(0.5)

            if save_to_file and results:
                self._save_config()
                _log.info("行程已保存到 servo_config.csv")

            # ★ 校准完成后：拨弦已归位 point_a，失能以节能
            if not stop_event or not stop_event.is_set():
                self.disable_all_servos()
            return results
        finally:
            _calibration_in_progress = False
            self._detector_close(shared_detector)

    # ========== 串口命令发送 ==========

    # 类级别调试开关：设为 True 时记录所有舵机命令（用于诊断舵机不动作问题）
    _debug_all_commands = False

    def enable_debug_commands(self):
        """启用详细命令日志（校准/测试时调用）"""
        ServoController._debug_all_commands = True
        _log.info("舵机命令调试日志已启用")

    def disable_debug_commands(self):
        """禁用详细命令日志"""
        ServoController._debug_all_commands = False
        _log.info("舵机命令调试日志已禁用")

    def _get_frame_layer(self) -> Optional[FrameLayer]:
        """懒初始化 FrameLayer（V3 二进制帧层）。"""
        if self._frame_layer is None and _V3_AVAILABLE and self._bus.is_open:
            try:
                self._frame_layer = FrameLayer(self._bus)
                _log.info("V3 FrameLayer 已初始化（二进制帧协议就绪）")
            except Exception as e:
                _log.warning("FrameLayer 初始化失败: %s，回退到 ASCII", e)
        return self._frame_layer

    def _cmd_serial(self, cmd: str) -> bool:
        """发送舵机命令到 ESP32（fire-and-forget，不等待响应）。
        PWM/PCA/ALL 命令由 ESP32 无回复执行，高速连续发送。"""
        if not self._bus.is_open:
            if not getattr(self, '_warned_serial_closed', False):
                self._warned_serial_closed = True
                _log.warning("串口不可用（SerialBus 未打开）")
            return False
        try:
            cnt = getattr(self, '_cmd_count', 0) + 1
            self._cmd_count = cnt
            if cnt <= 3 or ServoController._debug_all_commands:
                _log.debug("串口发送 #%d: %s", cnt, cmd)
            self._bus.raw_write((cmd + "\n").encode())
            return True
        except Exception as e:
            try:
                from error_collector import get_error_collector
                ec = get_error_collector()
                if ec:
                    from error_codes import ErrorCode
                    ec.report(ErrorCode.SERIAL_READ_TIMEOUT,
                              detail=f"串口发送失败: {e}, cmd={cmd[:30]}")
            except Exception:
                pass
            _log.error("串口发送失败: %s, cmd=%s", e, cmd[:30])
            return False

    def _send_binary_frame(self, frame: Frame) -> bool:
        """发送二进制帧（V3 协议，优先使用）。失败时回退到 ASCII。"""
        fl = self._get_frame_layer()
        if fl is not None:
            try:
                fl.send_frame(frame)
                return True
            except Exception as e:
                _log.warning("二进制帧发送失败: %s，回退 ASCII", e)
        return False

    # ========== 通道控制与解析 ==========

    @staticmethod
    def _parse_channel(channel_str: str) -> Tuple[str, int]:
        if channel_str.startswith("PWM"):
            return ("pwm", int(channel_str[3:]))
        if channel_str.startswith("PCA"):
            pca_num = int(channel_str[3:])
            if not (1 <= pca_num <= 16):
                _log.warning("通道 %s 超出范围(1-16)，已调整为 16", channel_str)
                pca_num = 16
            return ("pca", pca_num)
        raise ValueError(f"未知的通道格式: {channel_str}")

    def _set_channel(self, channel_type: str, channel_num: int, angle: float):
        """统一通道分派 — V3 二进制优先，ASCII 回退。"""
        angle = max(0.0, min(180.0, angle))
        ang_int = int(round(angle))

        # ── V3 二进制尝试 ──
        fl = self._get_frame_layer()
        if fl is not None:
            try:
                ch_type = ServoChType.PWM if channel_type == "pwm" else ServoChType.PCA
                frame = make_servo_single(int(ch_type), channel_num, ang_int)
                fl.send_frame(frame)
                return
            except Exception:
                pass

        # ── ASCII 回退 ──
        if channel_type == "pwm":
            self._cmd_serial(f"PWM {channel_num} {ang_int}")
        elif channel_type == "pca":
            self._cmd_serial(f"PCA {channel_num} {ang_int}")

    def _update_chord_channels(self, angles: List[float], delay: float = 0.0):
        """写入12个和弦舵机角度。

        V3 优化: 使用 NORMAL_SERVO_BATCH 一帧完成 18 通道设置。
        若 FrameLayer 不可用则回退到逐通道 ASCII 发送。

        ★ V3 状态追踪: 仅更新 angles 中非 None 的通道到 _current_chord_angles，
        从完整状态构建 BATCH 帧，避免部分更新时把其他通道覆盖为默认值 90°。
        """
        if len(angles) != 12:
            return

        # ── 更新状态: 非 None → 写入追踪数组 ──
        for i in range(12):
            if angles[i] is not None:
                self._current_chord_angles[i] = float(angles[i])

        fl = self._get_frame_layer()
        if fl is not None:
            # ── V3 二进制: 从追踪状态构建完整 BATCH ──
            batch = [0] * 18
            for i in range(12):
                batch[i] = int(round(max(0.0, min(180.0, self._current_chord_angles[i]))))
            # 拨弦通道发送追踪值（保持当前位置），而非 0
            # ESP32 端 handle_batch 跳过 strum_buf[i]==0，会导致位置追踪丢失
            for i in range(6):
                if self._current_strum_angles[i] > 0:
                    batch[12 + i] = int(round(max(0.0, min(180.0, self._current_strum_angles[i]))))
                else:
                    # 未追踪时使用 point_a 填充，确保 PCA9685 寄存器始终持有有效角度
                    fallback = max(STRUM_ANGLE_MIN, min(STRUM_ANGLE_MAX, self._strum_angles[i][0]))
                    batch[12 + i] = int(round(fallback))

            try:
                frame = make_servo_batch(batch)
                fl.send_frame(frame)
                if delay > 0:
                    time.sleep(delay)
                return
            except Exception as e:
                _log.debug("二进制帧发送失败: %s，回退逐通道 ASCII", e)
                # 回退到 ASCII

        # ── ASCII 回退: 逐通道发送 ──
        if not self._ascii_fallback_safe():
            _log.error("二进制帧不可用且检测到 V6.0 板：ASCII 回退通道表为 V5.1，"
                       "已拒绝执行以免驱动错误插座（请先排查 FrameLayer）")
            return
        for i, angle in enumerate(angles):
            if angle is not None:
                self._set_channel(*self._chord_channels[i], angle)
        if delay > 0:
            time.sleep(delay)

    def _update_strum_channels(self, angles: List[float], delay: float = 0.0):
        """写入6个拨弦舵机角度。

        V3 优化: 使用 NORMAL_SERVO_BATCH 一帧完成 18 通道设置。
        若 FrameLayer 不可用则回退到逐通道 ASCII 发送。

        ★ V3 状态追踪: 和弦通道从 _current_chord_angles 获取当前状态，
        而非默认 90°，防止拨弦更新时意外释放已按压的和弦。
        """
        if len(angles) != 6:
            return

        fl = self._get_frame_layer()
        if fl is not None:
            # ── V3 二进制: 和弦通道保持当前状态，拨弦通道写入目标角度 ──
            batch = [0] * 18
            # 和弦通道: 从追踪状态获取 (保持当前按压状态，不会被重置为 90°)
            for i in range(12):
                batch[i] = int(round(max(0.0, min(180.0, self._current_chord_angles[i]))))
            # 拨弦通道: 非 None → 写入目标角度，None → 保持 0 (ESP32 跳过)
            for i in range(6):
                if angles[i] is not None:
                    a = max(STRUM_ANGLE_MIN, min(STRUM_ANGLE_MAX, angles[i]))
                    batch[12 + i] = int(round(a))

            try:
                frame = make_servo_batch(batch)
                fl.send_frame(frame)
                # ★ 更新拨弦角度追踪（仅非 None 且 > 0 的通道）
                for i in range(6):
                    if angles[i] is not None and angles[i] > 0:
                        self._current_strum_angles[i] = float(angles[i])
                if delay > 0:
                    time.sleep(delay)
                return
            except Exception as e:
                _log.debug("二进制帧发送失败: %s，回退逐通道 ASCII", e)

        # ── ASCII 回退 ──
        if not self._ascii_fallback_safe():
            _log.error("二进制帧不可用且检测到 V6.0 板：ASCII 回退通道表为 V5.1，"
                       "已拒绝执行以免驱动错误插座（请先排查 FrameLayer）")
            return
        for i, angle in enumerate(angles):
            if angle is not None:
                angle = max(STRUM_ANGLE_MIN, min(STRUM_ANGLE_MAX, angle))
                self._set_channel(*self._strum_channels[i], angle)
                if angle > 0:
                    self._current_strum_angles[i] = float(angle)
        if delay > 0:
            time.sleep(delay)

    # ========== 和弦与拨弦执行 ==========

    def set_chord(self, frets: List[int]):
        zero      = 90
        k         = [0, 2, 4, 6, 8, 10]
        angles_12 = [0.0] * 12
        for x in range(6):
            s0, i0, s1, i1 = self._FRET_OFFSETS.get(frets[x], (0, None, 0, None))
            # ---- 弦4/5/6 (x>=3) 舵机机械方向反转，1↔2品、3↔4品交换 ----
            if x >= 3:
                # (s0,i0,s1,i1) 四元组完整替换，保留 ch0/ch1 归属
                _swap = {
                    1: (-1, 1,  0, None),   # 用2品配置 (ch0)
                    2: (+1, 0,  0, None),   # 用1品配置 (ch0)
                    3: ( 0, None, -1, 3),   # 用4品配置 (ch1)
                    4: ( 0, None, +1, 2),   # 用3品配置 (ch1)
                }
                if frets[x] in _swap:
                    s0, i0, s1, i1 = _swap[frets[x]]
            # ------------------------------------------------------------
            base0 = zero + self.null_offset[k[x]]
            base1 = zero + self.null_offset[k[x] + 1]
            angles_12[k[x]]     = base0 + (s0 * self._press_offsets[x][i0] if i0 is not None else 0)
            angles_12[k[x] + 1] = base1 + (s1 * self._press_offsets[x][i1] if i1 is not None else 0)
        self._update_chord_channels(angles_12)

    def set_strum(self, strums: List[int]):
        strum_angles = [None] * 6
        for x in range(6):
            if strums[x] == 1:
                strum_angles[x] = self._strum_angles[x][self._strum_flip_state[x]]
                self._strum_flip_state[x] ^= 1   # 0→1 或 1→0
        self._update_strum_channels(strum_angles)

    # ========== V4 预计算接口 (供 ESP32 快速传输) ==========

    def _compute_chord_angles_only(self, frets: List[int]) -> List[int]:
        """仅计算12和弦角度，不发串口。
        逻辑与 ESP32 chord_engine.cpp compute_angles() 完全一致。"""
        zero = 90
        k = [0, 2, 4, 6, 8, 10]
        angles = [0.0] * 12

        for x in range(6):
            f = max(0, min(4, frets[x]))
            s0, i0, s1, i1 = self._FRET_OFFSETS[f]

            # 弦4/5/6 机械反转
            if x >= 3:
                _swap = {
                    1: (-1, 1,  0, None),
                    2: (+1, 0,  0, None),
                    3: ( 0, None, -1, 3),
                    4: ( 0, None, +1, 2),
                }
                if f in _swap:
                    s0, i0, s1, i1 = _swap[f]

            base0 = zero + self.null_offset[k[x]]
            base1 = zero + self.null_offset[k[x] + 1]
            angles[k[x]]     = base0 + (s0 * self._press_offsets[x][i0] if i0 is not None else 0)
            angles[k[x] + 1] = base1 + (s1 * self._press_offsets[x][i1] if i1 is not None else 0)

        return [max(0, min(180, int(round(a)))) for a in angles]

    def _compute_strum_angles_only(self, strums: List[bool],
                                    flip_state: List[int]) -> List[int]:
        """仅计算6拨弦角度，更新 flip_state。不发串口。
        逻辑与 ESP32 chord_engine.cpp compute_strum() 完全一致。
        ★ 钳位到 [STRUM_ANGLE_MIN, STRUM_ANGLE_MAX]，避免超出 ESP32 安全限位被静默拒绝。"""
        angles = [0] * 6
        for x in range(6):
            if strums[x]:
                a = self._strum_angles[x][flip_state[x]]
                angles[x] = int(max(STRUM_ANGLE_MIN, min(STRUM_ANGLE_MAX, int(round(a)))))
                flip_state[x] ^= 1
        return angles

    # ========== V4 和弦序列优化 ==========

    @staticmethod
    def _fret_to_servo_group(fret: int) -> Optional[int]:
        """品位→舵机组：0=ch0(1-2品), 1=ch1(3-4品), None=空弦。"""
        if fret == 0:
            return None
        elif fret in (1, 2):
            return 0
        elif fret in (3, 4):
            return 1
        return None

    def _optimize_chord_sequence(self, note_data: List):
        """对预计算音符序列应用和弦优化（原地修改 chord_ang）。

        note_data: List of [frets, strums, wait_ms, sweep_dir, chord_ang, strum_ang]

        优化一：空闲弦提前定位
          当前音符该弦不拨弦且品位为0(空弦)时，向后查找未来需要该弦的音符，
          提前将该弦的和弦舵机移动到目标位置。

        优化二：同弦不同舵机组预压
          同一弦相邻两个音符都要拨弦，且品位由不同舵机组负责时，
          对空闲舵机组进行部分预压（ch0=20%, ch1=30%全压行程）。
        """
        n = len(note_data)
        if n == 0:
            return

        k = [0, 2, 4, 6, 8, 10]  # ch0 索引
        LOOK_AHEAD_MAX = 3
        PRE_PRESS_CH0 = 0.20  # ch0 预压比例 (1-2品)，按压角度的 1/5
        PRE_PRESS_CH1 = 0.20  # ch1 预压比例 (3-4品)，按压角度的 1/5

        def _base_angle(string_idx: int, ch: int) -> float:
            """舵机中性角 = 90 + null_offset。"""
            return 90.0 + self.null_offset[k[string_idx] + ch]

        def _compute_target_angles(string_idx: int, target_fret: int):
            """计算完整按压目标角度 (ch0_ang, ch1_ang)。"""
            if target_fret == 0:
                return int(round(_base_angle(string_idx, 0))), \
                       int(round(_base_angle(string_idx, 1)))

            f = target_fret
            s0, i0, s1, i1 = self._FRET_OFFSETS[f]

            if string_idx >= 3:
                _swap = {1: (-1,1,0,None), 2: (+1,0,0,None),
                         3: (0,None,-1,3), 4: (0,None,+1,2)}
                if f in _swap:
                    s0, i0, s1, i1 = _swap[f]

            base0 = _base_angle(string_idx, 0)
            base1 = _base_angle(string_idx, 1)
            a0 = base0 + (s0 * self._press_offsets[string_idx][i0] if i0 is not None else 0)
            a1 = base1 + (s1 * self._press_offsets[string_idx][i1] if i1 is not None else 0)
            return max(0, min(180, int(round(a0)))), max(0, min(180, int(round(a1))))

        # ── 优化一：空闲弦提前定位 ──
        for i in range(n):
            frets_i, strums_i, _wait_i, _swp_i, chord_ang_i, _ = note_data[i]
            for s in range(6):
                if strums_i[s] == 0 and frets_i[s] == 0:
                    for j in range(i + 1, min(i + 1 + LOOK_AHEAD_MAX, n)):
                        frets_j, strums_j, _, _, _, _ = note_data[j]
                        if strums_j[s] > 0 and frets_j[s] != 0:
                            new_ch0, new_ch1 = _compute_target_angles(s, frets_j[s])
                            chord_ang_i[k[s]] = new_ch0
                            chord_ang_i[k[s] + 1] = new_ch1
                            break

        # ── 优化二：同弦不同舵机组预压 ──
        for i in range(n - 1):
            frets_i, strums_i, _wait_i, _swp_i, chord_ang_i, _ = note_data[i]
            for s in range(6):
                if strums_i[s] == 0:
                    continue
                frets_next, strums_next, _, _, _, _ = note_data[i + 1]
                if strums_next[s] == 0:
                    continue

                cur_servo = self._fret_to_servo_group(frets_i[s])
                next_servo = self._fret_to_servo_group(frets_next[s])
                next_fret = frets_next[s]

                if next_servo is None or next_fret == 0:
                    continue

                # 确定预压比例和需要预压的舵机组
                if cur_servo is None:
                    # 空弦→按品：空闲舵机预压到下一品目标
                    # 舵机完全空闲，但使用保守比例避免影响当前空弦音色
                    ratio = PRE_PRESS_CH0 if next_servo == 0 else PRE_PRESS_CH1
                    target_servo = next_servo
                elif cur_servo != next_servo:
                    # 跨舵机组预压（原有逻辑）
                    ratio = PRE_PRESS_CH0 if next_servo == 0 else PRE_PRESS_CH1
                    target_servo = next_servo
                else:
                    continue  # 同组，无需预压

                # 计算预压量
                f = next_fret
                s0, i0, s1, i1 = self._FRET_OFFSETS[f]

                # Handle string reversal
                _s0, _i0, _s1, _i1 = s0, i0, s1, i1
                if s >= 3:
                    _swap = {1: (-1,1,0,None), 2: (+1,0,0,None),
                             3: (0,None,-1,3), 4: (0,None,+1,2)}
                    if f in _swap:
                        _s0, _i0, _s1, _i1 = _swap[f]

                base_next = _base_angle(s, target_servo)
                if target_servo == 0 and _i0 is not None:
                    press = self._press_offsets[s][_i0]
                    pre_angle = int(round(base_next + _s0 * press * ratio))
                    chord_ang_i[k[s]] = max(0, min(180, pre_angle))
                elif target_servo == 1 and _i1 is not None:
                    press = self._press_offsets[s][_i1]
                    pre_angle = int(round(base_next + _s1 * press * ratio))
                    chord_ang_i[k[s] + 1] = max(0, min(180, pre_angle))

    @staticmethod
    def _parse_csv_header(header: List[str]) -> Tuple[List[int], List[int], int, int, int]:
        """解析 CSV 表头，返回 (col_fret[6], col_region[6], col_dash, col_time_sig, col_bpm)。
        逻辑与 ESP32 csv_parser.cpp 表头解析一致。"""
        col_fret = [-1] * 6
        col_region = [-1] * 6
        col_dash = -1
        col_time_sig = -1
        col_bpm = -1
        for i, h in enumerate(header):
            h = h.strip()
            if '横线' in h:
                col_dash = i
            elif '拍号' in h:
                col_time_sig = i
            elif 'bpm' in h.lower():
                col_bpm = i
            elif h == '1弦': col_fret[0] = i
            elif h == '2弦': col_fret[1] = i
            elif h == '3弦': col_fret[2] = i
            elif h == '4弦': col_fret[3] = i
            elif h == '5弦': col_fret[4] = i
            elif h == '6弦': col_fret[5] = i
            elif h == '区域1': col_region[0] = i
            elif h == '区域2': col_region[1] = i
            elif h == '区域3': col_region[2] = i
            elif h == '区域4': col_region[3] = i
            elif h == '区域5': col_region[4] = i
            elif h == '区域6': col_region[5] = i
        return col_fret, col_region, col_dash, col_time_sig, col_bpm

    def precompute_song(self, csv_path: str) -> Tuple[int, List[bytes], str, int]:
        """解析CSV并预计算所有音符的23字节二进制数据。

        CSV 解析逻辑严格参照 ESP32 csv_parser.cpp:
          - 品位继承: 空值继承上一行
          - 区域解析: 非空=拨弦, 纯数字=覆盖品位, up/delow=扫弦
          - 时值: 以固定 70 BPM 为基准计算 wait_ms（BPM 在 ESP32 端生效）
            dash 0→857ms, 1→428ms, 2→214ms (60000/70/(2^dash))
          - 拍号: 从 CSV 第一行"拍号"列读取 (默认 4/4)
          - BPM: 从 CSV 第一行"BPM"列读取 (默认 70)

        返回: (音符总数, [note_binary_1, note_binary_2, ...], 拍号字符串, BPM值)
        每个 note_binary 23 字节: chord_angles[12] + strum_angles[6] + wait_ms[4 LE] + sweep_dir[1]
        """
        import csv as csv_mod
        import struct

        with open(csv_path, 'r', encoding='utf-8-sig') as f:
            reader = csv_mod.reader(f)
            header = next(reader)
            col_fret, col_region, col_dash, col_time_sig, col_bpm = self._parse_csv_header(header)

            flip_state = [1] * 6       # ★ 首次拨弦走 point_b，与 ESP32 strum_flip_state 初始值一致
            prev_frets = [0] * 6
            note_data = []             # (frets, strums, wait_ms, sweep_dir, chord_ang, strum_ang)

            time_signature = "4/4"  # 默认拍号
            bpm = 70                # 默认 BPM
            _BASE_MS = 60000.0 / 70  # 基准四分音符毫秒数 (70 BPM)
            first_row = True
            for row in reader:
                ncols = len(row)
                # ★ 读取第一行数据行的拍号和 BPM
                if first_row:
                    if col_time_sig >= 0:
                        val = row[col_time_sig].strip() if col_time_sig < ncols else ''
                        if val:
                            time_signature = val
                    if col_bpm >= 0:
                        val = row[col_bpm].strip() if col_bpm < ncols else ''
                        if val and val.isdigit():
                            bpm = max(30, min(300, int(val)))
                first_row = False
                # ① 品位 (空值继承上一行)
                frets = [0] * 6
                has_frets = False
                for j in range(6):
                    ci = col_fret[j]
                    val = row[ci].strip() if 0 <= ci < ncols else ''
                    if val:
                        # 容错: CSV 可能出现 '0.0' 浮点格式（data_merger 已修复，旧文件仍可能残留）
                        try:
                            fret_val = int(float(val))
                        except ValueError:
                            fret_val = prev_frets[j]
                        frets[j] = max(0, min(4, fret_val))
                        has_frets = True
                    else:
                        frets[j] = prev_frets[j]
                if has_frets:
                    prev_frets = list(frets)

                # ② 区域 → 拨弦标志 + 数字品位 + 扫弦方向
                strums = [False] * 6
                is_sweep_up = False
                is_sweep_down = False
                for j in range(6):
                    ci = col_region[j]
                    val = row[ci].strip() if 0 <= ci < ncols else ''
                    if val:
                        strums[j] = True
                        if val.replace('.', '', 1).isdigit():
                            # 容错: CSV 可能出现 '0.0' 浮点格式
                            frets[j] = max(0, min(4, int(float(val))))
                            prev_frets[j] = frets[j]
                        if 'up' in val.lower() or 'UP' in val:
                            is_sweep_up = True
                        if 'delow' in val.lower() or 'DELOW' in val:
                            is_sweep_down = True

                # ③ 时值 (以固定 70 BPM 为基准)
                dash_str = row[col_dash].strip() if 0 <= col_dash < ncols else '0'
                dash_count = int(dash_str) if dash_str.isdigit() else 0
                wait_ms = int(_BASE_MS / (2 ** dash_count))
                # dash 0→857ms, dash 1→428ms, dash 2→214ms, dash 3→107ms

                # ④ 扫弦方向
                if is_sweep_up:
                    sweep_dir = 1
                elif is_sweep_down:
                    sweep_dir = 2
                else:
                    sweep_dir = 0

                # ⑤ 计算角度
                chord_ang = self._compute_chord_angles_only(frets)
                strum_ang = self._compute_strum_angles_only(strums, flip_state)

                # ⑥ 保存中间数据
                note_data.append((list(frets), list(strums), wait_ms, sweep_dir,
                                  list(chord_ang), list(strum_ang)))

        # ★ 对和弦序列应用提前定位优化（修改 chord_ang 到位）
        self._optimize_chord_sequence(note_data)

        # 打包 23 字节
        notes = []
        for _frets, _strums, wait_ms, sweep_dir, chord_ang, strum_ang in note_data:
            notes.append(struct.pack('<12B6BIB',
                *chord_ang, *strum_ang, wait_ms, sweep_dir))

        return len(notes), notes, time_signature, bpm

    # [已删除 test_all_strings() (无进度回调版) — 被 test_all_strings_with_progress() 替代]

    # ---------- 带进度回调的舵机测试程序 ----------
    def test_all_strings_with_progress(self,
                                       progress_callback: Optional[Callable[[float, str], None]] = None,
                                       stop_event: Optional[threading.Event] = None,
                                       chord_delay: float = 0.9,
                                       strum_delay: float = 0.4):
        """
        全弦全品位测试，提供进度回调和停止事件支持。
        6弦 × 5品 = 30步测试。
        chord_delay: 和弦→拨弦等待时间 (秒)
        strum_delay: 拨弦→下一个和弦等待时间 (秒)
        """
        # 开启调试日志，记录所有舵机命令以便诊断
        ServoController._debug_all_commands = True
        try:
            total_steps = 6 * 5
            current_step = 0
            for KK in range(6):
                if stop_event and stop_event.is_set():
                    break
                SS = [0] * 6
                DD = [0] * 6
                SS[KK] = 1
                for LL in range(5):
                    if stop_event and stop_event.is_set():
                        break
                    DD[KK] = LL
                    _log.info("测试步骤 %d/30: 弦%d 品%d DD=%s",
                              current_step + 1, KK + 1, LL + 1, str(DD))
                    self.set_chord(DD)
                    time.sleep(chord_delay)
                    self.set_strum(SS)
                    time.sleep(strum_delay)
                    current_step += 1
                    if progress_callback:
                        progress = (current_step / total_steps) * 100
                        status = f"第{KK+1}弦 第{LL+1}品"
                        progress_callback(progress, status)
            # ★ V5: 复位并失能 — 和弦→90°, 拨弦→point_a, 发送 DISABLE
            # 修复: 测试程序结束原来不发 DISABLE, 舵机保持使能带电 (费电+保持力矩)
            # reset_and_idle() 内部含 0.3s 等待 + 发 DISABLE 帧 (触发 ESP32 DISSEQ 完整失能序列)
            self.reset_and_idle()
            if progress_callback and not (stop_event and stop_event.is_set()):
                progress_callback(100.0, "测试完成")
        finally:
            ServoController._debug_all_commands = False

    def _reset_strum_to_idle(self, safe_mode: bool = False):
        """将所有拨弦舵机归位。safe_mode=True 时统一回到 90° 中性位。"""
        if safe_mode:
            self._update_strum_channels([90.0] * 6)
            self._current_strum_angles = [90.0] * 6
        else:
            idle_angles = [self._strum_angles[i][0] for i in range(6)]
            self._update_strum_channels(idle_angles)
            self._current_strum_angles = [float(a) for a in idle_angles]

    @staticmethod
    def _esp32_board_id():
        """从 esp32_player 单例读取硬件版本号（未连接/未上报时返回 None）。"""
        try:
            import esp32_player as _ep
            p = getattr(_ep, "_player", None)
            info = getattr(p, "hw_info", None) if p is not None else None
            return int(info.get("board_id")) if info else None
        except Exception:
            return None

    @staticmethod
    def _esp32_in_safe_mode() -> bool:
        """ESP32 是否处于 safe mode（硬件版本未确认 → 固件已物理切断舵机输出）。"""
        try:
            import esp32_player as _ep
            p = getattr(_ep, "_player", None)
            return bool(p is not None and getattr(p, "safe_mode", False))
        except Exception:
            return False

    def _ascii_fallback_safe(self) -> bool:
        """ASCII 逐通道回退是否安全。

        回退走 _set_channel -> NORMAL_SERVO_SINGLE，而 SINGLE 是裸通道号、
        不经过固件的 profile 映射；回退用的通道表是写死的 V5.1 表
        （内置默认或 servo_config.csv）。V6.0 板的拨弦 6 路与 2 路和弦通道
        都不同，在 V6.0 上继续回退会驱动错误的插座，因此拒绝。
        """
        return self._esp32_board_id() != 2

    def _send_servo_ctrl(self, command: int):
        """发送舵机电源控制帧到 ESP32（V3 二进制协议）。
        command: ServoCtrlType (0x01=ENABLE, 0x02=DISABLE, 0x03=RESET_AND_IDLE)
        """
        # ★ v2.8.0 safe mode 联锁：固件在硬件版本未确认时会物理切断舵机输出，
        #   此时下发 ENABLE 只会被拒绝并刷屏，主机侧提前拦住。
        if command == 0x01 and self._esp32_in_safe_mode():
            _log.warning("ESP32 处于 SAFE MODE（硬件版本未确认，输出已切断），拒绝下发 ENABLE")
            return
        fl = self._get_frame_layer()
        if fl is not None:
            try:
                from frame_codec import make_servo_ctrl
                fl.send_frame(make_servo_ctrl(command))
                _log.debug("servo_ctrl 已发送: 0x%02X", command)
            except Exception as e:
                _log.warning("servo_ctrl 发送失败: %s", e)

    def disable_all_servos(self):
        """失能所有舵机（释放保持力矩）。先发送 BATCH 帧将拨弦归位到 point_a。"""
        self._reset_strum_to_idle(safe_mode=False)
        time.sleep(0.3)  # 等待舵机移动到 point_a
        self._send_servo_ctrl(0x02)  # DISABLE
        _log.info("拨弦已归位 point_a → 舵机失能")

    def reset_and_idle(self):
        """复位并失能：和弦→90°，拨弦→point_a，然后失能。
        比 disable_all_servos 多了一步和弦回中，适用于校准/配置同步完成后的完整归位。"""
        chord_angles = [90] * 12
        self._update_chord_channels(chord_angles)
        self._reset_strum_to_idle(safe_mode=False)
        time.sleep(0.3)
        self._send_servo_ctrl(0x02)  # DISABLE (V4: ESP32 不再有 SERVO_CMD_RESET)
        _log.info("舵机完整归位: 和弦→90°, 拨弦→point_a, 已失能")

    def cleanup(self):
        """释放所有舵机资源（串口由 SerialBus 统一管理，不在此关闭）。"""
        try:
            chord_angles = [90] * 12
            self._update_chord_channels(chord_angles)
            self._reset_strum_to_idle(safe_mode=False)  # 与其他路径对齐，拨弦归 point_a
            self._send_servo_ctrl(0x02)  # DISABLE
        except Exception:
            pass
        _log.info("舵机资源已释放")


if __name__ == "__main__":
    controller = ServoController(tune_ripple_threshold=0.1)  # 使用默认 config/ 路径
    try:
        _log.info("*** 开始和弦舵机校准 ***")
        controller.calibrate_all_chord_servos(
            stable_duration=1.0,
            save_to_file=True,
        )
    finally:
        controller.cleanup()
