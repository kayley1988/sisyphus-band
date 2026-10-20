#!/bin/sh
# 无障碍吉他改造 —— 开机初始化 rk809 音频通路（采集 + 播放）。
#
# 背景 1：'Capture MIC Path' 默认值是 'MIC OFF'，录音会得到全零数据
#         （表现为"麦克风没插"）。必须切到 'Main Mic'。
# 背景 2：采集链路有硬件增益控件，但默认档极低（静默本底 RMS≈30，人声仅≈59），
#         语音被本底噪声淹没，唤醒词检测不到。拉满后 SNR 显著改善。
# 背景 3：'Playback Path' 默认值是 'OFF'，且项目代码从未设置过它 ——
#         表现为 aplay 能正常"播放"但喇叭完全没声音。必须显式切到 'SPK'。
#
# 硬件说明（泰山派 TSPI-RK3566）：
#   板上不焊咪头/喇叭/耳机座，音频输入输出通过 4PIN GH1.25 座
#   （正面）与 2.54 焊盘（背面）引出，需外接 8Ω 喇叭 + 驻极体咪头。
#   驱动电路（含 1.3W 功放、MIC 差分输入）集成在 RK809。
#
# 调用方：guitar.service 的 ExecStartPre（失败不阻塞主服务）。

# ── 1. 采集通路 → 板载主麦克风 ──
for _i in $(seq 1 20); do
    if amixer -c 0 sset 'Capture MIC Path' 'Main Mic' >/dev/null 2>&1; then
        echo "[audio] Capture MIC Path -> Main Mic"
        break
    fi
    sleep 0.5
done

# ── 2. 拉满采集硬件增益（咪头信号偏弱，必须靠硬件增益抬 SNR）──
#    MIC Boost Gain      : 0..3   (10dB/step) -> 3   = +30dB
#    ADC PGA Gain        : 0..15  (3dB/step)  -> 15  = +27dB
#    ADC Capture Volume  : 0..255             -> 255 = 0dB (max)
amixer -c 0 sset 'MIC Boost Gain' 3,3 >/dev/null 2>&1 \
    && echo "[audio] MIC Boost Gain -> 3 (+30dB)"
amixer -c 0 sset 'ADC PGA Gain' 15,15 >/dev/null 2>&1 \
    && echo "[audio] ADC PGA Gain -> 15 (+27dB)"
amixer -c 0 sset 'ADC Capture Volume' 255,255 >/dev/null 2>&1 \
    && echo "[audio] ADC Capture Volume -> 255"

# ── 3. 播放通路 → 扬声器 SPK（RK809 内置 1.3W 功放）──
#    出厂默认 'OFF'，不设置则 aplay 有输出但喇叭无声（本项目历史遗留 Bug）。
for _i in $(seq 1 20); do
    if amixer -c 0 sset 'Playback Path' 'SPK' >/dev/null 2>&1; then
        echo "[audio] Playback Path -> SPK"
        break
    fi
    sleep 0.5
done

# ── 4. 播放音量（DAC 数字音量 + 耳机输出增益）──
#    DAC Playback Volume : 0..255 -> 235 (≈-1.1dB，默认已足够，不强行拉满避免削波)
#    HP Output Gain      : 0..3   (3dB/step) -> 3 = +9dB
amixer -c 0 cset numid=15 235,235 >/dev/null 2>&1 \
    && echo "[audio] DAC Playback Volume -> 235"
amixer -c 0 sset 'HP Output Gain' 3 >/dev/null 2>&1 \
    && echo "[audio] HP Output Gain -> 3 (+9dB)"

# 打印最终状态，便于日志核对
echo "[audio] capture : boost=$(amixer -c 0 cget numid=17 2>/dev/null | sed -n 's/.*values=//p') pga=$(amixer -c 0 cget numid=18 2>/dev/null | sed -n 's/.*values=//p')"
echo "[audio] playback: path=$(amixer -c 0 cget numid=12 2>/dev/null | sed -n 's/.*values=//p') dac=$(amixer -c 0 cget numid=15 2>/dev/null | sed -n 's/.*values=//p')"

exit 0
