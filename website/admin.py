"""The admin panel at /admin: accounts, sign-in sessions and the pages themselves.

There's no sign-up. The first account is made on the server with:
    docker compose exec web python admin.py add <name>
and after that, admins can add and remove each other from the panel.

Passwords are stored as scrypt hashes. A session is a random token in an HttpOnly, Secure, SameSite=Strict
cookie; the database only holds its SHA-256, so a copy of the database can't be used to sign in.
"""
import getpass
import hashlib
import hmac
import html
import re
import secrets
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

import countries

SESSION_COOKIE = "__Host-hdru_admin"
SESSION_HOURS = 12
MIN_PASSWORD = 8
USERNAME = re.compile(r"^[a-z0-9_.-]{2,32}$")

# Sign-in throttling, kept in memory only (nothing about visitors is written to disk).
MAX_FAILURES = 8
FAILURE_WINDOW = 15 * 60
failures = {}
failures_lock = threading.Lock()


def setup(db):
    db.execute("CREATE TABLE IF NOT EXISTS admins (username TEXT PRIMARY KEY, password TEXT NOT NULL, created TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, username TEXT NOT NULL, expires INTEGER NOT NULL)")
    db.commit()


# Passwords

def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$16384$8$1$%s$%s" % (salt.hex(), digest.hex())


def check_password(password, stored):
    try:
        _, n, r, p, salt, digest = stored.split("$")
        test = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(test.hex(), digest)
    except (ValueError, TypeError):
        return False


# Checked against when the username doesn't exist, so a wrong name takes as long as a wrong password.
DUMMY_HASH = hash_password(secrets.token_hex(16))


def password_problem(password, confirm):
    if password != confirm:
        return "The two passwords don't match."
    if len(password) < MIN_PASSWORD:
        return "Use at least %d characters for the password." % MIN_PASSWORD
    if len(password) > 256:
        return "That password is too long."
    return None


# Accounts

def list_admins(db, lock):
    with lock:
        return db.execute("SELECT username, created FROM admins ORDER BY username").fetchall()


def add_admin(db, lock, username, password):
    created = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with lock:
        cur = db.execute("INSERT OR IGNORE INTO admins (username, password, created) VALUES (?, ?, ?)",
                         (username, hash_password(password), created))
        db.commit()
        return cur.rowcount == 1


def remove_admin(db, lock, username):
    with lock:
        db.execute("DELETE FROM admins WHERE username = ?", (username,))
        db.execute("DELETE FROM sessions WHERE username = ?", (username,))
        db.commit()


def set_password(db, lock, username, password, keep_session=None):
    """Changes a password and signs that account out everywhere except keep_session."""
    with lock:
        db.execute("UPDATE admins SET password = ? WHERE username = ?", (hash_password(password), username))
        db.execute("DELETE FROM sessions WHERE username = ? AND id != ?", (username, token_id(keep_session or "")))
        db.commit()


def verify(db, lock, username, password):
    with lock:
        row = db.execute("SELECT password FROM admins WHERE username = ?", (username,)).fetchone()
    ok = check_password(password, row[0] if row else DUMMY_HASH)
    return ok and row is not None


# Sessions

def token_id(token):
    return hashlib.sha256(token.encode()).hexdigest()


def start_session(db, lock, username):
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    with lock:
        db.execute("DELETE FROM sessions WHERE expires < ?", (now,))
        db.execute("INSERT INTO sessions (id, username, expires) VALUES (?, ?, ?)",
                   (token_id(token), username, now + SESSION_HOURS * 3600))
        db.commit()
    return token


def session_user(db, lock, token):
    if not token:
        return None
    with lock:
        row = db.execute("SELECT s.username FROM sessions s JOIN admins a ON a.username = s.username "
                         "WHERE s.id = ? AND s.expires > ?", (token_id(token), int(time.time()))).fetchone()
    return row[0] if row else None


def end_session(db, lock, token):
    with lock:
        db.execute("DELETE FROM sessions WHERE id = ?", (token_id(token or ""),))
        db.commit()


def cookie_header(token):
    return "%s=%s; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=%d" % (SESSION_COOKIE, token, SESSION_HOURS * 3600)


def clear_cookie_header():
    return "%s=; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=0" % SESSION_COOKIE


# Throttling

def throttled(ip):
    now = time.time()
    with failures_lock:
        recent = [t for t in failures.get(ip, []) if now - t < FAILURE_WINDOW]
        if recent:
            failures[ip] = recent
        else:
            failures.pop(ip, None)
        return len(recent) >= MAX_FAILURES


def record_failure(ip):
    with failures_lock:
        failures.setdefault(ip, []).append(time.time())


def clear_failures(ip):
    with failures_lock:
        failures.pop(ip, None)


# Pages

MESSAGES = {
    "added": "Admin added.",
    "removed": "Admin removed.",
    "password": "Password changed. Any other sign-ins for your account were signed out.",
}

