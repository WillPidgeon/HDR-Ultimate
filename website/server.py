"""hdrultra.help: serves the download page and counts HDR Ultimate's daily users.

POST /ping   the app's once-a-day check-in. Adds one to today's total and stores nothing else:
             no IP addresses, IDs or anything about who checked in. Requests aren't logged either.
GET  /stats  ?key=<STATS_KEY>: your private view of the last 90 days.
Anything else is a file from public/.

Settings come from the environment: STATS_KEY, DATA_DIR (default /data), PORT (default 8080).
"""
import html
import os
import sqlite3
import threading
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

PUBLIC = Path(__file__).resolve().parent / "public"
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
STATS_KEY = os.environ.get("STATS_KEY", "")
PORT = int(os.environ.get("PORT", "8080"))

db_lock = threading.Lock()


def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATA_DIR / "usage.db", check_same_thread=False)
    db.execute("CREATE TABLE IF NOT EXISTS daily (day TEXT PRIMARY KEY, users INTEGER NOT NULL)")
    db.commit()
    return db


db = connect()


def count_check_in():
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with db_lock:
        db.execute("INSERT INTO daily (day, users) VALUES (?, 1) ON CONFLICT (day) DO UPDATE SET users = users + 1", (day,))
        db.commit()


def stats_page():
    with db_lock:
        rows = db.execute("SELECT day, users FROM daily ORDER BY day DESC LIMIT 90").fetchall()
    body = "".join("<tr><td>%s</td><td>%d</td></tr>" % (html.escape(day), users) for day, users in rows)
    return ('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            '<meta name="robots" content="noindex"><title>HDR Ultimate users</title>'
            "<style>body{font:16px system-ui;margin:24px}td{padding:4px 16px 4px 0}th{text-align:left;padding-right:16px}</style>"
            "<h1>Daily users</h1><table><tr><th>Day (UTC)</th><th>Users</th></tr>%s</table>"
            % (body or "<tr><td colspan=2>No check-ins yet</td></tr>"))


class Handler(SimpleHTTPRequestHandler):
    server_version = "hdrultra"
    sys_version = ""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PUBLIC), **kwargs)

    def do_POST(self):
        if urlsplit(self.path).path != "/ping":
            return self.send_error(HTTPStatus.NOT_FOUND)
        # Turns away crawlers and stray requests. It isn't a secret, so it can't stop deliberate spam.
        if self.headers.get("User-Agent") != "HDRUltimate":
            return self.reply(HTTPStatus.BAD_REQUEST)
        count_check_in()
        self.reply(HTTPStatus.NO_CONTENT)

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/stats":
            key = parse_qs(url.query).get("key", [""])[0]
            if not STATS_KEY or key != STATS_KEY:
                return self.send_error(HTTPStatus.NOT_FOUND)
            return self.reply(HTTPStatus.OK, stats_page().encode(), "text/html; charset=utf-8", cache="no-store")
        super().do_GET()

    def reply(self, status, body=b"", content_type=None, cache=None):
        self.send_response(status)
        if content_type:
            self.send_header("Content-Type", content_type)
        if cache:
            self.send_header("Cache-Control", cache)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("X-Frame-Options", "DENY")
        path = urlsplit(self.path).path
        if path.startswith("/fonts/"):
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        elif path.startswith("/img/"):
            self.send_header("Cache-Control", "public, max-age=86400")
        super().end_headers()

    def list_directory(self, path):
        self.send_error(HTTPStatus.NOT_FOUND)  # no folder listings

    def log_message(self, *args):
        pass  # no request log, so no visitor IPs are kept


if __name__ == "__main__":
    print("hdrultra.help on port %d%s" % (PORT, "" if STATS_KEY else " (no STATS_KEY: /stats is off)"), flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
