# -*- coding: utf-8 -*-
"""
关闭泰山派上的 AI 语音助手（无障碍吉他改造，Z 拍板 2026-10-07）

本机没有物理喇叭，上台也不喊唤醒词 —— 语音助手整体关闭，
OLED 上不再显示「AI 不可用 / 未找到麦克风 / 网址 IP」之类文字。

改三处：
  1. main.py          —— 加 config/ai_config.json 的 enabled 总开关
  2. oled_renderer.py —— UNAVAILABLE 分支留白（不显示"AI 不可用"+原因+IP）
  3. ai_config.json   —— 写入 "enabled": false

每处改前自动备份 .bak_noai，可重复执行（幂等）。
"""
import json
import os
import shutil

APP = "/root/guitarist_app"
MP = os.path.join(APP, "src/guitarist/main.py")
OP = os.path.join(APP, "src/guitarist/oled_renderer.py")
CP = os.path.join(APP, "config/ai_config.json")


def backup(p):
    b = p + ".bak_noai"
    if not os.path.exists(b):
        shutil.copy2(p, b)


# ─────────────────────────── 1. main.py ───────────────────────────
backup(MP)
s = open(MP, encoding="utf-8").read()

if "_ai_enabled_by_config" in s:
    print("main.py: already patched")
else:
    anchor = "if AI_AVAILABLE:\n    ai_ctx = AIContext()\n"
    if anchor not in s:
        print("main.py: ANCHOR NOT FOUND, skip")
    else:
        new = (
            "# ── AI 语音助手总开关（★ 无障碍改造 2026-10-07 Z 拍板）──\n"
            "#   本机没有物理喇叭，上台也不喊唤醒词 —— 语音助手整体关闭。\n"
            "#   恢复方法：把 config/ai_config.json 的 \"enabled\" 改回 true 并重启服务。\n"
            "def _ai_enabled_by_config():\n"
            "    try:\n"
            "        import json as _json\n"
            "        _p = os.path.join(\n"
            "            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),\n"
            "            \"config\", \"ai_config.json\",\n"
            "        )\n"
            "        with open(_p, \"r\", encoding=\"utf-8\") as _f:\n"
            "            return bool(_json.load(_f).get(\"enabled\", True))\n"
            "    except Exception:\n"
            "        return True\n"
            "\n"
            "if AI_AVAILABLE and not _ai_enabled_by_config():\n"
            "    AI_AVAILABLE = False\n"
            "    _log.info(\"AI 语音助手已按 config/ai_config.json (enabled=false) 关闭\")\n"
            "\n"
        ) + anchor
        s = s.replace(anchor, new, 1)
        open(MP, "w", encoding="utf-8").write(s)
        print("main.py: patched (AI 总开关已加)")

# ─────────────────────────── 2. oled_renderer.py ───────────────────────────
backup(OP)
o = open(OP, encoding="utf-8").read()

old_block = (
    "        if state_raw == \"UNAVAILABLE\":\n"
    "            ai_error = main_info.get(\"ai_error\", \"\")\n"
    "            draw.text((2, 14), \"AI 不可用\", font=self.font, fill=255)\n"
)

if "★ 无障碍改造（Z 拍板 2026-10-07）：语音助手已整体关闭" in o:
    print("oled_renderer.py: already patched")
elif old_block not in o:
    print("oled_renderer.py: ANCHOR NOT FOUND, skip")
else:
    start = o.index(old_block)
    # 找到该分支的 return（第一个在 start 之后的 "\n            return\n"）
    ret = o.index("\n            self.show(img)\n            return\n", start)
    end = ret + len("\n            self.show(img)\n            return\n")
    new_block = (
        "        # ── 状态：AI 不可用（软件/硬件条件缺失） ──\n"
        "        # ★ 无障碍改造（Z 拍板 2026-10-07）：语音助手已整体关闭（本机没有喇叭，\n"
        "        #   上台也不喊唤醒词）。此处**不再**显示「AI 不可用」+ 错误原因 + IP ——\n"
        "        #   她的眼睛在琴上，这一眼只该看到和弦。AI 状态一律留白。\n"
        "        if state_raw == \"UNAVAILABLE\":\n"
        "            self.show(img)\n"
        "            return\n"
    )
    o = o[:start] + new_block + o[end:]
    open(OP, "w", encoding="utf-8").write(o)
    print("oled_renderer.py: patched (UNAVAILABLE 分支留白)")

# ─────────────────────────── 3. ai_config.json ───────────────────────────
backup(CP)
try:
    cfg = json.load(open(CP, encoding="utf-8"))
except Exception:
    cfg = {}
cfg["enabled"] = False
cfg.setdefault("wake_word", "小爱同学")
cfg.setdefault("wake_method", "voice")
cfg.setdefault("asr_engine", "vosk")
cfg.setdefault("tts_engine", "espeak")
cfg.setdefault("mic_gain", 3.0)
cfg.setdefault("speaker_volume", 90)
with open(CP, "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
print("ai_config.json: enabled=false")

# ─────────────────────────── 校验 ───────────────────────────
import py_compile
for p in (MP, OP):
    try:
        py_compile.compile(p, doraise=True)
        print("OK compile:", os.path.basename(p))
    except Exception as e:
        print("COMPILE FAIL:", os.path.basename(p), e)