CSP = ("default-src 'none'; style-src 'self'; img-src 'self'; font-src 'self'; "
       "form-action 'self'; frame-ancestors 'none'; base-uri 'none'")


def esc(text):
    return html.escape(str(text), quote=True)


def page(title, body, user=None):
    signed_in = ""
    if user:
        signed_in = ('<div class="who"><span>Signed in as <strong>%s</strong></span>'
                     '<form method="post" action="/admin/logout"><button class="link">Sign out</button></form></div>' % esc(user))
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<meta name="robots" content="noindex, nofollow"><title>%s</title>'
            '<link rel="icon" href="/img/logo-dark.png"><link rel="stylesheet" href="/panel.css"></head><body>'
            '<header class="top"><a class="brand" href="/admin"><img src="/img/logo-dark.png" alt="" width="32" height="32">'
            '<span>HDR Ultimate admin</span></a>%s</header><main>%s</main></body></html>'
            % (esc(title), signed_in, body))


def login_page(error=None, username=""):
    note = '<p class="error" role="alert">%s</p>' % esc(error) if error else ""
    return page("Sign in", (
        '<section class="card narrow"><h1>Sign in</h1>%s'
        '<form method="post" action="/admin/login">'
        '<label>Username<input name="username" autocomplete="username" autocapitalize="none" required value="%s"></label>'
        '<label>Password<input name="password" type="password" autocomplete="current-password" required></label>'
        '<button class="primary">Sign in</button></form></section>') % (note, esc(username)))


def days_back(rows, count):
    """The last `count` days up to today (UTC), oldest first, with 0 for days without check-ins."""
    by_day = dict(rows)
    today = datetime.now(timezone.utc).date()
    days = [(today - timedelta(days=i)).isoformat() for i in range(count - 1, -1, -1)]
    return [(day, by_day.get(day, 0)) for day in days]


def chart(series, label="Daily users"):
    width, height = 720, 180
    gap = 4 if len(series) <= 30 else 2
    top = max([users for _, users in series] + [1])
    bar = (width - gap * (len(series) - 1)) / len(series)
    bars = []
    for i, (day, users) in enumerate(series):
        h = max(users / top * (height - 4), 2 if users else 0)
        bars.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="2"%s><title>%s: %d</title></rect>'
                    % (i * (bar + gap), height - h, bar, h, ' class="today"' if i == len(series) - 1 else "", day, users))
    return ('<svg class="chart" viewBox="0 0 %d %d" preserveAspectRatio="none" role="img" '
            'aria-label="%s over the last %d days"><line x1="0" y1="%d" x2="%d" y2="%d"/>%s</svg>'
            '<div class="axis"><span>%s</span><span>Peak %d</span><span>Today</span></div>'
            % (width, height, esc(label), len(series), height, width, height, "".join(bars), series[0][0], top))


def traffic(visits, days):
    by_day, by_country = visits
    series = days_back(by_day, days)
    total = sum(views for _, views in by_country)
    ranges = "".join('<a href="/admin?range=%d"%s>%d days</a>' % (d, ' aria-current="page"' if d == days else "", d)
                     for d in (7, 30, 90))
    tiles = "".join('<div class="tile"><span>%s</span><strong>%s</strong></div>' % (esc(label), esc(value)) for label, value in [
        ("Views today (UTC)", series[-1][1]),
        ("Views, last %d days" % days, total),
        ("Daily average", "%.1f" % (total / days)),
        ("Countries", sum(1 for code, _ in by_country if code not in ("XX", "T1"))),
    ])
    # The CSP blocks inline styles, so share bars use width classes in 5% steps (w0 to w100 in panel.css).
    rows = "".join(
        '<tr><td><span class="code">%s</span>%s</td><td>%d</td>'
        '<td class="share"><span class="bar"><i class="w%d"></i></span>%.1f%%</td></tr>'
        % (esc(code), esc(countries.name(code)), views, round(views / total * 20) * 5, views / total * 100)
        for code, views in by_country)
    return (
        '<section class="card"><div class="head"><h2>Website traffic</h2><nav class="ranges" aria-label="Time range">%s</nav></div>'
        '<div class="tiles">%s</div>%s'
        '<h3 class="sub">By country</h3><div class="scroll"><table><thead><tr><th>Country</th><th>Views</th><th>Share</th></tr></thead>'
        '<tbody>%s</tbody></table></div>'
        '<p class="fine">Views of the home page, by the country Cloudflare reports for each visitor. Known bots are left out. '
        'No IP addresses or cookies are kept, only these totals.</p></section>'
        % (ranges, tiles, chart(series, "Page views"), rows or '<tr><td colspan="3">No visits yet</td></tr>'))


