"""
语音识别引擎 — Whisper RKNN 在线推理 + Vosk 离线识别双引擎

Whisper RKNN (默认):
  - 使用 RK3566 NPU 加速推理 (RKNN 模型)
  - 支持 encoder-decoder 分离架构
  - WebRTC VAD 语音活动检测 (level 2 平衡模式)
  - 适配自定义唤醒词场景的短音频识别

Vosk 离线 (备选):
  - 纯 CPU 推理，无需联网
  - 适合网络不稳定环境
  - 模型路径: models/vosk/vosk-model-small-cn-0.22/

类:
  ASREngine — Whisper RKNN 引擎 (VAD → Mel 频谱 → RKNN 推理 → 文本)
  VoskASR   — Vosk 离线引擎 (KaldiRecognizer)
"""
import os
import sys
import time
import json
import queue
import numpy as np
import sounddevice as sd
from logger import get_logger

# ══════════════════════════════════════════════════════════════════
#  可选重依赖 —— 仅「Whisper RKNN」这条路径需要
#  ─────────────────────────────────────────────────────────────────
#  ★ 无障碍改造：本机走 Vosk 离线识别（config.yaml: ai.asr_engine = "vosk"），
#    不需要 torch / rknn / webrtcvad。原因：
#      · torch==1.13（作者环境）与 numpy 2.x 不兼容，装了要降 numpy
#        → 会连累已经跑通的演奏链；
#      · rknn-toolkit-lite2 是 Rockchip 离线 wheel，官方只出 cp38，
#        本机是 Py3.10/aarch64，pip 根本装不到。
#    改成条件导入后：缺它们时本模块**仍能 import**，
#    ASREngine（Whisper RKNN）不可用，但 VoskASR 完全不受影响。
# ══════════════════════════════════════════════════════════════════
try:
    import torch
    TORCH_AVAILABLE = True
except ImportError:                                  # pragma: no cover
    torch = None
    TORCH_AVAILABLE = False

try:
    import webrtcvad
    WEBRTCVAD_AVAILABLE = True
except ImportError:                                  # pragma: no cover
    webrtcvad = None
    WEBRTCVAD_AVAILABLE = False

try:
    from rknn.api import RKNN
    RKNN_AVAILABLE = True
except ImportError:                                  # pragma: no cover
    RKNN = None
    RKNN_AVAILABLE = False

_log = get_logger("ai")

from .constants import SAMPLE_RATE, N_FFT, HOP_LENGTH, N_MELS, MAX_LENGTH, VOSK_MODEL_PATH, VOSK_AVAILABLE
from .audio_utils import AGCProcessor


