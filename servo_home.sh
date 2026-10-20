#!/bin/bash
# 舵机一键归位（装琴用）
#
# 用法:
#   bash servo_home.sh              # 用配置角度归位 + 保持力矩（默认，推荐装机时用）
#   bash servo_home.sh 95           # 本次用 95° 归位（临时，不写回配置）
#   bash servo_home.sh 95 0         # 归位后立即释放力矩（可手推齿条）
#   bash servo_home.sh 95 1 save    # 把 95° 写回配置，长期生效
#
# 参数: [和弦归位角] [保持力矩 1/0] [save(可选)]
#   和弦归位角 留空 = 读配置里的值（当前 90°）
#   保持力矩   1 = 归位后通电保持（齿条不会自己跑位，默认）
#              0 = 归位后失能（省电，可用手推动）

ADB="E:/音乐/档3实现/adb_tools/platform-tools/adb.exe"
ANGLE="$1"
HOLD="${2:-1}"
SAVE="$3"

if [ "$HOLD" = "0" ]; then HOLD_BOOL="False"; else HOLD_BOOL="True"; fi
if [ "$SAVE" = "save" ]; then SAVE_BOOL="True"; else SAVE_BOOL="False"; fi

echo "== 提交归位: chord_angle=${ANGLE:-配置值} hold=${HOLD_BOOL} save=${SAVE_BOOL} =="

"$ADB" shell "python3 -c \"import urllib.request,json,sys
a=sys.argv[1]; h=(sys.argv[2]=='True'); s=(sys.argv[3]=='True')
d={'hold':h,'save':s}
if a: d['chord_angle']=float(a)
req=urllib.request.Request('http://127.0.0.1:5000/api/servo/home',method='POST',data=json.dumps(d).encode(),headers={'Content-Type':'application/json'})
print(urllib.request.urlopen(req,timeout=30).read().decode())\" '$ANGLE' '$HOLD_BOOL' '$SAVE_BOOL'"

echo ""
echo "== 板上日志确认 =="
"$ADB" shell "journalctl -u guitar.service --no-pager -n 40 | grep -a '归位' | tail -3"