def dashboard(user, rows, visits, days, admins, message=None, error=None):
    last30 = days_back(rows, 30)
    today = last30[-1][1]
    yesterday = last30[-2][1]
    week = sum(users for _, users in last30[-7:]) / 7
    best = max(rows, key=lambda r: r[1]) if rows else ("none yet", 0)
    flash = ""
    if message:
        flash = '<p class="notice" role="status">%s</p>' % esc(message)
    if error:
        flash = '<p class="error" role="alert">%s</p>' % esc(error)

    tiles = "".join('<div class="tile"><span>%s</span><strong>%s</strong></div>' % (esc(label), esc(value)) for label, value in [
        ("Today (UTC)", today),
        ("Yesterday", yesterday),
        ("7-day average", "%.1f" % week),
        ("Best day", "%d" % best[1]),
    ])

    table = "".join("<tr><td>%s</td><td>%d</td></tr>" % (esc(day), users) for day, users in rows[:90])
    admin_rows = "".join(
        '<tr><td>%s%s</td><td>%s</td><td>%s</td></tr>' % (
            esc(name), ' <span class="badge">you</span>' if name == user else "", esc(created),
            "" if name == user else
            '<form method="post" action="/admin/remove"><input type="hidden" name="username" value="%s">'
            '<button class="link danger">Remove</button></form>' % esc(name))
        for name, created in admins)

    return page("HDR Ultimate admin", (
        '%s'
        '<section class="card"><h1>Daily users</h1><div class="tiles">%s</div>%s'
        '<p class="fine">One check-in per copy of the app per UTC day, from version 1.2.0 on. Best day: %s.</p></section>'
        '%s'
        '<section class="card"><h2>History</h2><div class="scroll"><table><thead><tr><th>Day (UTC)</th><th>Users</th></tr></thead>'
        '<tbody>%s</tbody></table></div></section>'
        '<section class="card"><h2>Admins</h2><div class="scroll"><table><thead><tr><th>Username</th><th>Added</th><th></th></tr></thead>'
        '<tbody>%s</tbody></table></div>'
        '<div class="forms">'
        '<form method="post" action="/admin/add"><h3>Add an admin</h3>'
        '<label>Username<input name="username" autocomplete="off" autocapitalize="none" required pattern="[a-z0-9_.\\-]{2,32}"'
        ' title="2 to 32 lowercase letters, numbers, dots, dashes or underscores"></label>'
        '<label>Password<input name="password" type="password" autocomplete="new-password" required minlength="%d"></label>'
        '<label>Password again<input name="confirm" type="password" autocomplete="new-password" required minlength="%d"></label>'
        '<button class="primary">Add admin</button></form>'
        '<form method="post" action="/admin/password"><h3>Change your password</h3>'
        '<label>Current password<input name="current" type="password" autocomplete="current-password" required></label>'
        '<label>New password<input name="password" type="password" autocomplete="new-password" required minlength="%d"></label>'
        '<label>New password again<input name="confirm" type="password" autocomplete="new-password" required minlength="%d"></label>'
        '<button class="primary">Change password</button></form>'
        '</div></section>') % (
            flash, tiles, chart(last30), esc(best[0]), traffic(visits, days),
            table or '<tr><td colspan="2">No check-ins yet</td></tr>', admin_rows,
            MIN_PASSWORD, MIN_PASSWORD, MIN_PASSWORD, MIN_PASSWORD), user)


# Command line, for making the first account (or getting back in) on the server

def ask_password():
    if not sys.stdin.isatty():
        sys.exit("Run this with a terminal attached (docker compose exec, without -T) so the password isn't shown.")
    password = getpass.getpass("Password: ")
    problem = password_problem(password, getpass.getpass("Password again: "))
    if problem:
        sys.exit(problem)
    return password


def main(argv):
    from server import db, db_lock
    usage = "Usage: python admin.py add <name> | password <name> | remove <name> | list"
    if argv[:1] == ["list"]:
        for name, created in list_admins(db, db_lock):
            print("%s (added %s)" % (name, created))
        return
    if len(argv) != 2 or argv[0] not in ("add", "password", "remove"):
        sys.exit(usage)
    action, name = argv[0], argv[1].strip().lower()
    if not USERNAME.match(name):
        sys.exit("Usernames are 2 to 32 lowercase letters, numbers, dots, dashes or underscores.")
    exists = any(n == name for n, _ in list_admins(db, db_lock))
    if action == "add":
        if exists:
            sys.exit("%s is already an admin. Use: python admin.py password %s" % (name, name))
        add_admin(db, db_lock, name, ask_password())
        print("Added %s. Sign in at https://hdrultra.help/admin" % name)
    elif not exists:
        sys.exit("There's no admin called %s." % name)
    elif action == "password":
        set_password(db, db_lock, name, ask_password())
        print("Changed the password for %s and signed it out everywhere." % name)
    else:
        remove_admin(db, db_lock, name)
        print("Removed %s." % name)


if __name__ == "__main__":
    main(sys.argv[1:])
