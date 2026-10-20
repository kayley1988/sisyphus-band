# -*- coding: utf-8 -*-
"""向拓竹打印机下发打印指令（局域网 MQTT project_file）。

用法:
  python _bambu_print.py probe     # 探测：url 指向不存在的文件
  python _bambu_print.py go        # 正式打印 HTTP 上的切片文件
  python _bambu_print.py stop      # 停止当前打印
"""
import hashlib, json, os, ssl, sys, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paho.mqtt.client as mqtt

HOST, PORT = "192.168.1.50", 8883
SERIAL = "22E8BJ631501784"
USER, PASSWD = "bblp", "278e12a5"
PC = "192.168.1.29"
HTTP_PORT = 8086
FNAME = "boyun_v73_batt_v2.gcode.3mf"
HERE = os.path.dirname(os.path.abspath(__file__))
LOCAL = os.path.join(HERE, "_print_job", "plate.gcode.3mf")

REPORTS = []
LOCK = threading.Lock()


def make_client():
    c = mqtt.Client(client_id="wb-print-%d" % int(time.time()), protocol=mqtt.MQTTv311)
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


def payload(url, subtask, use_ams=True, ams_mapping=(0,), md5=None):
    p = {
        "print": {
            "sequence_id": str(int(time.time()) % 100000),
            "command": "project_file",
            "param": "Metadata/plate_1.gcode",
            "subtask_name": subtask,
            "url": url,
            "timelapse": False,
            "bed_type": "textured_plate",
            "bed_leveling": True,
            "flow_cali": False,
            "vibration_cali": True,
            "layer_inspect": False,
            "use_ams": use_ams,
            "ams_mapping": list(ams_mapping),
            "project_id": "0",
            "profile_id": "0",
            "task_id": "0",
            "subtask_id": "0",
            "slice_id": "0",
            "cfg": "0",
        }
    }
    if md5:
        p["print"]["md5"] = md5
    return json.dumps(p)


def run(send_payload, wait=25):
    c = make_client()

    def on_connect(cl, ud, flags, rc):
        print("[mqtt] connected rc=%s" % rc)
        cl.subscribe("device/%s/report" % SERIAL, qos=0)
        time.sleep(0.5)
        print("[mqtt] 发送:", send_payload)
        cl.publish("device/%s/request" % SERIAL, send_payload, qos=0)

    def on_message(cl, ud, msg):
        try:
            j = json.loads(msg.payload.decode("utf-8", "replace"))
        except Exception:
            return
        with LOCK:
            REPORTS.append(j)
        p = j.get("print") if isinstance(j, dict) else None
        if p:
            st = p.get("gcode_state")
            if st:
                print("  [状态] %-10s percent=%s stage=%s err=%s create=%s" % (
                    st, p.get("mc_percent"), p.get("mc_print_stage"),
                    p.get("print_error"), p.get("err")))

    c.on_connect = on_connect
    c.on_message = on_message
    c.connect(HOST, PORT, 15)
    c.loop_start()
    time.sleep(wait)
    c.loop_stop()
    try:
        c.disconnect()
    except Exception:
        pass
    # 汇总
    merged = {}
    with LOCK:
        for r in REPORTS:
            if isinstance(r, dict):
                merged.update(r)
    p = merged.get("print", {})
    print("\n--- 最终状态 ---")
    print("  gcode_state      :", p.get("gcode_state"))
    print("  print_error      :", p.get("print_error"))
    print("  mc_print_error_code:", p.get("mc_print_error_code"))
    print("  fail_reason      :", p.get("fail_reason"))
    print("  subtask_name     :", p.get("subtask_name"))
    print("  mc_percent       :", p.get("mc_percent"))
    return p


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "probe"
    if mode == "probe":
        u = "http://%s:%d/__probe_missing__.gcode.3mf" % (PC, HTTP_PORT)
        run(payload(u, "__probe__"), wait=25)
    elif mode == "go":
        md5 = hashlib.md5(open(LOCAL, "rb").read()).hexdigest()
        print("本地文件 md5:", md5)
        u = "http://%s:%d/%s" % (PC, HTTP_PORT, FNAME)
        run(payload(u, "顶盖v7.3+电池盖v2", md5=md5), wait=40)
    elif mode == "stop":
        run(json.dumps({"print": {"sequence_id": "1", "command": "stop"}}), wait=8)
