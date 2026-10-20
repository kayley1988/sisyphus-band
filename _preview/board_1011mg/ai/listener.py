"""
唤醒监听器 — 持续监听音频流并检测唤醒词

WakeListener 类:
  - 使用 sounddevice 库实时采集麦克风音频
  - 将音频块喂入 WakeWordEngine 进行唤醒词检测
  - 支持两种唤醒模式:
    * voice:  语音唤醒 (连续检测，自动触发)
    * button: 按键说话 (按住 SET 键录音，松开识别)
  - 检测到唤醒词后触发回调，启动 ASR → Chat → TTS 流水线

音频参数:
  - 采样率: 16000Hz (KWS 模型要求)
  - 帧长: KWS_CHUNK (512 采样点 ≈ 32ms)
  - 声道: 单声道
"""
import time
import queue
import numpy as np
import sounddevice as sd
from logger import get_logger

_log = get_logger("ai")

from .constants import SAMPLE_RATE, KWS_CHUNK


class WakeListener:
    """唤醒监听器，在后台音频流中检测唤醒词（含 AGC 自动增益控制）"""

    def __init__(self, kws_engine, device_index=None, sample_rate=SAMPLE_RATE, chunk=KWS_CHUNK):
        self.kws = kws_engine
        self.sample_rate = sample_rate
        self.chunk = chunk
        self.device_index = device_index
        self.q = queue.Queue()
        self.stream = None
        self.last_callback_time = time.time()
        # AGC 处理器 — 唤醒词检测需要高增益（RK809 偏弱，远距离补偿）
        # ASR 路径使用默认参数，避免语音失真
        from .audio_utils import AGCProcessor
        self.agc = AGCProcessor(initial_gain=5.0, max_gain=25.0)

    def _callback(self, indata, frames, time_info, status):
        if status:
            if not getattr(self, '_status_printed', False):
                _log.debug("音频回调状态: %s", status)
                self._status_printed = True

        # 预分配/复用 float32 缓冲区
        if not hasattr(self, '_float_buf') or len(self._float_buf) != frames:
            self._float_buf = np.empty(frames, dtype=np.float32)
        np.divide(indata[:, 0], 32768.0, out=self._float_buf)

        # ── AGC：自动增益控制 ──
        gained = self.agc.process(self._float_buf)
        self.q.put(gained)
        self.last_callback_time = time.time()

    def start(self):
        try:
            self.stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype='int16',
                blocksize=self.chunk,
                device=self.device_index,
                callback=self._callback,
            )
            self.stream.start()
            _log.info("唤醒监听流已启动")
            # 启动后采集 1 秒环境音，诊断麦克风电平
            self._diagnose_mic_level()
        except Exception as e:
            _log.error("无法打开音频流: %s", e)
            return False
        return True

    def _diagnose_mic_level(self):
        """采集 1 秒环境音，报告麦克风电平（帮助诊断硬件问题）。

        在 INFO 日志中输出平均幅度和峰值，并给出诊断建议。
        """
        samples_list = []
        t0 = time.time()
        while time.time() - t0 < 1.0:
            try:
                s = self.q.get(timeout=0.5)
                samples_list.append(s)
            except queue.Empty:
                break
        if not samples_list:
            _log.warning("麦克风诊断: 1 秒内未收到音频数据，请检查麦克风连接")
            return
        all_samples = np.concatenate(samples_list)
        avg = float(np.abs(all_samples).mean() * 32768)
        peak = float(np.abs(all_samples).max() * 32768)
        _log.info("麦克风诊断 — 平均幅度: %.0f, 峰值: %.0f", avg, peak)
        if avg < 200:
            _log.warning(
                "⚠️ 麦克风电平过低 (%.0f)，请检查: "
                "1) 麦克风是否插好  2) 菜单 → AI设置 → 麦克风增益  3) 靠近说话",
                avg,
            )
        elif avg < 500:
            _log.info("麦克风电平偏低 (%.0f)，建议在菜单中调高麦克风增益", avg)
        else:
            _log.info("麦克风电平正常 (%.0f)", avg)

    def stop(self):
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None

    def wait_for_keyword(self, timeout=None):
        if not self.stream:
            return None
        _log.info("等待唤醒词（'%s'）...", self.kws.wake_word)
        start = time.time()
        last_debug = time.time()
        while timeout is None or (time.time() - start < timeout):
            if time.time() - self.last_callback_time > 2.0:
                _log.warning("音频回调超过2秒无数据，请检查麦克风")
            try:
                samples = self.q.get(timeout=0.5)
            except queue.Empty:
                continue
            keyword = self.kws.process(samples)
            if keyword:
                _log.info("检测到唤醒词: %s", keyword)
                return keyword
            if time.time() - last_debug > 1.0:
                avg_energy = np.abs(samples).mean() * 32768
                _log.debug("麦克风幅度值: %.1f (正常语音 500~20000, 静音 < 100)", avg_energy)
                last_debug = time.time()
        _log.debug("唤醒等待超时")
        return None
