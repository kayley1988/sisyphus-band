# -*- coding: utf-8 -*-
"""在 PC 上架一个匿名只读 FTP 服务，供打印机拉取切片文件。

用法: python _ftp_serve.py [port] [dir]
"""
import os, sys, datetime
from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 21
HERE = os.path.dirname(os.path.abspath(__file__))
DIR = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "_print_job")
LOG = os.path.join(DIR, "ftp_access.log")


class H(FTPHandler):
    def on_connect(self):
        self._t0 = datetime.datetime.now()
        print("[%s] 连接来自 %s:%s" % (self._t0.strftime("%H:%M:%S"), self.remote_ip, self.remote_port), flush=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("[%s] connect %s:%s\n" % (self._t0, self.remote_ip, self.remote_port))

    def on_login(self, username):
        print("  登录: %s" % username, flush=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("  login %s\n" % username)

    def on_file_sent(self, file):
        print("  已发送文件: %s" % file, flush=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("  SENT %s\n" % file)

    def on_disconnect(self):
        print("  断开", flush=True)


authorizer = DummyAuthorizer()
authorizer.add_anonymous(DIR, perm="elr")   # 只读
H.authorizer = authorizer
H.passive_ports = range(60000, 60050)

server = FTPServer(("0.0.0.0", PORT), H)
print("FTP 服务已启动: ftp://192.168.1.29:%d/  根目录=%s" % (PORT, DIR), flush=True)
server.serve_forever()
