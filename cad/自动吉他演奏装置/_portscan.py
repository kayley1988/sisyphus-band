# -*- coding: utf-8 -*-
"""快速全端口扫描（asyncio）找打印机可能的摄像头/服务端口。"""
import asyncio, sys

HOST = sys.argv[1] if len(sys.argv) > 1 else "192.168.1.50"
CONC = 800
TIMEOUT = 0.35

async def probe(port, sem, out):
    async with sem:
        try:
            fut = asyncio.open_connection(HOST, port)
            r, w = await asyncio.wait_for(fut, timeout=TIMEOUT)
            out.append(port)
            w.close()
            try:
                await w.wait_closed()
            except Exception:
                pass
        except Exception:
            pass

async def main():
    sem = asyncio.Semaphore(CONC)
    out = []
    tasks = [probe(p, sem, out) for p in range(1, 65536)]
    await asyncio.gather(*tasks)
    out.sort()
    print("%s 开放端口: %s" % (HOST, out))

asyncio.run(main())
