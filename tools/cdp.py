# -*- coding: utf-8 -*-
"""极简 CDP 客户端（标准库实现 WebSocket），用于真实浏览器驱动诊所前端。

用法：python cdp.py <script.py>
或作为模块 import：Browser(url)
"""
import base64
import json
import os
import socket
import struct
import sys
import time
import urllib.request


class WS:
    def __init__(self, url):
        # ws://127.0.0.1:9222/devtools/page/XXXX
        assert url.startswith("ws://")
        rest = url[5:]
        hostport, path = rest.split("/", 1)
        host, port = hostport.split(":")
        self.sock = socket.create_connection((host, int(port)), timeout=30)
        key = base64.b64encode(os.urandom(16)).decode()
        req = ("GET /%s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\n"
               "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
               "Sec-WebSocket-Version: 13\r\n\r\n" % (path, hostport, key))
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("ws handshake failed")
            buf += chunk
        if b"101" not in buf.split(b"\r\n")[0]:
            raise RuntimeError("ws handshake: " + buf.split(b"\r\n")[0].decode("latin1"))
        self.buf = b""
        self.msgid = 0

    def _recv_exact(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("ws closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, obj):
        data = json.dumps(obj).encode("utf-8")
        header = bytearray([0x81])
        n = len(data)
        mask = os.urandom(4)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self.sock.sendall(bytes(header) + masked)

    def recv(self, timeout=30):
        self.sock.settimeout(timeout)
        b1, b2 = self._recv_exact(2)
        length = b2 & 0x7F
        if length == 126:
            length = struct.unpack(">H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._recv_exact(8))[0]
        payload = self._recv_exact(length)
        return json.loads(payload.decode("utf-8"))

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


class Browser:
    def __init__(self, port=9222, target_url_part="8787"):
        deadline = time.time() + 30
        targets = None
        while time.time() < deadline:
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/json" % port, timeout=3) as r:
                    targets = json.loads(r.read().decode("utf-8"))
                if targets:
                    break
            except Exception:
                time.sleep(0.5)
        if not targets:
            raise RuntimeError("no CDP targets on port %d" % port)
        page = None
        for t in targets:
            if t.get("type") == "page":
                page = t
                break
        page = page or targets[0]
        self.ws = WS(page["webSocketDebuggerUrl"])
        self.id = 0
        self.events = []
        self.send("Page.enable")
        self.send("Runtime.enable")
        self.send("Log.enable")
        self.send("Network.enable")

    def send(self, method, params=None):
        self.id += 1
        mid = self.id
        self.ws.send({"id": mid, "method": method, "params": params or {}})
        while True:
            msg = self.ws.recv(60)
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError("%s -> %s" % (method, msg["error"]))
                return msg.get("result", {})
            self.events.append(msg)
            if len(self.events) > 4000:
                self.events = self.events[-2000:]

    def drain(self, seconds=0.6):
        end = time.time() + seconds
        while time.time() < end:
            try:
                self.events.append(self.ws.recv(1))
            except Exception:
                break

    def goto(self, url, wait=1.6):
        self.send("Page.navigate", {"url": url})
        self.drain(wait)
        return self

    def js(self, expr, await_promise=False):
        r = self.send("Runtime.evaluate", {
            "expression": expr, "returnByValue": True,
            "awaitPromise": await_promise, "userGesture": True})
        if "exceptionDetails" in r:
            return {"__error__": r["exceptionDetails"].get("text"),
                    "detail": str(r["exceptionDetails"].get("exception", {}).get("description"))[:800]}
        return r.get("result", {}).get("value")

    async def js_async(self, expr):
        return self.js(expr, await_promise=True)

    def screenshot(self, path):
        r = self.send("Page.captureScreenshot", {"format": "png"})
        with open(path, "wb") as f:
            f.write(base64.b64decode(r["data"]))
        return path

    def console_errors(self):
        out = []
        for e in self.events:
            m = e.get("method")
            if m == "Runtime.exceptionThrown":
                d = e["params"].get("exceptionDetails", {})
                out.append("EXC " + str(d.get("exception", {}).get("description"))[:400])
            elif m == "Runtime.consoleAPICalled" and e["params"].get("type") in ("error", "warning"):
                args = [a.get("value", a.get("description", "")) for a in e["params"].get("args", [])]
                out.append("CONSOLE " + " ".join(str(a) for a in args)[:400])
            elif m == "Log.entryAdded":
                en = e["params"]["entry"]
                if en.get("level") in ("error", "warning"):
                    out.append("LOG " + str(en.get("text"))[:400])
        return out

    def close(self):
        self.ws.close()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9222
    b = Browser(port)
    print(json.dumps(b.js("document.title"), ensure_ascii=False))
    print(json.dumps(b.js("document.body.innerText.slice(0,1500)"), ensure_ascii=False))
    b.close()
