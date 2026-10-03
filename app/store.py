"""Eigene App-Datenbank (SQLite im Volume /data): Benutzer, Dashboards, Freigaben."""
import json
import os
import sqlite3
import threading
import time

from werkzeug.security import check_password_hash, generate_password_hash

ROLES = ("viewer", "operator", "admin")  # viewer: nur ansehen, operator: + Schaltflächen, admin: alles

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    pw_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'viewer',
    theme TEXT NOT NULL DEFAULT 'auto',
    start_dashboard INTEGER,
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS dashboards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    config TEXT NOT NULL,
    sort INTEGER NOT NULL DEFAULT 0,
    updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS access (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    dashboard_id INTEGER NOT NULL REFERENCES dashboards(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, dashboard_id)
);
CREATE TABLE IF NOT EXISTS action_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    username TEXT NOT NULL,
    topic TEXT NOT NULL,
    payload TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._local = threading.local()
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self):
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=10)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA foreign_keys = ON")
            c.execute("PRAGMA journal_mode = WAL")
            self._local.conn = c
        return c

    def _q(self, sql, args=(), one=False):
        cur = self._conn().execute(sql, args)
        rows = cur.fetchall()
        return (rows[0] if rows else None) if one else rows

    def _x(self, sql, args=()):
        with self._lock:
            c = self._conn()
            cur = c.execute(sql, args)
            c.commit()
            return cur.lastrowid

    # --- Benutzer -----------------------------------------------------------
    def user_count(self):
        return self._q("SELECT COUNT(*) AS n FROM users", one=True)["n"]

    def users(self):
        return self._q("SELECT * FROM users ORDER BY username")

    def user(self, user_id):
        return self._q("SELECT * FROM users WHERE id = ?", (user_id,), one=True)

    def user_by_name(self, username):
        return self._q("SELECT * FROM users WHERE username = ?", (username,), one=True)

    def check_login(self, username, password):
        u = self.user_by_name(username)
        if u and check_password_hash(u["pw_hash"], password):
            return u
        return None

    def create_user(self, username, password, role="viewer"):
        if role not in ROLES:
            raise ValueError("Unbekannte Rolle")
        return self._x(
            "INSERT INTO users (username, pw_hash, role, created) VALUES (?, ?, ?, ?)",
            (username, generate_password_hash(password), role, time.time()),
        )

    def update_user(self, user_id, role=None, theme=None, start_dashboard=None):
        u = self.user(user_id)
        self._x(
            "UPDATE users SET role = ?, theme = ?, start_dashboard = ? WHERE id = ?",
            (role or u["role"], theme or u["theme"], start_dashboard, user_id),
        )

    def set_password(self, user_id, password):
        self._x("UPDATE users SET pw_hash = ? WHERE id = ?", (generate_password_hash(password), user_id))

    def delete_user(self, user_id):
        self._x("DELETE FROM users WHERE id = ?", (user_id,))

    def admin_count(self):
        return self._q("SELECT COUNT(*) AS n FROM users WHERE role = 'admin'", one=True)["n"]

    # --- Dashboards ---------------------------------------------------------
    def dashboards(self):
        return self._q("SELECT * FROM dashboards ORDER BY sort, title")

    def dashboard(self, dash_id):
        return self._q("SELECT * FROM dashboards WHERE id = ?", (dash_id,), one=True)

    def dashboard_by_slug(self, slug):
        return self._q("SELECT * FROM dashboards WHERE slug = ?", (slug,), one=True)

    def save_dashboard(self, dash_id, slug, title, config, sort=0):
        cfg = json.dumps(config, ensure_ascii=False, indent=2)
        if dash_id:
            self._x(
                "UPDATE dashboards SET slug = ?, title = ?, config = ?, sort = ?, updated = ? WHERE id = ?",
                (slug, title, cfg, sort, time.time(), dash_id),
            )
            return dash_id
        return self._x(
            "INSERT INTO dashboards (slug, title, config, sort, updated) VALUES (?, ?, ?, ?, ?)",
            (slug, title, cfg, sort, time.time()),
        )

    def delete_dashboard(self, dash_id):
        self._x("DELETE FROM dashboards WHERE id = ?", (dash_id,))

    # --- Freigaben ----------------------------------------------------------
    def dashboards_for(self, user):
        if user["role"] == "admin":
            return self.dashboards()
        return self._q(
            "SELECT d.* FROM dashboards d JOIN access a ON a.dashboard_id = d.id "
            "WHERE a.user_id = ? ORDER BY d.sort, d.title",
            (user["id"],),
        )

    def can_view(self, user, dash_id):
        if user["role"] == "admin":
            return True
        return self._q(
            "SELECT 1 FROM access WHERE user_id = ? AND dashboard_id = ?", (user["id"], dash_id), one=True
        ) is not None

    def access_ids(self, user_id):
        return set(r["dashboard_id"] for r in self._q("SELECT dashboard_id FROM access WHERE user_id = ?", (user_id,)))

    def set_access(self, user_id, dash_ids):
        with self._lock:
            c = self._conn()
            c.execute("DELETE FROM access WHERE user_id = ?", (user_id,))
            c.executemany(
                "INSERT INTO access (user_id, dashboard_id) VALUES (?, ?)", [(user_id, d) for d in dash_ids]
            )
            c.commit()

    # --- Protokoll ----------------------------------------------------------
    def log_action(self, username, topic, payload):
        self._x(
            "INSERT INTO action_log (ts, username, topic, payload) VALUES (?, ?, ?, ?)",
            (time.time(), username, topic, payload),
        )
        self._x("DELETE FROM action_log WHERE id <= (SELECT MAX(id) - 1000 FROM action_log)")

    def action_log(self, limit=100):
        return self._q("SELECT * FROM action_log ORDER BY id DESC LIMIT ?", (limit,))
