# -*- coding: utf-8 -*-
"""连接拓竹打印机（局域网 MQTT）查询/下发命令。

用法:
  python _bambu.py status          # 查询完整状态
  python _bambu.py raw <json>      # 发送任意 JSON 到 request 主题
"""
import json, ssl, sys, time, threading

HOST, PORT = "192.168.1.50", 8883
SERIAL = "22E8BJ631501784"
USER = "bblp"
PASSWD = "278e12a5"

import paho.mqtt.client as mqtt

def make_client():
    c = mqtt.Client(client_id="wb-probe-%d" % int(time.time()), protocol=mqtt.MQTTv311)
    c.username_pw_set(USER, PASSWD)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        ctx.set_ciphers("DEFAULT@SECLEVEL=0")
    except Exception:
        pass
    c.tls_set_context(ctx)
    c.tls_insecure_set(True)
    return c

def get_status(wait=10.0):
    reports = []
    done = threading.Event()

    def on_connect(cl, ud, flags, rc):
        print("[mqtt] connected rc=%s" % rc)
        cl.subscribe("device/%s/report" % SERIAL, qos=0)
        cl.publish("device/%s/request" % SERIAL,
                   json.dumps({"pushing": {"sequence_id": "1", "command": "pushall"}}), qos=0)
        cl.publish("device/%s/request" % SERIAL,
                   json.dumps({"info": {"sequence_id": "2", "command": "get_version"}}), qos=0)

    def on_message(cl, ud, msg):
        try:
            j = json.loads(msg.payload.decode("utf-8", "replace"))
        except Exception:
            return
        reports.append(j)

    def on_disconnect(cl, ud, rc):
        print("[mqtt] disconnected rc=%s" % rc)

    c = make_client()
    c.on_connect = on_connect
    c.on_message = on_message
    c.on_disconnect = on_disconnect
    c.connect(HOST, PORT, 15)
    c.loop_start()
    time.sleep(wait)
    c.loop_stop()
    try:
        c.disconnect()
    except Exception:
        pass
    return reports

def summarize(reports):
    if not reports:
        print("!! 未收到任何上报")
        return None
    # 取最后一条含 print 的
    merged = {}
    for r in reports:
        if isinstance(r, dict):
            merged.update(r)
    p = merged.get("print", {})
    print("\n================ 打印机状态 ================")
    def g(k, d="?"):
        return p.get(k, d)
    print("  状态 gcode_state :", g("gcode_state"))
    print("  打印阶段         :", g("mc_print_stage"), "| print_type:", g("print_type"))
    print("  当前任务         :", g("subtask_name"), "| percent:", g("mc_percent"),
          "| 剩余(min):", g("mc_remaining_time"))
    print("  喷嘴温度         : %.1f / 目标 %s" % (g("nozzle_temper", 0) or 0, g("nozzle_target_temper")))
    print("  热床温度         : %.1f / 目标 %s" % (g("bed_temper", 0) or 0, g("bed_target_temper")))
    print("  舱温             :", g("chamber_temper"))
    print("  错误码 print_error:", g("print_error", 0))
    print("  sdcard           :", g("sdcard"), "| wifi:", g("wifi_signal"))
    print("  AMS 状态         :", (p.get("ams") or {}).get("ams_status"), "| ams_exist:", bool(p.get("ams")))
    ams = (p.get("ams") or {}).get("ams", [])
    if ams:
        for u in ams:
            print("    AMS 单元 id=%s humidity=%s temp=%s" % (u.get("id"), u.get("humidity"), u.get("temp")))
            for t in (u.get("tray") or []):
                if t.get("tray_type"):
                    print("      槽 %s: %s  %s  剩余%s%%" % (
                        t.get("id"), t.get("tray_type"), t.get("tray_color"), t.get("remain")))
    else:
        vt = p.get("vt_tray") or {}
        print("    外挂料盘(vt_tray):", vt.get("tray_type"), vt.get("tray_color"), "remain:", vt.get("remain"))
    hms = merged.get("hms") or []
    if hms:
        print("  HMS 告警:", json.dumps(hms, ensure_ascii=False)[:400])
    print("===========================================\n")
    return merged

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        rs = get_status(wait=10)
        summarize(rs)
    elif cmd == "raw":
        payload = sys.argv[2]
        c = make_client()
        c.connect(HOST, PORT, 15)
        c.loop_start()
        time.sleep(1)
        c.publish("device/%s/request" % SERIAL, payload, qos=0)
        time.sleep(2)
        c.loop_stop(); c.disconnect()
        print("已发送:", payload)
