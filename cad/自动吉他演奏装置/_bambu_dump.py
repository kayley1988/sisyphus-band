# -*- coding: utf-8 -*-
"""抓取打印机所有 MQTT 报文（不合并），用于排查指令被忽略的原因。"""
import json, os, ssl, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paho.mqtt.client as mqtt

HOST, PORT = "192.168.1.50", 8883
SERIAL = "22E8BJ631501784"
USER, PASSWD = "bblp", "278e12a5"

msgs = []
c = mqtt.Client(client_id="wb-dump-%d" % int(time.time()), protocol=mqtt.MQTTv311)
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


def on_connect(cl, ud, flags, rc):
    print("[mqtt] connected rc=%s" % rc)
    cl.subscribe("device/%s/#" % SERIAL, qos=0)
    time.sleep(0.4)
    cl.publish("device/%s/request" % SERIAL,
               json.dumps({"pushing": {"sequence_id": "1", "command": "pushall"}}), qos=0)


def on_message(cl, ud, msg):
    msgs.append((msg.topic, msg.payload))
    print("\n### 主题:", msg.topic, " 长度:", len(msg.payload))
    txt = msg.payload.decode("utf-8", "replace")
    try:
        j = json.loads(txt)
        print("    顶层键:", list(j.keys()))
    except Exception:
        print("    (非JSON)", txt[:300])


c.on_connect = on_connect
c.on_message = on_message
c.connect(HOST, PORT, 15)
c.loop_start()
time.sleep(12)
c.loop_stop()
c.disconnect()

# 把每条报文单独存一份便于细看
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_print_job")
os.makedirs(out, exist_ok=True)
for i, (t, p) in enumerate(msgs):
    open(os.path.join(out, "msg_%02d.json" % i), "wb").write(p)
print("\n共收到 %d 条报文，已存到 %s/msg_XX.json" % (len(msgs), out))
