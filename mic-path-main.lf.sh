#!/bin/sh
# 无障碍吉他改造 —— 开机把 rk809 采集通路切到板载主麦克风。
#
# 背景：泰山派 rk809 声卡的 'Capture MIC Path' 默认值是 'MIC OFF'，
#       录音会得到全零数据（表现为"麦克风没插"）。
#       必须切到 'Main Mic' 才有输入。
# 调用方：guitar.service 的 ExecStartPre（失败不阻塞主服务）。

for _i in $(seq 1 20); do
    if amixer -c 0 sset 'Capture MIC Path' 'Main Mic' >/dev/null 2>&1; then
        echo "[mic] Capture MIC Path -> Main Mic"
        exit 0
    fi
    sleep 0.5
done

echo "[mic] 设置 Capture MIC Path 失败（声卡未就绪？）" >&2
exit 1
