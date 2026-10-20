#!/bin/bash
# 板钟网络校时一键安装 —— 泰山派 Ubuntu 22（无 RTC，靠 NTP）
# 用法：adb push 本文件到板子后执行，或 adb shell 直接跑
set -e

echo "== 1/4 检查外网 =="
ping -c 2 -W 2 ntp.aliyun.com || { echo "外网不通，先确认 WiFi 已连"; exit 1; }

echo "== 2/4 安装 chrony =="
apt-get update -qq
apt-get install -y chrony

echo "== 3/4 写配置（国内 NTP 源）=="
cp /etc/chrony/chrony.conf /etc/chrony/chrony.conf.bak 2>/dev/null || true
cat > /etc/chrony/chrony.conf << 'EOF'
server ntp.aliyun.com iburst
server cn.pool.ntp.org iburst
server time1.cloud.tencent.com iburst
driftfile /var/lib/chrony/drift
makestep 1.0 3
rtcsync
logdir /var/log/chrony
EOF

echo "== 4/4 启动并验证 =="
systemctl enable --now chrony
sleep 8
chronyc tracking || true
echo "== 当前板钟 =="
date
