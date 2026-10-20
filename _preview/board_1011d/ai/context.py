"""
AI 上下文管理 + 后台 AI 主循环线程 (687 行核心模块)

AIContext 类:
  - AI 系统的全局上下文容器
  - 线程安全的配置/状态/控制信号管理
  - 包含: 唤醒词配置、ASR 引擎参数、TTS 引擎参数、对话历史
  - 控制信号: active, pause, stop_flag, interrupt_playing, ptt_recording, page_active

ai_thread_main():
  - AI 后台主循环线程入口
  - 管理 WakeListener → VoiceAssistant 的生命周期
  - 处理 restart 信号 (切换唤醒词/ASR引擎/TTS引擎时自动重启)
  - 处理 pause/resume (演奏时暂停 AI 释放音频设备)

控制接口:
  - set_config(key, val)  — 运行时修改配置
  - get_config(key)       — 读取配置
  - set_control(key, val) — 发送控制信号
  - get_control(key)      — 读取控制信号
  - get_status(key)       — 读取运行状态
  - restart()             — 重启 AI 线程
  - start() / stop()      — 启动/停止 AI 系统
"""
import os
import time
import re
import json
import subprocess
import queue
import threading
import sounddevice as sd
from logger import get_logger

_ai_log = get_logger("ai")

from .constants import read_api_key, get_input_device, load_wake_words
from .assistant import VoiceAssistant
from .asr_engine import VoskASR
from .tts_engine import EspeakTTS
from .listener import WakeListener
from .audio_utils import AGCProcessor


# ──────────── AI 配置持久化（断电重启后保持用户设置）────────────
def _ai_config_path() -> str:
    """AI 配置持久化文件: 项目根目录/config/ai_config.json"""
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    return os.path.join(root, "config", "ai_config.json")


def save_ai_config(config: dict):
    """原子写入 AI 配置（临时文件 + rename），失败仅告警不影响运行"""
    try:
        path = _ai_config_path()
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception as e:
        _ai_log.warning("AI 配置持久化失败: %s", e)


def load_ai_config() -> dict:
    """从文件加载 AI 配置，文件缺失/损坏时返回空 dict（使用默认值）"""
    try:
        path = _ai_config_path()
        if not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        _ai_log.warning("AI 配置加载失败（使用默认值）: %s", e)
        return {}


class AIContext:
    """主线程与 AI 线程共享状态（线程安全）"""
    def __init__(self):
        self._lock = threading.Lock()
        self.config = {
            "wake_word": "小爱同学",
            "wake_method": "voice",   # "voice" 或 "button"
            "asr_engine": "whisper",  # "whisper" 或 "vosk"
            "tts_engine": "default",   # "default" 或 "espeak"
            "mic_gain": 1.5,           # 麦克风软件增益 1.0~5.0
            "speaker_volume": 80,      # 扬声器音量 0-100
        }
        # ★ 断电重启后恢复用户设置（Web/OLED 设置的唤醒词等持久化到 config/ai_config.json）
        self.config.update({
            k: v for k, v in load_ai_config().items() if k in self.config
        })
        self.status = {
            "state": "IDLE",          # IDLE, PREPARING, LISTENING, THINKING, SPEAKING, UNAVAILABLE
            "user_text": "",
            "reply_text": "",
            "error": "",
            "available": False,       # AI 是否可用（所有条件满足后设为 True）
            "loading_detail": "",     # 启动加载子阶段描述（如"检查 API Key..."）
            "state_changed_at": 0.0,  # 最后一次状态变化的时间戳 (time.time())
        }
        self.control = {
            "thread": None,
            "stop_flag": False,
            "active": False,
            "interrupt_playing": False,
            "ptt_recording": False,
            "pause": False,
            "page_active": True,   # 主页面是否激活（AI 是否应工作）
        }
        self.display_dirty = False

    # ---------- 线程安全的读写方法 ----------
    def get_config(self, key, default=None):
        with self._lock:
            return self.config.get(key, default)

    def set_config(self, key, value):
        with self._lock:
            self.config[key] = value
            # ★ 持久化: Web/OLED 两条设置入口最终都走 set_config，
            # 统一落盘保证断电重启后保持
            save_ai_config(self.config)

    def get_status(self, key, default=None):
        with self._lock:
            return self.status.get(key, default)

    def set_status(self, key, value):
        with self._lock:
            old = self.status.get(key)
            if old == value:
                return  # 值未变化，跳过，避免不必要的刷新
            self.status[key] = value
            # 自动记录状态变化时间戳（供 OLED 渲染器判断新鲜度）
            if key == "state" or key == "reply_text" or key == "user_text":
                self.status["state_changed_at"] = time.time()
        self.display_dirty = True

    def get_control(self, key, default=None):
        with self._lock:
            return self.control.get(key, default)

    def set_control(self, key, value):
        with self._lock:
            self.control[key] = value

    def get_status_snapshot(self):
        """获取 status 的原子快照，供主线程读取"""
        with self._lock:
            return dict(self.status)

    def get_config_snapshot(self):
        """获取 config 的原子快照"""
        with self._lock:
            return dict(self.config)

    def get_control_snapshot(self):
        """获取 control 的原子快照"""
        with self._lock:
            return dict(self.control)

    def start(self):
        with self._lock:
            if self.control["thread"] and self.control["thread"].is_alive():
                if self.control["stop_flag"]:
                    old_thread = self.control["thread"]
                else:
                    return
            else:
                old_thread = None
            self.control["stop_flag"] = False
            self.control["active"] = False
            self.status["state"] = "IDLE"
        # 锁外 join，避免死锁
        if old_thread and old_thread.is_alive():
            old_thread.join(timeout=10)
        t = threading.Thread(target=ai_thread_main, args=(self,), daemon=True)
        with self._lock:
            self.control["thread"] = t
        t.start()

    def stop(self):
        with self._lock:
            self.control["stop_flag"] = True

    def restart(self):
        self.stop()
        with self._lock:
            old_thread = self.control.get("thread")
        if old_thread and old_thread.is_alive():
            old_thread.join(timeout=10)
        self.start()


