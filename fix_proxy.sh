#!/bin/sh
# 移除 guitar.service 里写死的失效代理 (127.0.0.1:7897)
# 该代理指向 PC 上的 Clash，板子上并不存在 -> 导致所有外网请求 Connection refused
set -e
F=/etc/systemd/system/guitar.service
if [ ! -f "$F.bak_1011_proxy" ]; then
    cp "$F" "$F.bak_1011_proxy"
    echo "[backup] $F.bak_1011_proxy"
fi
# 删掉三行代理环境变量，保留其他内容
grep -v -E '^Environment=(http_proxy|https_proxy|no_proxy)=' "$F.bak_1011_proxy" > "$F"
echo "--- 修改后的 Environment 行 ---"
grep -n 'Environment' "$F" || true
systemctl daemon-reload
echo "[ok] daemon-reload 完成"
