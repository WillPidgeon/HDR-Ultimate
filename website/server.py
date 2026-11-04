"""hdrultra.help: serves the download page and counts HDR Ultimate's daily users.

POST /ping   the app's once-a-day check-in. Adds one to today's total and stores nothing else:
             no IP addresses, IDs or anything about who checked in. Requests aren't logged either.
/admin       the admin panel (see admin.py). Only admin accounts can sign in.
Anything else is a file from public/. Views of the home page are counted per day and country (from Cloudflare's
CF-IPCountry header); that count is all that's kept, with no IPs and no cookies.

Settings come from the environment: DATA_DIR (default /data), PORT (default 8080).
"""
import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import admin

PUBLIC = Path(__file__).resolve().parent / "public"
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
PORT = int(os.environ.get("PORT", "8080"))
COUNTRY = re.compile(r"^[A-Z][A-Z0-9]$")
BOT = re.compile(r"bot|crawl|spider|slurp|preview|monitor|curl|wget|python|headless", re.I)

db_lock = threading.Lock()


def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATA_DIR / "usage.db", check_same_thread=False, timeout=10)
    db.execute("CREATE TABLE IF NOT EXISTS daily (day TEXT PRIMARY KEY, users INTEGER NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS visits (day TEXT NOT NULL, country TEXT NOT NULL, views INTEGER NOT NULL, "
               "PRIMARY KEY (day, country))")
    db.commit()
    admin.setup(db)
    return db


db = connect()


def count_check_in():
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with db_lock:
        db.execute("INSERT INTO daily (day, users) VALUES (?, 1) ON CONFLICT (day) DO UPDATE SET users = users + 1", (day,))
        db.commit()


def count_visit(country):
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with db_lock:
        db.execute("INSERT INTO visits (day, country, views) VALUES (?, ?, 1) "
                   "ON CONFLICT (day, country) DO UPDATE SET views = views + 1", (day, country))
        db.commit()


def daily_rows():
    with db_lock:
        return db.execute("SELECT day, users FROM daily ORDER BY day DESC").fetchall()


def visit_rows(since):
    """Page views per day and per country since a UTC day (YYYY-MM-DD)."""
    with db_lock:
        by_day = db.execute("SELECT day, SUM(views) FROM visits WHERE day >= ? GROUP BY day", (since,)).fetchall()
        by_country = db.execute("SELECT country, SUM(views) AS v FROM visits WHERE day >= ? GROUP BY country "
                                "ORDER BY v DESC, country", (since,)).fetchall()
    return by_day, by_country


