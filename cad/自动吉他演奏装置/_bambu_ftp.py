# -*- coding: utf-8 -*-
"""拓竹打印机 FTPS（隐式 TLS, 990）上传/列举。

用法:
  python _bambu_ftp.py ls
  python _bambu_ftp.py put <本地文件> [远端文件名]
"""
import ftplib, ssl, socket, sys, os

HOST, PORT = "192.168.1.50", 990
USER, PASSWD = "bblp", "278e12a5"


class ImplicitFTPTLS(ftplib.FTP_TLS):
    """隐式 FTPS：连接建立后立即 TLS 握手（拓竹 990 端口）。"""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._sock = None

    @property
    def sock(self):
        return self._sock

    @sock.setter
    def sock(self, value):
        if value is not None and not isinstance(value, ssl.SSLSocket):
            value = self.context.wrap_socket(value)
        self._sock = value

    def ntransfercmd(self, cmd, rest=None):
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            # vsftpd 要求数据连接复用控制连接的 TLS 会话（否则 522）
            sess = None
            try:
                sess = self.sock.session
            except Exception:
                pass
            conn = self.context.wrap_socket(conn, server_hostname=self.host, session=sess)
        return conn, size


def ctx():
    c = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    c.check_hostname = False
    c.verify_mode = ssl.CERT_NONE
    try:
        c.set_ciphers("DEFAULT@SECLEVEL=0")
    except Exception:
        pass
    c.minimum_version = ssl.TLSVersion.TLSv1_2
    return c


def connect():
    f = ImplicitFTPTLS(context=ctx())
    f.connect(HOST, PORT, timeout=20)
    f.login(USER, PASSWD)
    f.prot_p()
    f.set_pasv(True)
    return f


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "ls"
    try:
        f = connect()
        print("[ftps] 已登录", f.getwelcome())
        if cmd == "ls":
            print("--- 根目录 ---")
            f.retrlines("LIST", print)
        elif cmd == "put":
            local = sys.argv[2]
            remote = sys.argv[3] if len(sys.argv) > 3 else os.path.basename(local)
            size = os.path.getsize(local)
            print("上传 %s (%d 字节) -> /%s" % (local, size, remote))
            with open(local, "rb") as fh:
                f.storbinary("STOR " + remote, fh, blocksize=65536)
            print("上传完成")
            print("--- 校验根目录 ---")
            f.retrlines("LIST", print)
        f.quit()
    except Exception as e:
        print("!! 失败:", type(e).__name__, e)
        sys.exit(1)
