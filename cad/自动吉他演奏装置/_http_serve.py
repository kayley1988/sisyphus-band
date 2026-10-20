# -*- coding: utf-8 -*-
"""在 PC 上架一个 HTTP 服务，供打印机下载切片文件；记录所有请求。

用法: python _http_serve.py [port] [dir]
"""
import http.server, socketserver, os, sys, datetime, threading

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8086
DIR = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "_print_job")
LOG = os.path.join(DIR, "http_access.log")


class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=DIR, **kw)

    def log_message(self, fmt, *args):
        line = "[%s] %s %s" % (datetime.datetime.now().strftime("%H:%M:%S"),
                               self.address_string(), fmt % args)
        print(line, flush=True)
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def do_GET(self):
        self.log_message("GET %s  UA=%s", self.path, self.headers.get("User-Agent", "-"))
        return super().do_GET()

    def do_HEAD(self):
        self.log_message("HEAD %s  UA=%s", self.path, self.headers.get("User-Agent", "-"))
        return super().do_HEAD()


class TS(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    open(LOG, "a", encoding="utf-8").write(
        "\n===== server start %s on port %d, dir=%s =====\n" %
        (datetime.datetime.now(), PORT, DIR))
    with TS(("0.0.0.0", PORT), H) as httpd:
        print("HTTP 服务已启动: http://192.168.1.29:%d/  目录=%s" % (PORT, DIR), flush=True)
        httpd.serve_forever()