class ASREngine:
    """Whisper RKNN 语音识别引擎"""
    def __init__(self, encoder_model_path, decoder_model_path, vocab_path,
                 mel_filters_path, target, device_id, task_code, task,
                 sample_rate=SAMPLE_RATE):
        # ── 重依赖自检：缺任一就明确报错，而不是抛 NameError 让人猜 ──
        _missing = [n for n, ok in (
            ("torch", TORCH_AVAILABLE),
            ("rknn.api", RKNN_AVAILABLE),
            ("webrtcvad", WEBRTCVAD_AVAILABLE),
        ) if not ok]
        if _missing:
            raise RuntimeError(
                "Whisper RKNN 引擎不可用 —— 缺少 %s。"
                "本机请改用 Vosk 离线识别：config.yaml → ai.asr_engine: \"vosk\""
                % " / ".join(_missing)
            )
        self.sample_rate = sample_rate
        #: 最近一次录音失败原因（自检 P4 用它区分"麦克风忙/异常"与"没听到语音"）
        self.last_error = None
        self.vad = webrtcvad.Vad(2)  # level 2 平衡模式，减少环境噪音误判
        self.agc = AGCProcessor()     # AGC 自动增益控制，与 WakeListener 共用同一实现
        self.target = target
        self.device_id = device_id
        self.task = task
        self.task_code = task_code

        self.vocab = self._read_vocab(vocab_path)
        self.mel_filters = self._load_mel_filters(mel_filters_path)
        self.encoder_model = self._init_rknn(encoder_model_path)
        self.decoder_model = self._init_rknn(decoder_model_path)
        _log.info("Whisper RKNN 语音识别引擎就绪")

    def _init_rknn(self, model_path):
        if not model_path.endswith(".rknn"):
            raise ValueError(f"模型文件应为 .rknn 格式: {model_path}")
        rknn = RKNN()
        _log.info("加载 RKNN 模型: %s", model_path)
        ret = rknn.load_rknn(model_path)
        if ret != 0:
            raise RuntimeError(f'加载 RKNN 模型失败: {model_path}')
        _log.debug("初始化 RKNN 运行时环境")
        ret = rknn.init_runtime(target=self.target, device_id=self.device_id)
        if ret != 0:
            raise RuntimeError('初始化运行时环境失败')
        _log.debug("RKNN 初始化完成")
        return rknn

    def _read_vocab(self, vocab_path):
        vocab = {}
        with open(vocab_path, 'r', encoding='utf-8') as f:
            for line in f:
                parts = line.strip().split(' ')
                if len(parts) >= 2:
                    key, value = parts[0], ' '.join(parts[1:])
                else:
                    key, value = parts[0], ""
                vocab[key] = value
        return vocab

    def _load_mel_filters(self, filter_path):
        mel_data = np.loadtxt(filter_path, dtype=np.float32).reshape((80, 201))
        return torch.from_numpy(mel_data)

    def _base64_decode(self, encoded_string):
        if not encoded_string:
            return ""

        def get_char_index(c):
            if 'A' <= c <= 'Z': return ord(c) - ord('A')
            if 'a' <= c <= 'z': return ord(c) - ord('a') + (ord('Z') - ord('A') + 1)
            if '0' <= c <= '9': return ord(c) - ord('0') + (ord('Z') - ord('A')) + (ord('z') - ord('a')) + 2
            if c == '+': return 62
            if c == '/': return 63
            raise ValueError(f"Invalid Base64 char: {c}")

        output_length = len(encoded_string) // 4 * 3
        decoded_bytes = bytearray(output_length)
        index = 0
        output_index = 0
        while index < len(encoded_string):
            if encoded_string[index] == '=':
                break
            first_byte = (get_char_index(encoded_string[index]) << 2) + ((get_char_index(encoded_string[index + 1]) & 0x30) >> 4)
            decoded_bytes[output_index] = first_byte
            if index + 2 < len(encoded_string) and encoded_string[index + 2] != '=':
                second_byte = ((get_char_index(encoded_string[index + 1]) & 0x0f) << 4) + ((get_char_index(encoded_string[index + 2]) & 0x3c) >> 2)
                decoded_bytes[output_index + 1] = second_byte
                if index + 3 < len(encoded_string) and encoded_string[index + 3] != '=':
                    third_byte = ((get_char_index(encoded_string[index + 2]) & 0x03) << 6) + get_char_index(encoded_string[index + 3])
                    decoded_bytes[output_index + 2] = third_byte
                    output_index += 3
                else:
                    output_index += 2
            else:
                output_index += 1
            index += 4
        return decoded_bytes.decode('utf-8', errors='replace')

    def _log_mel_spectrogram(self, audio: np.ndarray, n_mels: int = N_MELS):
        if not torch.is_tensor(audio):
            audio = torch.from_numpy(audio)
        if audio.ndim > 1:
            audio = audio.reshape(-1)
        window = torch.hann_window(N_FFT)
        stft = torch.stft(audio, N_FFT, HOP_LENGTH, window=window, return_complex=True)
        magnitudes = stft[..., :-1].abs() ** 2
        mel_spec = self.mel_filters @ magnitudes
        log_spec = torch.clamp(mel_spec, min=1e-10).log10()
        log_spec = torch.maximum(log_spec, log_spec.max() - 8.0)
        log_spec = (log_spec + 4.0) / 4.0
        return log_spec.numpy()

    def _pad_or_trim(self, mel: np.ndarray):
        if mel.shape[1] == MAX_LENGTH:
            return mel
        if mel.shape[1] > MAX_LENGTH:
            return mel[:, :MAX_LENGTH]
        pad_width = MAX_LENGTH - mel.shape[1]
        return np.pad(mel, ((0, 0), (0, pad_width)), mode='constant')

    def _run_encoder(self, x_mel):
        return self.encoder_model.inference(inputs=[x_mel])[0]

    def _run_decoder(self, tokens, audio_features):
        return self.decoder_model.inference(
            [np.asarray([tokens], dtype="int64"), audio_features])[0]

    def _decode(self, audio_features):
        end_token = 50257
        tokens = [50258, self.task_code, 50359, 50363]
        max_tokens = 12
        tokens_str = ''
        pop_id = max_tokens
        tokens = tokens * (max_tokens // 4)
        next_token = 50258
        while next_token != end_token:
            out_decoder = self._run_decoder(tokens, audio_features)
            next_token = out_decoder[0, -1].argmax()
            next_token_str = self.vocab.get(str(next_token), "")
            tokens.append(next_token)
            if next_token == end_token:
                tokens.pop(-1)
                next_token = tokens[-1]
                break
            if next_token > 50364:
                continue
            if pop_id > 4:
                pop_id -= 1
            tokens.pop(pop_id)
            tokens_str += next_token_str
        result = tokens_str.replace('\u0120', ' ').replace('<|endoftext|>', '').replace('\n', '')
        if self.task_code == 50260:
            result = self._base64_decode(result)
        return result.strip()

    def listen_and_recognize(self, silence_sec=0.8, max_sec=8,
                              on_inference_start=None) -> str:
        chunk_size = int(self.sample_rate * 0.03)
        silence_chunks = int(silence_sec * 1000 / 30)
        max_chunks = int(max_sec * 1000 / 30)

        buffer = []
        silent = 0
        started = False

        try:
            with sd.InputStream(samplerate=self.sample_rate, channels=1,
                                dtype='int16', blocksize=chunk_size) as stream:
                for _ in range(max_chunks):
                    data_int16, _ = stream.read(chunk_size)
                    # ── AGC 增益处理：与 WakeListener 保持一致的音量体验 ──
                    data_float = data_int16.ravel().astype(np.float32) / 32768.0
                    data_gained = self.agc.process(data_float)
                    # 转回 int16 供 VAD 使用（WebRTC VAD 需要 int16）
                    data_int16_gained = (data_gained * 32768.0).clip(-32768, 32767).astype(np.int16)
                    buffer.append(data_int16_gained)
                    is_speech = self.vad.is_speech(data_int16_gained.tobytes(), self.sample_rate)
                    if is_speech:
                        silent = 0
                        started = True
                    elif started:
                        silent += 1
                        if silent > silence_chunks:
                            break
        except Exception as e:
            _log.error("录音异常: %s", e)
            self.last_error = "record_error: %s" % e
            return None

        if not started:
            self.last_error = "no_speech"
            return None

        # 录音结束，通知调用方开始 NPU 推理
        if on_inference_start:
            try:
                on_inference_start()
            except Exception:
                pass

        self.last_error = None
        audio_int16 = np.concatenate(buffer)
        audio_int16 = audio_int16.ravel()
        audio_float = audio_int16.astype(np.float32) / 32768.0

        mel = self._log_mel_spectrogram(audio_float)
        mel_padded = self._pad_or_trim(mel)
        x_mel = np.expand_dims(mel_padded, 0)

        audio_features = self._run_encoder(x_mel)
        result = self._decode(audio_features)
        return result if result else None

    def transcribe_pcm(self, audio_int16) -> str:
        """对**已录好的** int16 PCM 做本地转写（不开麦克风、不播放声音）。

        给开机自检 P4 用：欢迎语窗口先缓冲 PCM，等引擎就绪后再转写，
        避开"抢麦克风"与"引擎未就绪"两个竞态。失败返回空串。
        """
        try:
            import numpy as np
            audio = np.asarray(audio_int16).reshape(-1).astype(np.float32) / 32768.0
            audio = self.agc.process(audio)   # ★ 必须与 listen_and_recognize 一致
            mel = self._log_mel_spectrogram(audio)
            mel_padded = self._pad_or_trim(mel)
            x_mel = np.expand_dims(mel_padded, 0)
            features = self._run_encoder(x_mel)
            result = self._decode(features)
            self.last_error = None
            return result or ""
        except Exception as e:
            self.last_error = "transcribe_error: %s" % e
            return ""

    def release(self):
        if hasattr(self, 'encoder_model') and self.encoder_model:
            self.encoder_model.release()
        if hasattr(self, 'decoder_model') and self.decoder_model:
            self.decoder_model.release()
        _log.info("Whisper RKNN 模型已释放")


class VoskASR:
    """Vosk 离线语音识别封装"""
    def __init__(self, model_path=VOSK_MODEL_PATH, sample_rate=16000):
        self.model_path = model_path
        self.sample_rate = sample_rate
        self.model = None
        self.recognizer = None
        self._init_model()

    def _init_model(self):
        if not VOSK_AVAILABLE:
            raise RuntimeError("Vosk 库未安装，请执行 pip install vosk")
        if not os.path.exists(self.model_path):
            raise FileNotFoundError(f"Vosk 模型未找到: {self.model_path}")
        from vosk import Model as VoskModel, KaldiRecognizer
        self.model = VoskModel(self.model_path)
        self.recognizer = KaldiRecognizer(self.model, self.sample_rate)

    def listen_once(self, audio_stream_generator, timeout=8.0):
        self.recognizer.Reset()
        start_time = time.time()
        final_text = ""

        for chunk in audio_stream_generator:
            if time.time() - start_time > timeout:
                break
            if self.recognizer.AcceptWaveform(chunk):
                result = json.loads(self.recognizer.Result())
                text = result.get("text", "").strip()
                if text:
                    final_text = text
                    break

        if not final_text:
            final_res = json.loads(self.recognizer.FinalResult())
            final_text = final_res.get("text", "").strip()

        return final_text if final_text else None

    def listen_streaming(self, audio_stream_generator, timeout=8.0,
                          on_partial=None, on_final=None):
        """流式识别：每收到一个音频块就通过 on_partial 回调上报部分结果。
        返回最终完整文本。"""
        self.recognizer.Reset()
        self.recognizer.SetWords(True)
        start_time = time.time()
        final_text = ""
        last_partial = ""

        for chunk in audio_stream_generator:
            if time.time() - start_time > timeout:
                break
            if self.recognizer.AcceptWaveform(chunk):
                # 获取部分结果（实时）
                partial = json.loads(self.recognizer.PartialResult())
                partial_text = partial.get("partial", "").strip()
                if partial_text and partial_text != last_partial and on_partial:
                    last_partial = partial_text
                    try:
                        on_partial(partial_text)
                    except Exception:
                        pass
                # 检查是否有完整句子
                result = json.loads(self.recognizer.Result())
                text = result.get("text", "").strip()
                if text:
                    final_text = text
                    if on_final:
                        try:
                            on_final(text)
                        except Exception:
                            pass
                    break

        if not final_text:
            final_res = json.loads(self.recognizer.FinalResult())
            final_text = final_res.get("text", "").strip()
            if final_text and on_final:
                try:
                    on_final(final_text)
                except Exception:
                    pass

        return final_text if final_text else None

    def release(self):
        if self.model:
            del self.model
            self.model = None
