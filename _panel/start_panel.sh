#!/bin/sh
# 幂等拉起舵机校准面板服务（板子上跑，端口默认 5002）
# 已在跑就直接退出；没跑就 systemd-run 拉起来；板子断电重启后同样可用。
PORT="${1:-5002}"
if curl -s -o /dev/null -m 2 "http://127.0.0.1:$PORT/"; then
    echo "panel already up on $PORT"
    exit 0
fi
systemctl stop servo-panel 2>/dev/null
sleep 0.3
systemd-run --unit=servo-panel --collect \
    --working-directory=/root/guitarist_app \
    /usr/bin/python3 /root/guitarist_app/servo_panel.py "$PORT" >/dev/null 2>&1
sleep 1.2
if curl -s -o /dev/null -m 3 "http://127.0.0.1:$PORT/"; then
    echo "panel started on $PORT"
else
    echo "panel FAILED to start on $PORT"
    exit 1
fi
