# -*- coding: utf-8 -*-
"""本地预览服务：只为在 PC 浏览器里看 servo_panel.html 的样子。
不碰板子、不动舵机；/api/* 全部返回假数据（真实配置的快照）。
    python preview_server.py [port]
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HTML = os.path.join(ROOT, "servo_panel.html")

CFG = {
    "ok": True,
    "null_offset": [0, 8, 0, -2, 0, -2, 0, -5, 0, -12, 5, -5],
    "strum_angles": [[70.0, 130.0], [60.0, 120.0], [55.0, 130.0],
                     [77.0, 145.0], [30.0, 95.0], [75.0, 155.0]],
    "press_offsets": [[45, 50, 55, 60], [42, 48, 53, 58], [40, 46, 51, 56],
                      [-38, -44, -49, -54], [-35, -41, -47, -52], [-32, -38, -44, -50]],
    "chord_map": ["PWM0", "PCA15", "PCA13", "PCA11", "PCA9", "PCA2",
                  "PWM1", "PCA16", "PCA14", "PCA12", "PCA10", "PCA1"],
    "strum_map": ["PCA2", "PCA4", "PCA6", "PCA1", "PCA3", "PCA5"],
    "home_chord_angle": 89.0,
}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            with open(HTML, "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
            return
        if path == "/api/cfg":
            self._send(200, json.dumps(CFG), "application/json")
            return
        if path.startswith("/api/"):
            self._send(200, json.dumps({"ok": True, "tag": "PCA2", "ang": 90}),
                       "application/json")
            return
        self._send(404, "not found", "text/plain")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5055
    print("预览: http://127.0.0.1:%d/" % port)
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