def try_parse_play_intent(reply: str):
    """尝试从 DeepSeek 回复中解析演奏意图。

    Args:
        reply: DeepSeek API 返回的完整回复文本

    Returns:
        (is_play: bool, song_name: str | None)
        - is_play=True, song_name="七里香"   → 检测到演奏意图
        - is_play=False, song_name=None     → 正常聊天回复
    """
    import json as _json
    import re as _re

    if not reply:
        return False, None

    text = reply.strip()

    # 1. 直接 JSON 解析
    try:
        data = _json.loads(text)
        if isinstance(data, dict) and data.get("action") == "play" and data.get("song"):
            song = str(data["song"]).strip()
            if song:
                return True, song
    except (_json.JSONDecodeError, ValueError):
        pass

    # 2. 正则提取（容错：DeepSeek 可能在 JSON 外包裹了 markdown 或说明文字）
    m = _re.search(
        r'\{\s*"action"\s*:\s*"play"\s*,\s*"song"\s*:\s*"([^"]+)"\s*\}',
        text
    )
    if m:
        song = m.group(1).strip()
        if song:
            return True, song

    return False, None


def try_parse_download_intent(reply: str):
    """尝试从 DeepSeek 回复中解析下载意图。

    Args:
        reply: DeepSeek API 返回的完整回复文本

    Returns:
        (is_download: bool, song_name: str | None)
    """
    import json as _json
    import re as _re

    if not reply:
        return False, None

    text = reply.strip()

    # 1. 直接 JSON 解析
    try:
        data = _json.loads(text)
        if isinstance(data, dict) and data.get("action") == "download" and data.get("song"):
            song = str(data["song"]).strip()
            if song:
                return True, song
    except (_json.JSONDecodeError, ValueError):
        pass

    # 2. 正则提取（容错）
    m = _re.search(
        r'\{\s*"action"\s*:\s*"download"\s*,\s*"song"\s*:\s*"([^"]+)"\s*\}',
        text
    )
    if m:
        song = m.group(1).strip()
        if song:
            return True, song

    return False, None


def _get_available_songs(songs_dir=None):
    """获取可用歌曲名称列表（读取 歌曲列表/ 目录下的文件夹名）。

    Args:
        songs_dir: 歌曲目录路径，为 None 时自动推导项目根目录

    Returns:
        歌曲名称列表（按字母排序）
    """
    if songs_dir is None:
        _root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))))
        songs_dir = os.path.join(_root, "歌曲列表")
    if not os.path.isdir(songs_dir):
        return []
    return sorted([
        d for d in os.listdir(songs_dir)
        if os.path.isdir(os.path.join(songs_dir, d))
    ])


#: 自检 P4 比对的期望短语（main.py 欢迎语的前缀：≤3s 录音窗口只能覆盖前缀）
VOICE_LOOPBACK_EXPECTED = "欢迎使用自动吉他"

#: 回环 PCM 的峰值归一化目标（可容忍环境变量覆盖，便于现场调参）。
#: ★ 为什么需要它（2026-09-27 实测）：回环的 PCM 是"喇叭→麦克风"**声学**采集的，
#:   实测 `peak=1.0 / clipped=true`（削顶）。而 ASR 的 AGC 只会放大不会衰减
#:   （`ai/audio_utils.py:106` `total_gain = get_mic_gain() * self._gain`，
#:   且 `self._gain = max(1.0, ...)`），削顶的波形再怎么增益都救不回来 ——
#:   归一化把峰值拉回线性区，AGC 才不会继续把它推向饱和。
#: 注意：只作用于**自检用的那份副本**，不改采集、不改配置、不影响正常语音路径。
VOICE_LOOPBACK_PEAK_TARGET = float(os.environ.get("GUITAR_LOOPBACK_PEAK_TARGET",
                                                  "0.35") or 0.35)


