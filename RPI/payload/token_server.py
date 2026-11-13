#!/usr/bin/env python3
"""Tiny LAN page (port 8081) to paste a ChargePoint coulomb_sess cookie into the Pi."""
import html
import os
import re
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs

STATE = Path(os.getenv("CHARGEPOINT_STATE_DIR", "/var/lib/chargepoint"))
TOKEN = STATE / "session_token"
STATUS = STATE / "status"
TOKEN_RE = re.compile(r"^[A-Za-z0-9%._~+/=:-]{20,4096}$")


def page(msg: str = "") -> bytes:
    try:
        age = int(time.time() - TOKEN.stat().st_mtime)
        tok = f"token file updated {age // 60} min ago"
    except OSError:
        tok = "no token yet"
    try:
        st = STATUS.read_text().strip()
    except OSError:
        st = "no poll yet"
    return f"""<!doctype html><meta name=viewport content="width=device-width,initial-scale=1">
<title>ChargePoint token</title>
<body style="font:16px system-ui;max-width:40rem;margin:2rem auto;padding:0 1rem">
<h2>ChargePoint session token</h2>
<p><b>{html.escape(tok)}</b><br>last poll: {html.escape(st)}</p>
<p style="color:#070">{html.escape(msg)}</p>
<form method=post>
<p>Paste the <code>coulomb_sess</code> cookie value from a logged-in browser
(DevTools &rarr; Application &rarr; Cookies &rarr; driver.chargepoint.com):</p>
<textarea name=token rows=4 style="width:100%" autofocus></textarea><br>
<button style="margin-top:.5rem;padding:.5rem 1rem">Save</button>
</form></body>""".encode()


class H(BaseHTTPRequestHandler):
    def _send(self, code, body):
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send(200, page())

    def do_POST(self):
        n = min(int(self.headers.get("Content-Length") or 0), 8192)
        raw = parse_qs(self.rfile.read(n).decode("utf-8", "replace")).get("token", [""])[0]
        tok = raw.strip().removeprefix("coulomb_sess=").strip().strip('"').rstrip(";")
        if not TOKEN_RE.match(tok):
            return self._send(400, page("That doesn't look like a coulomb_sess token."))
        tmp = TOKEN.with_suffix(".new")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(tok)
        os.replace(tmp, TOKEN)
        self._send(200, page("Saved. Polling will start within a few seconds."))

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 8081), H).serve_forever()
