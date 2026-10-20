"""
语音合成引擎 - Edge-TTS（在线）+ eSpeak（离线备选）
==================================================
Tier 1 优化：edge_tts 库调用替代 CLI subprocess（消除进程 fork 开销）
"""
import os
import sys
import asyncio
import hashlib
import threading
import subprocess
import shutil

from logger import get_logger

_log = get_logger("ai")

from .constants import CACHE_DIR, ensure_dir

# ── 常用短语缓存 ──
_PHRASE_CACHE = {
    "没听清，请再说一次": None,
    "再见": None,
    "网络超时，请稍后再试": None,
    "抱歉，我没有得到有效回复": None,
    "我在呢": None,
}


def _get_cache_path(text):
    h = hashlib.md5(text.encode("utf-8")).hexdigest()[:12]
    return os.path.join(CACHE_DIR, f"cache_{h}.mp3")


class TTSEngine:
    """Edge-TTS 语音合成引擎（进程内调用，无需 CLI subprocess）"""

    def __init__(self, cache_dir=CACHE_DIR, voice="zh-CN-XiaoxiaoNeural"):
        self.cache_dir = cache_dir
        self.voice = voice
        ensure_dir(cache_dir)
        self._pre_cache_phrases()

    # ── 预缓存 ──
    def _pre_cache_phrases(self):
        def _worker():
            for phrase in _PHRASE_CACHE:
                cache_path = _get_cache_path(phrase)
                if not os.path.exists(cache_path):
                    try:
                        self._synthesize_sync(phrase, cache_path)
                        _log.debug("TTS 预缓存: %s", phrase)
                    except Exception as e:
                        _log.warning("TTS 预缓存失败: %s", e)
        threading.Thread(target=_worker, daemon=True).start()

    # ── 核心：进程内 TTS 合成（通过 edge_tts Python 库，非 CLI）──
    async def _synthesize_async_stream(self, text: str, out_path: str):
        """异步流式合成 MP3（edge_tts 库调用）"""
        import edge_tts
        communicate = edge_tts.Communicate(text, self.voice)
        with open(out_path, "wb") as f:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    pass  # 可用于同步口型，暂不使用

    def _synthesize_sync(self, text: str, out_path: str):
        """同步合成 MP3（封装 asyncio 调用）"""
        try:
            asyncio.run(self._synthesize_async_stream(text, out_path))
        except Exception:
            # 回退：edge_tts 库不可用时尝试 CLI
            python_exe = sys.executable
            cmd = [
                python_exe, "-m", "edge_tts",
                "--voice", self.voice,
                "--text", text,
                "--write-media", out_path,
            ]
            try:
                subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=15)
            except subprocess.CalledProcessError as e:
                raise RuntimeError(f"TTS 生成失败: {e.stderr}")

    def synthesize_async(self, text: str, out_path: str):
        """异步合成 MP3（后台线程），返回 (threading.Thread, out_path) 元组"""
        t = threading.Thread(target=self._synthesize_sync, args=(text, out_path), daemon=True)
        t.start()
        return t, out_path

    # ── 播放 ──
    def speak(self, text: str):
        """同步合成并播放"""
        cache_path = _get_cache_path(text)
        if os.path.exists(cache_path):
            self._play_file(cache_path)
            return
        mp3_path = os.path.join(self.cache_dir, "reply.mp3")
        self._synthesize_sync(text, mp3_path)
        if text in _PHRASE_CACHE:
            try:
                shutil.copy2(mp3_path, cache_path)
            except Exception:
                pass
        self._play_file(mp3_path)

    def speak_streaming(self, text: str):
        """流式合成并播放：边合成边播放，不等全部合成完"""
        cache_path = _get_cache_path(text)
        if os.path.exists(cache_path):
            self._play_file(cache_path)
            return
        # 后台线程流式合成到临时文件
        mp3_path = os.path.join(self.cache_dir, "reply_stream.mp3")
        # 先清空文件
        open(mp3_path, "wb").close()
        synth_done = threading.Event()
        synth_error = [None]

        def _synth_worker():
            try:
                asyncio.run(self._synthesize_async_stream(text, mp3_path))
            except Exception as e:
                synth_error[0] = e
            finally:
                synth_done.set()

        threading.Thread(target=_synth_worker, daemon=True).start()
        # 立即开始播放（mpg123 会等待文件数据就绪）
        self._play_file(mp3_path)
        synth_done.wait()
        if synth_error[0]:
            _log.error("TTS 流式合成错误: %s", synth_error[0])
        # 缓存
        if text in _PHRASE_CACHE:
            try:
                shutil.copy2(mp3_path, cache_path)
            except Exception:
                pass

    def play_file(self, path: str):
        """仅播放已有 MP3"""
        self._play_file(path)

    def play_wake_sound(self, text=None):
        if text is None:
            text = "我在呢"
        cache_path = _get_cache_path(text)
        if os.path.exists(cache_path):
            self._play_file(cache_path)
            return
        mp3_path = os.path.join(self.cache_dir, "wake.mp3")
        if not os.path.exists(mp3_path):
            self._synthesize_sync(text, mp3_path)
        self._play_file(mp3_path)

    def _play_file(self, path: str):
        """播放 MP3 文件"""
        if not os.path.exists(path):
            return
        player = None
        for cmd_name in ["mpg123", "ffplay"]:
            if shutil.which(cmd_name):
                player = cmd_name
                break
        if not player:
            _log.warning("未找到音频播放器 (mpg123/ffmpeg)")
            return
        try:
            from . import audio_utils
            vol = audio_utils.get_speaker_volume()
        except Exception:
            vol = 80
        try:
            if player == "mpg123":
                factor = int(32768 * vol / 100)
                subprocess.Popen(
                    ["mpg123", "-q", "-f", str(factor), path],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                ).wait()
            else:
                subprocess.Popen(
                    ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet",
                     "-volume", str(vol), path],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                ).wait()
        except Exception as e:
            _log.error("TTS 播放出错: %s", e)


class EspeakTTS:
    """eSpeak‑NG 离线 TTS"""

    def __init__(self, voice="zh", speed=160, amplitude=100):
        self.voice = voice
        self.speed = speed
        self.amplitude = amplitude
        self._check_available()

    def _check_available(self):
        try:
            subprocess.run(["espeak-ng", "--version"], capture_output=True, timeout=2)
        except Exception:
            _log.warning("espeak-ng 未安装")

    def speak(self, text: str):
        if not text:
            return
        try:
            from . import audio_utils
            vol = audio_utils.get_speaker_volume()
            amplitude = max(10, int(200 * vol / 100))
        except Exception:
            amplitude = self.amplitude
        safe_text = text.replace('"', '').replace('`', '').replace('$', '')
        subprocess.run(
            ["espeak-ng", "-v", self.voice, "-s", str(self.speed),
             "-a", str(amplitude), "--", safe_text],
            capture_output=True, timeout=30,
        )

    def play_wake_sound(self):
        self.speak("我在")

    def play_file(self, path: str):
        pass

    def synthesize_async(self, text: str, out_path: str):
        return None