def _normalize_loopback_pcm(pcm, target=VOICE_LOOPBACK_PEAK_TARGET):
    """把回环 PCM 的峰值归一化到 ``target``；返回 ``(新数组, 说明)``。

    只降不升（``gain <= 1``）—— 本来就是弱信号时不该再放大，那属于采集侧问题。
    """
    try:
        import numpy as np
        arr = np.asarray(pcm)
        if arr.size == 0 or arr.dtype.kind not in "iu":
            return pcm, {"applied": False, "why": "not_int"}
        peak = float(max(abs(int(arr.min())), abs(int(arr.max())))) / 32768.0
        if peak <= 0.0:
            return pcm, {"applied": False, "why": "silent"}
        gain = min(1.0, float(target) / peak)
        if gain >= 0.999:
            return pcm, {"applied": False, "why": "already_below_target",
                         "peak": round(peak, 4)}
        out = (arr.astype("float32") * gain)
        out = np.clip(out, -32768, 32767).astype("int16")
        return out, {"applied": True, "peak_in": round(peak, 4),
                     "gain": round(gain, 4), "target": float(target)}
    except Exception as exc:                                   # noqa: BLE001
        return pcm, {"applied": False, "why": "error:%s" % str(exc)[:80]}


def _make_voice_loopback_provider(ctx, assistant):
    """构造自检 P4 的 provider：有界录音 + 本地 ASR，**只回分档，不回原文**。

    返回 ``fn(phrase_id) -> dict``（形状见 `boot_selftest.register_voice_loopback_provider`）。
    绝不启动唤醒词会话、绝不播放声音；只在引擎 IDLE 时录一段。
    """

    def _provider(phrase_id, pcm=None):
        started = time.time()
        out = {"bucket": None, "ratio": None, "recognized_chars": None,
               "duration_ms": 0, "reason": None, "status": "INCONCLUSIVE"}
        try:
            state = ctx.get_status("state")
        except Exception:                                # noqa: BLE001
            state = None
        engine = getattr(assistant, "asr_engine", None)
        if engine is None:
            out["status"] = "SKIPPED"
            out["reason"] = "no_asr_engine"
            return out
        if pcm is None and state != "IDLE":
            # 没有缓冲 PCM 只能现场录音 ⇒ 必须等引擎空闲，否则会抢麦克风
            out["status"] = "SKIPPED"
            out["reason"] = "engine_not_idle:%s" % state
            return out
        try:
            if pcm is not None:
                # 欢迎语窗口缓冲下来的 PCM：转写不需要麦克风，也不要求 IDLE
                # ★ 先做峰值归一化：声学回环必然偏响甚至削顶，而 AGC 只放大不衰减
                norm_pcm, norm_info = _normalize_loopback_pcm(pcm)
                out["pcm_norm"] = norm_info
                text = engine.transcribe_pcm(norm_pcm)
                out["window"] = "welcome_pcm"
            else:
                text = engine.listen_and_recognize(silence_sec=0.6, max_sec=3)
                out["window"] = "idle_live"
        except Exception as exc:                         # noqa: BLE001
            out["status"] = "INCONCLUSIVE"
            out["reason"] = "provider_call_error:%s" % exc
            out["duration_ms"] = int((time.time() - started) * 1000)
            return out
        out["duration_ms"] = int((time.time() - started) * 1000)
        err = getattr(engine, "last_error", None)
        if not text:
            if err and str(err).startswith("record_error"):
                out["status"] = "SKIPPED"
                out["reason"] = "mic_busy_or_error"
            else:
                out["status"] = "INCONCLUSIVE"
                out["reason"] = "no_speech"
            return out
        try:
            import boot_selftest as _st
            bucket, ratio = _st.similarity_bucket(text, VOICE_LOOPBACK_EXPECTED)
        except Exception:                                # noqa: BLE001
            bucket, ratio = "UNKNOWN", 0.0
        out["bucket"] = bucket
        out["ratio"] = ratio
        out["recognized_chars"] = len(text)
        out["status"] = "PASS"
        return out

    return _provider


