"""
AI 语音助手子包 — 统一导出所有公共 API

模块结构:
  - constants.py   — 常量 + 工具函数 (采样率、模型路径、KWS URL)
  - wake_word.py   — 唤醒词检测 (sherpa-onnx KWS)
  - asr_engine.py  — 语音识别 (Whisper RKNN / Vosk 离线)
  - chat_engine.py — DeepSeek 对话 (OpenAI 兼容 API)
  - tts_engine.py  — 语音合成 (Edge-TTS / espeak-ng)
  - listener.py    — 音频监听 (麦克风录制 + 唤醒词触发)
  - assistant.py   — 语音助手主控 (ASR → Chat → TTS 流水线)
  - context.py     — AI 上下文管理 + 后台线程
  - audio_utils.py — 音频工具 (麦克风增益 / 扬声器音量)

向后兼容: 旧代码 `from AI_voice_assistant import *` 仍然有效。
"""

# 导出所有公共接口，保持与旧 AI_voice_assistant.py 兼容

from .constants import (
    SAMPLE_RATE, KWS_CHUNK, MODEL_DIR, CACHE_DIR, KWS_MODEL_URL,
    N_FFT, HOP_LENGTH, CHUNK_LENGTH, N_MELS, MAX_LENGTH,
    VOSK_MODEL_PATH, VOSK_AVAILABLE, ensure_dir, read_api_key,
    get_input_device, load_wake_words,
)
from .wake_word import setup_kws_model, WakeWordEngine, ModelFinder
from .asr_engine import ASREngine, VoskASR
from .chat_engine import ChatEngine
from .tts_engine import TTSEngine, EspeakTTS
from .listener import WakeListener
from .assistant import VoiceAssistant
from .context import AIContext, ai_thread_main
