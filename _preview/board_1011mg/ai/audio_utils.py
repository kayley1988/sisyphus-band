"""
音频音量控制工具（软件层）
==========================
Rockchip RK809 无硬件音量控件，改用软件方案：
- 麦克风：音频采样增益系数 + AGC（自动增益控制）
- 扬声器：播放器音量参数（传给 mpg123/ffplay/espeak-ng）
"""
import subprocess
import numpy as np
from logger import get_logger

_log = get_logger("ai")

# ── 模块级音量状态 ──
_mic_gain = 1.5       # 麦克风增益系数 (1.0 ~ 5.0)
_speaker_volume = 80  # 扬声器音量百分比 (0 ~ 100)


def get_mic_gain():
    """获取当前麦克风软件增益系数"""
    return _mic_gain


def set_mic_gain(factor):
    """设置麦克风软件增益系数 (1.0 ~ 5.0)"""
    global _mic_gain
    _mic_gain = max(1.0, min(5.0, factor))
    _log.info("麦克风增益: %.1fx", _mic_gain)


def get_speaker_volume():
    """获取当前扬声器音量百分比 (0-100)"""
    return _speaker_volume


def set_speaker_volume(percent):
    """设置扬声器音量百分比 (0-100)"""
    global _speaker_volume
    _speaker_volume = max(0, min(100, percent))
    _log.info("扬声器音量: %d%%", _speaker_volume)


# ── ALSA 诊断（仅供参考，不用于音量控制）──
def print_alsa_info():
    """打印 ALSA 声卡控件信息（诊断用）"""
    try:
        result = subprocess.run(
            ["amixer", "-c", "0", "scontrols"],
            capture_output=True, text=True, timeout=5,
        )
        _log.debug("ALSA 控件:\n%s", result.stdout)
    except Exception as e:
        _log.debug("ALSA 诊断失败: %s", e)


# ── 自动增益控制 (AGC) ──

class AGCProcessor:
    """自动增益控制处理器 — 对音频流实时调整增益，补偿音量波动。

    使用方式（逐块处理）::

        agc = AGCProcessor()
        for chunk in audio_stream:
            gained = agc.process(chunk)   # gained 是增益后的 float32 数组

    参数:
        target_rms:   目标 RMS 水平（float32 域），默认 0.08
        attack:       增益上升速度（信号弱时快速放大），默认 0.3
        release:      增益下降速度（信号强时缓慢衰减），默认 0.05
        max_gain:     最大增益倍数，默认 8.0
        initial_gain: 初始增益倍数，默认 2.0（补偿 RK809 偏弱）
    """

    def __init__(self, target_rms=0.08, attack=0.3, release=0.05,
                 max_gain=8.0, initial_gain=2.0):
        self.target_rms = target_rms
        self.attack = attack
        self.release = release
        self.max_gain = max_gain
        self._gain = initial_gain

    def process(self, samples: np.ndarray) -> np.ndarray:
        """对单帧音频应用 AGC 增益，返回增益后的副本。

        Args:
            samples: float32 数组，值域 [-1.0, 1.0]

        Returns:
            增益后的 float32 数组（新副本，不修改输入）
        """
        if samples.dtype != np.float32:
            samples = samples.astype(np.float32)

        # 计算当前帧 RMS
        rms = float(np.sqrt(np.mean(samples ** 2)) + 1e-10)

        # 更新 AGC 增益
        if rms < self.target_rms:
            self._gain += self.attack * (self.target_rms / rms - 1.0)
        else:
            self._gain -= self.release * (rms / self.target_rms - 1.0)
        self._gain = max(1.0, min(self.max_gain, self._gain))

        # 应用手动增益 + AGC 增益
        total_gain = get_mic_gain() * self._gain
        if total_gain != 1.0:
            return samples * total_gain
        return samples.copy()

    @property
    def current_gain(self) -> float:
        """当前 AGC 增益倍数（调试用）"""
        return self._gain