def ai_thread_main(ctx):
    """AI 对话主循环，在后台线程中运行"""

    # 标记 AI 正在加载，主屏幕据此显示加载动画
    ctx.set_status("state", "LOADING")
    errors = []

    # ═══════════════════════════════════════════════════
    #  阶段 1: 逐项检查所有依赖条件（一次性收集所有错误）
    # ═══════════════════════════════════════════════════

    # ── 检查 1: DeepSeek API Key ──
    print("[AI] 检查 DeepSeek API Key...")
    ctx.set_status("loading_detail", "检查 API Key...")
    api_key = read_api_key()
    if not api_key:
        msg = "未配置 DeepSeek API Key（请在 config/deepseek_api.key 中填写或设置环境变量 DEEPSEEK_API_KEY）"
        print(f"[AI] ❌ {msg}")
        errors.append(msg)
    else:
        print("[AI] ✅ API Key 已配置")

    # ── 检查 2: 麦克风设备 ──
    print("[AI] 检查麦克风设备...")
    ctx.set_status("loading_detail", "检查麦克风设备...")
    input_device = get_input_device()
    if input_device is None:
        msg = "未找到可用的麦克风设备"
        print(f"[AI] ❌ {msg}")
        errors.append(msg)
    else:
        print(f"[AI] ✅ 麦克风设备索引: {input_device}")

    # ── 检查 3: 识别引擎模型文件 ──
    #   ★ 无障碍改造：asr_engine="vosk" 时走 Vosk 离线识别 —— 不需要
    #     Whisper RKNN 模型，也不需要 torch/rknn 两个装不上的重依赖，
    #     故此项改为按引擎分派检查。
    _asr_engine = ctx.get_config("asr_engine") or "whisper"
    model_files = []
    if _asr_engine == "vosk":
        ctx.set_status("loading_detail", "检查 Vosk 中文模型...")
        from .constants import VOSK_MODEL_PATH
        print("[AI] 检查 Vosk 中文模型...")
        if not os.path.exists(VOSK_MODEL_PATH):
            msg = f"缺少 Vosk 中文模型（{VOSK_MODEL_PATH}）"
            print(f"[AI] ❌ {msg}")
            errors.append(msg)
        else:
            print(f"[AI] ✅ Vosk 中文模型已找到: {VOSK_MODEL_PATH}")
    else:
        ctx.set_status("loading_detail", "检查 Whisper 模型文件...")
        from .constants import MODEL_DIR
        whisper_dir = os.path.join(MODEL_DIR, "whisper")
        model_files = [
            (os.path.join(whisper_dir, "whisper_encoder_base_20s.rknn"), "Whisper 编码器模型"),
            (os.path.join(whisper_dir, "whisper_decoder_base_20s.rknn"), "Whisper 解码器模型"),
            (os.path.join(whisper_dir, "vocab_zh.txt"),              "Whisper 中文词表"),
            (os.path.join(whisper_dir, "mel_80_filters.txt"),        "Whisper 梅尔滤波器"),
        ]
        print("[AI] 检查 Whisper 模型文件...")
        for path, name in model_files:
            if not os.path.exists(path):
                msg = f"缺少 {name}（{path}）"
                print(f"[AI] ❌ {msg}")
                errors.append(msg)
            else:
                print(f"[AI] ✅ {name} 已找到")

    # ═══════════════════════════════════════════════════
    #  阶段 2: 汇总判断 — 任一条件不满足则退出
    # ═══════════════════════════════════════════════════
    if errors:
        ctx.set_status("state", "UNAVAILABLE")
        ctx.set_status("available", False)
        ctx.set_status("error", "; ".join(errors))
        _ai_log.warning("AI 不可用: %s", "; ".join(errors))
        print(f"[AI] ⛔ 语音助手不可用，共 {len(errors)} 项缺失:")
        for e in errors:
            print(f"[AI]    - {e}")
        print("[AI] ℹ️  其他系统功能不受影响，可正常使用。")
        return

    # ═══════════════════════════════════════════════════
    #  阶段 3: 全部通过，初始化 VoiceAssistant
    # ═══════════════════════════════════════════════════
    assistant = None
    listener = None
    try:
        ctx.set_status("loading_detail", "加载唤醒词模型...")
        _extra = {}
        if _asr_engine != "vosk":
            _extra = dict(
                encoder_model_path=model_files[0][0],
                decoder_model_path=model_files[1][0],
                vocab_path=model_files[2][0],
                mel_filters_path=model_files[3][0],
                whisper_target='rk3566',
                whisper_device_id=None,
                whisper_task='zh',
                whisper_task_code=50260,
            )
            print("[AI] 初始化语音助手（加载唤醒词+Whisper 模型，约 10-30 秒）...")
        else:
            print("[AI] 初始化语音助手（加载唤醒词 + Vosk 离线识别）...")
        assistant = VoiceAssistant(
            wake_word=ctx.get_config("wake_word"),
            api_key=api_key,
            input_device=input_device,
            asr_engine=_asr_engine,
            **_extra,
        )
        ctx.set_status("loading_detail", "语音助手初始化完成")
        print("[AI] ✅ 语音助手初始化完成")

        # ── 开机自检 P4：欢迎语回环 provider（**默认关闭**，见 boot_selftest） ──
        #   ★ 不注册时行为与之前完全一致：不开麦克风、不加载任何东西。
        #   ★ 引擎是**常驻**的 `assistant.asr_engine`，但 `assistant` 是本函数的局部
        #     变量，外部拿不到 ⇒ 只能在这里注册（这正是之前 P4 只能是占位的根因）。
        try:
            import boot_selftest as _st
            if _st.voice_loopback_enabled():
                _st.register_voice_loopback_provider(
                    _make_voice_loopback_provider(ctx, assistant))
                print("[AI] ✅ 已注册开机自检 P4 回环 provider")
        except Exception as _exc:                       # noqa: BLE001
            print("[AI] 注册 P4 回环 provider 失败（不影响语音功能）: %s" % _exc)

        # ── 初始化歌曲列表（让 LLM 知道有哪些歌可点）──
        song_list = _get_available_songs()
        assistant.chat_engine.update_song_list(song_list)
        print(f"[AI] ✅ 歌曲列表已注入 LLM 提示词: {len(song_list)} 首")
    except Exception as e:
        msg = f"助手初始化异常: {e}"
        print(f"[AI] ❌ {msg}")
        ctx.set_status("state", "UNAVAILABLE")
        ctx.set_status("available", False)
        ctx.set_status("error", msg)
        return

    ctx.set_status("available", True)
    ctx.set_status("error", "")
    ctx.set_control("active", True)

    # ── 应用软件音量设置 ──
    try:
        from . import audio_utils
        mic_gain = ctx.get_config("mic_gain", 1.5)
        spk_vol = ctx.get_config("speaker_volume", 80)
        audio_utils.set_mic_gain(mic_gain)
        audio_utils.set_speaker_volume(spk_vol)
        print(f"[AI] 音量已应用: 麦克风增益={mic_gain:.1f}x, 扬声器={spk_vol}%")
    except Exception as e:
        print(f"[AI] 音量应用失败: {e}")

    current_wake_method = ctx.get_config("wake_method")

    # ---- 按需初始化 Vosk / eSpeak ----
    vosk_asr = None
    espeak_tts = None

    def get_vosk():
        nonlocal vosk_asr
        if vosk_asr is None:
            try:
                vosk_asr = VoskASR()
                print("[AI] Vosk ASR 引擎已加载")
            except Exception as e:
                print(f"[AI] Vosk 初始化失败: {e}")
                vosk_asr = None
        return vosk_asr

    def get_espeak():
        nonlocal espeak_tts
        if espeak_tts is None:
            try:
                espeak_tts = EspeakTTS()
                print("[AI] eSpeak TTS 引擎已加载")
            except Exception as e:
                print(f"[AI] eSpeak 初始化失败: {e}")
                espeak_tts = None
        return espeak_tts

    def tts_speak(text: str):
        engine = ctx.get_config("tts_engine")
        if engine == "espeak":
            tts = get_espeak()
            if tts:
                tts.speak(text)
            else:
                try:
                    assistant.tts_engine.speak(text)
                except Exception:
                    pass
        else:
            try:
                assistant.tts_engine.speak(text)
            except Exception:
                pass

    def tts_play_wake_sound():
        engine = ctx.get_config("tts_engine")
        if engine == "espeak":
            tts = get_espeak()
            if tts:
                tts.play_wake_sound()
            else:
                try:
                    assistant.tts_engine.play_wake_sound()
                except Exception:
                    pass
        else:
            try:
                assistant.tts_engine.play_wake_sound()
            except Exception:
                pass

    def _play_beep():
        """播放短提示音（~100ms），用于即时唤醒反馈"""
        try:
            import numpy as np
            sr = 16000
            duration = 0.1
            freq = 800
            t = np.linspace(0, duration, int(sr * duration), False)
            tone = (np.sin(2 * np.pi * freq * t) * 0.3).astype(np.float32)
            sd.play(tone, sr)
            sd.wait()
        except Exception:
            pass

    def asr_listen(timeout=8.0, on_ready=None, on_inference_start=None):
        engine = ctx.get_config("asr_engine")
        if engine == "vosk":
            v = get_vosk()
            if v:
                try:
                    q = queue.Queue()
                    # AGC 处理器实例（与 WakeListener/ASREngine 保持一致的增益体验）
                    vosk_agc = AGCProcessor()
                    def callback(indata, frames, time_info, status):
                        # ── AGC 增益处理 ──
                        data_float = indata.ravel().astype(np.float32) / 32768.0
                        gained = vosk_agc.process(data_float)
                        data_int16 = (gained * 32768.0).clip(-32768, 32767).astype(np.int16)
                        q.put(bytes(data_int16))
                    with sd.RawInputStream(samplerate=16000, blocksize=4000,
                                           dtype='int16', channels=1, callback=callback):
                        if on_ready:
                            try:
                                on_ready()
                            except Exception:
                                pass

                        def audio_gen():
                            start = time.time()
                            while time.time() - start < timeout:
                                try:
                                    yield q.get(timeout=0.1)
                                except queue.Empty:
                                    continue
                        # Vosk 流式模式：实时上报部分识别结果到 OLED
                        # Vosk 流式部分识别结果：仅文本变化时才更新，避免连续相同内容刷屏
                        _last_partial = [None]
                        def _on_partial(text):
                            if text != _last_partial[0]:
                                _last_partial[0] = text
                                ctx.set_status("user_text", text)
                        return v.listen_streaming(
                            audio_gen(), timeout=timeout,
                            on_partial=_on_partial,
                        )
                except Exception as e:
                    print(f"[AI] Vosk 录音异常: {e}")
                    return None
            else:
                if on_ready:
                    try:
                        on_ready()
                    except Exception:
                        pass
                try:
                    return assistant.listen_command(
                        on_inference_start=on_inference_start,
                    )
                except Exception:
                    return None
        else:
            if on_ready:
                try:
                    on_ready()
                except Exception:
                    pass
            try:
                return assistant.listen_command(
                    on_inference_start=on_inference_start,
                )
            except Exception:
                return None

    def asr_listen_ptt(on_ready=None, max_seconds=30.0):
        engine = ctx.get_config("asr_engine")
        if engine == "vosk":
            v = get_vosk()
            if v:
                try:
                    q = queue.Queue()
                    # AGC 处理器实例（PTT 模式也享受一致的增益体验）
                    vosk_agc_ptt = AGCProcessor()

                    def callback(indata, frames, time_info, status):
                        # ── AGC 增益处理 ──
                        data_float = indata.ravel().astype(np.float32) / 32768.0
                        gained = vosk_agc_ptt.process(data_float)
                        data_int16 = (gained * 32768.0).clip(-32768, 32767).astype(np.int16)
                        q.put(bytes(data_int16))

                    with sd.RawInputStream(samplerate=16000, blocksize=4000,
                                           dtype='int16', channels=1, callback=callback):
                        if on_ready:
                            try:
                                on_ready()
                            except Exception:
                                pass
                        chunks = []
                        start = time.time()
                        while (ctx.get_control("ptt_recording", False)
                               and not ctx.get_control("stop_flag", False)
                               and time.time() - start < max_seconds):
                            try:
                                chunks.append(q.get(timeout=0.1))
                            except queue.Empty:
                                continue
                        if not chunks:
                            return None

                        def gen():
                            for c in chunks:
                                yield c
                        return v.listen_once(gen(), timeout=max_seconds)
                except Exception as e:
                    print(f"[AI] PTT Vosk 录音异常: {e}")
                    return None
            else:
                if on_ready:
                    try:
                        on_ready()
                    except Exception:
                        pass
                try:
                    return assistant.listen_command()
                except Exception:
                    return None
        else:
            if on_ready:
                try:
                    on_ready()
                except Exception:
                    pass
            try:
                return assistant.listen_command()
            except Exception:
                return None

    # ---- 引擎预热 ----
    try:
        if ctx.get_config("asr_engine") == "vosk":
            get_vosk()
        if ctx.get_config("tts_engine") == "espeak":
            get_espeak()
    except Exception as e:
        print(f"[AI] 引擎预热异常: {e}")

    # ---- 主循环 ----
    _chat_future = None       # 跟踪后台对话线程，用于超时清理
    _last_song_check = 0      # 上次检查歌曲列表变化的时间戳
    try:
        while not ctx.get_control("stop_flag", False):
            # ── 页面激活检查：只在主页面时运行 AI ──
            if not ctx.get_control("page_active", True):
                if listener:
                    try:
                        listener.stop()
                    except Exception:
                        pass
                    listener = None
                ctx.set_status("state", "IDLE")
                ctx.set_control("ptt_recording", False)
                current_wake_method = None  # 强制下次激活时重建 listener
                time.sleep(0.3)
                continue

            # 仅在状态变化时更新，避免无效刷屏
            if ctx.get_status("state") != "IDLE":
                ctx.set_status("state", "IDLE")
            if ctx.get_status("reply_text") != "":
                ctx.set_status("reply_text", "")
            if ctx.get_status("user_text") != "":
                ctx.set_status("user_text", "")
            ctx.set_control("ptt_recording", False)
            wake_method = ctx.get_config("wake_method")

            # ── 定期刷新歌曲列表（每 60 秒检测一次，新下载的歌会自动加入 LLM 提示词）──
            if time.time() - _last_song_check > 60:
                current_songs = _get_available_songs()
                if set(current_songs) != set(getattr(assistant.chat_engine, '_song_list', [])):
                    assistant.chat_engine.update_song_list(current_songs)
                    print(f"[AI] 歌曲列表已更新: {len(current_songs)} 首")
                _last_song_check = time.time()

            if wake_method != current_wake_method or listener is None:
                if listener:
                    try:
                        listener.stop()
                    except Exception:
                        pass
                    listener = None
                current_wake_method = wake_method

            # ── 等待开机启动完成（避免欢迎语 TTS 被麦克风录入触发误唤醒）──
            if not ctx.get_control("boot_complete", False):
                _boot_wait_start = time.time()
                while not ctx.get_control("boot_complete", False):
                    if time.time() - _boot_wait_start > 120:
                        print("[AI] 开机启动等待超时（120s），强制继续")
                        break
                    if ctx.get_control("stop_flag", False):
                        break
                    time.sleep(0.3)
                if not ctx.get_control("boot_complete", False):
                    # 超时或停止，跳过本轮
                    continue
                print("[AI] 开机启动完成，开始监听唤醒词")

            woke = False
            is_ptt = False

            if wake_method == "voice":
                if listener is None:
                    try:
                        listener = WakeListener(assistant.kws_engine, device_index=assistant.input_device)
                    except Exception as e:
                        print(f"[AI] 创建 WakeListener 失败: {e}")
                        time.sleep(0.5)
                        continue
                try:
                    if not listener.start():
                        time.sleep(0.5)
                        continue
                except Exception as e:
                    print(f"[AI] 启动 WakeListener 失败: {e}")
                    time.sleep(0.5)
                    continue

                while not ctx.get_control("stop_flag", False):
                    try:
                        samples = listener.q.get(timeout=0.05)
                    except queue.Empty:
                        continue
                    keyword = assistant.kws_engine.process(samples)
                    if keyword:
                        _ai_log.info("唤醒词检测: %s", keyword)
                        woke = True
                        break
                try:
                    listener.stop()
                except Exception:
                    pass
                if not woke:
                    continue
            else:
                while (not ctx.get_control("stop_flag", False)
                       and not ctx.get_control("ptt_recording", False)):
                    time.sleep(0.02)
                if ctx.get_control("stop_flag", False):
                    break
                woke = True
                is_ptt = True

            if not woke:
                continue

            if not is_ptt:
                # 1. 短促 beep 即时反馈（~100ms）
                _play_beep()

                # 2. 同步等待唤醒提示音播放完毕，避免麦克风录到 TTS 输出
                _wake_sound_done = threading.Event()
                def _play_wake_sync():
                    try:
                        tts_play_wake_sound()
                    finally:
                        _wake_sound_done.set()
                try:
                    threading.Thread(target=_play_wake_sync, daemon=True).start()
                except Exception:
                    _wake_sound_done.set()  # 启动失败则跳过等待
                # 等待唤醒提示音播放完毕（最长等 5 秒）
                _wake_sound_done.wait(timeout=5.0)
                # 额外 0.3 秒缓冲，让音频硬件缓冲区清空残留
                time.sleep(0.3)

            assistant.chat_engine.reset()
            # 标记为聆听状态（此时扬声器已静音，安全开始录音）
            ctx.set_status("state", "LISTENING")
            ctx.set_status("user_text", "")

            # NPU 推理开始时设置"识别中"，区别于"聆听中"
            def _on_inference():
                ctx.set_status("state", "RECOGNIZING")

            if is_ptt:
                user_text = asr_listen_ptt(on_ready=None)
            else:
                user_text = asr_listen(
                    timeout=8.0, on_ready=None,
                    on_inference_start=_on_inference,
                )

            cleaned = (user_text or "").strip()
            if len(cleaned) <= 2:
                ctx.set_status("state", "SPEAKING")
                # 区分三种 ASR 结果，给出针对性反馈
                if user_text is None:
                    reply = "没有听到声音，请再试一次"
                elif len(cleaned) == 0:
                    reply = "没有识别到内容，请大声一点"
                else:
                    reply = "没听清，请再说完整一点"
                ctx.set_status("reply_text", reply)
                try:
                    tts_speak(reply)
                except Exception:
                    pass
                continue

            ctx.set_status("user_text", cleaned)
            _ai_log.info("ASR 结果: %s", cleaned[:60])
            ctx.set_status("state", "THINKING")

            if any(w in cleaned for w in ["再见", "退出", "拜拜", "休息"]):
                ctx.set_status("state", "SPEAKING")
                ctx.set_status("reply_text", "再见")
                try:
                    tts_speak("再见")
                except Exception:
                    pass
                continue

            chat_result = {"reply": None, "timed_out": False}
            _chat_done = threading.Event()

            def _chat_with_timeout():
                try:
                    r = assistant.chat_engine.chat(cleaned)
                    if not _chat_done.is_set():
                        chat_result["reply"] = r
                except Exception as e:
                    if not _chat_done.is_set():
                        chat_result["reply"] = f"抱歉，我暂时无法回答。(错误: {e})"
                finally:
                    _chat_done.set()

            chat_thread = threading.Thread(target=_chat_with_timeout, daemon=True)
            chat_thread.start()
            finished = _chat_done.wait(timeout=15)
            if not finished:
                chat_result["timed_out"] = True
                reply = "网络超时，请稍后再试"
            else:
                reply = chat_result["reply"]
            if not reply:
                reply = "抱歉，我没有得到有效回复"

            # ── 演奏意图检测 ──
            _ai_log.info("AI 回复: %s", (reply or "")[:80])
            is_play, song_name = try_parse_play_intent(reply)
            if is_play and song_name:
                # 不进行 TTS 播报，将请求交给主线程
                ctx.set_status("state", "THINKING")
                _ai_log.info("演奏意图: song=%s", song_name)
                ctx.set_control("voice_play_request", song_name)
                # 等待主线程处理完成（最长 60 秒）
                _wait_start = time.time()
                while ctx.get_control("voice_play_request") is not None:
                    if time.time() - _wait_start > 60:
                        ctx.set_control("voice_play_request", None)
                        break
                    if ctx.get_control("stop_flag", False):
                        ctx.set_control("voice_play_request", None)
                        break
                    time.sleep(0.1)
                continue

            # ── 下载意图检测 ──
            is_download, dl_song = try_parse_download_intent(reply)
            if is_download and dl_song:
                ctx.set_status("state", "THINKING")
                _ai_log.info("下载意图: song=%s", dl_song)
                ctx.set_control("voice_download_request", dl_song)
                # 等待主线程处理完成（最长 60 秒）
                _wait_start = time.time()
                while ctx.get_control("voice_download_request") is not None:
                    if time.time() - _wait_start > 60:
                        ctx.set_control("voice_download_request", None)
                        break
                    if ctx.get_control("stop_flag", False):
                        ctx.set_control("voice_download_request", None)
                        break
                    time.sleep(0.1)
                continue

            # ── 结构化 chat 回复解析（song not found 等场景）──
            import json as _json2
            try:
                chat_data = _json2.loads(reply.strip())
                if isinstance(chat_data, dict) and chat_data.get("action") == "chat":
                    chat_reply = chat_data.get("reply", reply)
                    if chat_reply:
                        reply = chat_reply
            except (_json2.JSONDecodeError, ValueError):
                pass  # 不是 JSON，保持原始 reply 文本

            # 减少句子拆分：只在句号、感叹号、问号处断句，保留逗号等使朗读更流畅
            sentences = [s.strip() for s in re.split(r'[。！？.!?\s]+', reply) if s.strip()]
            _ai_log.info("TTS 播放: %d 句", len(sentences) if sentences else 0)
            if not sentences:
                sentences = [reply]

            # ── 流水线 TTS：播放当前句时后台预合成下一句 ──
            _pending_synth = None   # (thread, path) 后台合成任务
            for i, sentence in enumerate(sentences):
                if (ctx.get_control("stop_flag", False)
                        or ctx.get_control("interrupt_playing", False)
                        or not ctx.get_control("page_active", True)):
                    break

                ctx.set_status("reply_text", sentence)
                ctx.set_status("state", "SPEAKING")

                # 确定当前句的 MP3 路径
                if i == 0:
                    # 第一句：流式合成（边合成边播放）
                    mp3_path = os.path.join(
                        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "cache", f"tts_{i}.mp3",
                    )
                    try:
                        assistant.tts_engine._synthesize_sync(sentence, mp3_path)
                    except Exception:
                        mp3_path = None
                else:
                    # 后续句：使用已预合成的结果
                    if _pending_synth:
                        _thr, mp3_path = _pending_synth
                        try:
                            _thr.join(timeout=5)
                        except Exception:
                            pass
                    else:
                        mp3_path = None

                # 提前启动下一句的异步合成（在播放当前句的同时进行）
                if i + 1 < len(sentences):
                    next_path = os.path.join(
                        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "cache", f"tts_{i+1}.mp3",
                    )
                    _thr, _synth_path = assistant.tts_engine.synthesize_async(sentences[i + 1], next_path)
                    _pending_synth = (_thr, _synth_path)
                else:
                    _pending_synth = None

                # 播放当前句（阻塞，但同时后台在合成下一句）
                if mp3_path and os.path.exists(mp3_path):
                    try:
                        assistant.tts_engine.play_file(mp3_path)
                    except Exception:
                        pass
                else:
                    # 回退到传统 speak 方式
                    try:
                        tts_speak(sentence)
                    except Exception:
                        pass

                if (ctx.get_control("stop_flag", False)
                        or ctx.get_control("interrupt_playing", False)
                        or not ctx.get_control("page_active", True)):
                    break

            if ctx.get_control("interrupt_playing", False):
                ctx.set_control("interrupt_playing", False)

            ctx.set_status("reply_text", reply)

    finally:
        ctx.set_control("active", False)
        ctx.set_control("ptt_recording", False)
        if listener:
            try:
                listener.stop()
            except Exception:
                pass
        if assistant:
            try:
                assistant.asr_engine.release()
            except Exception:
                pass
        if vosk_asr:
            try:
                vosk_asr.release()
            except Exception:
                pass
