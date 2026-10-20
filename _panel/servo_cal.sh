#!/bin/sh
# servo_cal.sh — 进入屏上触摸校准模式（自动接管屏 + 串口，退出后恢复主程序）
#
#   sh /root/guitarist_app/servo_cal.sh              # 正常校准
#   sh /root/guitarist_app/servo_cal.sh --selftest 8 # 自检：只读触摸 8 秒
#
# 屏(/dev/spidev3.0)与串口(/dev/ttyS3)都被主程序独占，所以先停 guitar.service；
# 退出（含 Ctrl+C / 脚本异常）时 EXIT 陷阱负责把主程序和网页面板重新拉起。

cd /root/guitarist_app || exit 1

echo "[cal] 让出屏幕与串口：停止 guitar.service / servo-panel ..."
systemctl stop guitar.service 2>/dev/null
systemctl stop servo-panel 2>/dev/null
sleep 1

restore() {
    echo "[cal] 恢复：重新启动主程序 guitar.service ..."
    systemctl start guitar.service 2>/dev/null
    sh /root/guitarist_app/start_panel.sh 5002 >/dev/null 2>&1
    echo "[cal] 已恢复。"
}
trap restore EXIT INT TERM

/usr/bin/python3 /root/guitarist_app/servo_cal_screen.py "$@"