class Handler(SimpleHTTPRequestHandler):
    server_version = "hdrultra"
    sys_version = ""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PUBLIC), **kwargs)

    def do_POST(self):
        path = urlsplit(self.path).path
        if path.startswith("/admin"):
            return self.admin_post(path)
        if path != "/ping":
            return self.send_error(HTTPStatus.NOT_FOUND)
        # Turns away crawlers and stray requests. It isn't a secret, so it can't stop deliberate spam.
        if self.headers.get("User-Agent") != "HDRUltimate":
            return self.reply(HTTPStatus.BAD_REQUEST)
        count_check_in()
        self.reply(HTTPStatus.NO_CONTENT)

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path.startswith("/admin"):
            return self.admin_get(url)
        if url.path in ("/", "/index.html") and not BOT.search(self.headers.get("User-Agent", "")):
            country = self.headers.get("CF-IPCountry", "").upper()
            count_visit(country if COUNTRY.match(country) else "XX")
        super().do_GET()

    # Admin panel

    def admin_get(self, url):
        user = self.admin_user()
        if url.path == "/admin/login":
            return self.redirect("/admin") if user else self.html(admin.login_page())
        if url.path not in ("/admin", "/admin/"):
            return self.send_error(HTTPStatus.NOT_FOUND)
        if not user:
            return self.redirect("/admin/login")
        query = parse_qs(url.query)
        message = admin.MESSAGES.get(query.get("done", [""])[0])
        days = {"7": 7, "90": 90}.get(query.get("range", [""])[0], 30)
        self.html(self.dashboard(user, days, message=message))

    def dashboard(self, user, days=30, message=None, error=None):
        since = (datetime.now(timezone.utc).date() - timedelta(days=days - 1)).isoformat()
        return admin.dashboard(user, daily_rows(), visit_rows(since), days, admin.list_admins(db, db_lock), message, error)

    def admin_post(self, path):
        # Cross-site form posts are refused twice over: the cookie is SameSite=Strict, and the Origin must be this site.
        origin = self.headers.get("Origin")
        if origin and urlsplit(origin).netloc != self.headers.get("Host"):
            return self.send_error(HTTPStatus.FORBIDDEN)
        form = self.read_form()
        if form is None:
            return self.send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)

        if path == "/admin/login":
            return self.login(form)
        token = self.session_token()
        user = admin.session_user(db, db_lock, token)
        if path == "/admin/logout":
            admin.end_session(db, db_lock, token)
            return self.redirect("/admin/login", admin.clear_cookie_header())
        if not user:
            return self.redirect("/admin/login")

        name = form.get("username", "").strip().lower()
        if path == "/admin/add":
            if not admin.USERNAME.match(name):
                return self.admin_error(user, "Usernames are 2 to 32 lowercase letters, numbers, dots, dashes or underscores.")
            problem = admin.password_problem(form.get("password", ""), form.get("confirm", ""))
            if problem:
                return self.admin_error(user, problem)
            if not admin.add_admin(db, db_lock, name, form["password"]):
                return self.admin_error(user, "%s is already an admin." % name)
            return self.redirect("/admin?done=added")
        if path == "/admin/remove":
            if name == user:
                return self.admin_error(user, "You can't remove your own account.")
            admin.remove_admin(db, db_lock, name)
            return self.redirect("/admin?done=removed")
        if path == "/admin/password":
            if not admin.verify(db, db_lock, user, form.get("current", "")):
                return self.admin_error(user, "Your current password isn't right.")
            problem = admin.password_problem(form.get("password", ""), form.get("confirm", ""))
            if problem:
                return self.admin_error(user, problem)
            admin.set_password(db, db_lock, user, form["password"], keep_session=token)
            return self.redirect("/admin?done=password")
        self.send_error(HTTPStatus.NOT_FOUND)

    def login(self, form):
        ip = self.client_ip()
        name = form.get("username", "").strip().lower()
        if admin.throttled(ip):
            return self.html(admin.login_page("Too many tries. Wait 15 minutes and try again.", name), HTTPStatus.TOO_MANY_REQUESTS)
        if not admin.verify(db, db_lock, name, form.get("password", "")):
            admin.record_failure(ip)
            return self.html(admin.login_page("That username and password don't match.", name), HTTPStatus.UNAUTHORIZED)
        admin.clear_failures(ip)
        self.redirect("/admin", admin.cookie_header(admin.start_session(db, db_lock, name)))

    def admin_error(self, user, error):
        self.html(self.dashboard(user, error=error), HTTPStatus.BAD_REQUEST)

    def admin_user(self):
        return admin.session_user(db, db_lock, self.session_token())

    def session_token(self):
        try:
            morsel = SimpleCookie(self.headers.get("Cookie", "")).get(admin.SESSION_COOKIE)
        except CookieError:
            return None
        return morsel.value if morsel else None

    def client_ip(self):
        # Behind the tunnel every request comes from cloudflared; Cloudflare puts the visitor's address here.
        return self.headers.get("CF-Connecting-IP") or self.client_address[0]

    def read_form(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length > 8192:
            return None
        fields = parse_qs(self.rfile.read(length).decode("utf-8", "replace"), keep_blank_values=True)
        return {key: values[0] for key, values in fields.items()}

    def html(self, text, status=HTTPStatus.OK):
        self.reply(status, text.encode(), "text/html; charset=utf-8", cache="no-store")

    def redirect(self, location, cookie=None):
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # Shared

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
        if path.startswith("/admin"):
            self.send_header("Content-Security-Policy", admin.CSP)
            self.send_header("X-Robots-Tag", "noindex, nofollow")
        elif path.startswith("/fonts/"):
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        elif path.startswith("/img/"):
            self.send_header("Cache-Control", "public, max-age=86400")
        super().end_headers()

    def list_directory(self, path):
        self.send_error(HTTPStatus.NOT_FOUND)  # no folder listings

    def log_message(self, *args):
        pass  # no request log, so no visitor IPs are kept


if __name__ == "__main__":
    print("hdrultra.help on port %d" % PORT, flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
