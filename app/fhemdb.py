"""FHEM-DbLog als Quelle: aktuelle Werte aus der Tabelle "current", Verläufe aus "history".

Unterstützt die DbLog-Backends MySQL/MariaDB (PyMySQL), PostgreSQL (pg8000) und SQLite (Datei im Container).
Die Tabelle "current" wird im Hintergrund alle "interval" Sekunden komplett gelesen, solange ein Dashboard
Werte daraus anzeigt; Widgets lesen dann nur noch aus dem Speicher, wie bei MQTT.
"""
import datetime as dt
import logging
import re
import sqlite3
import threading
import time

from .history import _downsample, to_number

log = logging.getLogger("fhemdb")

BACKENDS = ("mysql", "postgresql", "sqlite")
DEFAULT_PORTS = {"mysql": 3306, "postgresql": 5432}
CURRENT_QUERY = "SELECT DEVICE, READING, VALUE, TIMESTAMP FROM current"
HISTORY_QUERY = ("SELECT TIMESTAMP, VALUE FROM history "
                 "WHERE DEVICE = %(device)s AND READING = %(reading)s "
                 "AND TIMESTAMP BETWEEN %(start)s AND %(end)s ORDER BY TIMESTAMP")
IDLE_SECONDS = 600  # so lange ohne Abruf wird "current" nicht mehr abgefragt
MAX_ROWS = 50000

# Einstellungen einer FHEM-Datenbank (Verwaltung > Quellen); Standardwerte für neue Quellen
SETTINGS = {
    "backend": "mysql",
    "host": "",
    "port": 0,  # 0 = Standardport des Backends
    "database": "fhem",
    "user": "",
    "password": "",
    "path": "",  # nur SQLite: Pfad zur Datenbankdatei im Container, z.B. /fhem/fhem.db
    "interval": 10,  # Sekunden zwischen zwei Abfragen von "current"
    "current_query": CURRENT_QUERY,
    "history_query": HISTORY_QUERY,
}

_PARAM = re.compile(r"%\((\w+)\)s|%%")


def bind(sql, params, style):
    """SQL mit %(name)s-Platzhaltern in den Stil des Treibers umsetzen.

    pyformat (PyMySQL) bleibt wie es ist, "format" (pg8000) und "qmark" (sqlite3) bekommen eine Werteliste.
    """
    if style == "pyformat":
        return sql, params
    args = []

    def rep(m):
        if m.group(0) == "%%":
            return "%%" if style == "format" else "%"
        args.append(params[m.group(1)])
        return "%s" if style == "format" else "?"

    return _PARAM.sub(rep, sql), args


def _epoch(ts):
    """Zeitstempel aus der DB (datetime, Text oder Unix-Zeit) als Unix-Zeit, None wenn unbekannt."""
    if ts is None:
        return None
    try:
        if isinstance(ts, dt.datetime):
            return ts.timestamp()
        if isinstance(ts, (int, float)):
            return ts / 1000.0 if ts > 1e11 else float(ts)
        if isinstance(ts, bytes):
            ts = ts.decode()
        return dt.datetime.fromisoformat(str(ts).strip()).timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def _text(v):
    if v is None:
        return ""
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    return str(v)


class FhemDb:
    kind = "fhemdb"

    def __init__(self, name, settings, builtin=False):
        self.name = name
        self.builtin = builtin
        self.s = dict(SETTINGS)
        self.s.update({k: v for k, v in settings.items() if v not in (None, "") or k == "password"})
        if self.s["backend"] not in BACKENDS:
            raise ValueError("Unbekanntes Datenbank-Backend '%s' (erlaubt: %s)" % (self.s["backend"], ", ".join(BACKENDS)))
        self.s["interval"] = max(2, int(self.s["interval"] or 10))
        self.values = {}  # "GERÄT:READING" -> (Wert als Text, Unix-Zeit)
        self.lock = threading.Lock()
        self.last_error = ""
        self.polled_at = 0.0
        self.last_use = 0.0
        self._poller = None
        self._poll_lock = threading.Lock()
        self._wake = threading.Event()
        self._stopped = False

    # --- Verbindung ---------------------------------------------------------
    @property
    def configured(self):
        if self.s["backend"] == "sqlite":
            return bool(self.s["path"])
        return bool(self.s["host"] and self.s["database"])

    @property
    def port(self):
        return int(self.s["port"] or DEFAULT_PORTS.get(self.s["backend"], 0))

    @property
    def target(self):
        if self.s["backend"] == "sqlite":
            return "sqlite:" + (self.s["path"] or "–")
        return "%s://%s:%s/%s" % (self.s["backend"], self.s["host"] or "–", self.port, self.s["database"])

    def _connect(self):
        """(Verbindung, Platzhalter-Stil)"""
        b = self.s["backend"]
        if b == "mysql":
            import pymysql
            return pymysql.connect(host=self.s["host"], port=self.port, user=self.s["user"],
                                   password=self.s["password"], database=self.s["database"],
                                   connect_timeout=5, read_timeout=20, charset="utf8mb4"), "pyformat"
        if b == "postgresql":
            import pg8000.dbapi
            return pg8000.dbapi.connect(host=self.s["host"], port=self.port, user=self.s["user"],
                                        password=self.s["password"] or None, database=self.s["database"],
                                        timeout=20), "format"
        # SQLite nur lesend öffnen: FHEM schreibt weiter in dieselbe Datei
        return sqlite3.connect("file:%s?mode=ro" % self.s["path"], uri=True, timeout=5), "qmark"

    def query(self, sql, params=None):
        if not self.configured:
            raise RuntimeError("Datenbank '%s' ist nicht konfiguriert" % self.name)
        params = dict(params or {})
        conn, style = self._connect()
        if style == "qmark":  # SQLite: DbLog speichert Zeitstempel als Text "JJJJ-MM-TT hh:mm:ss"
            params = {k: v.strftime("%Y-%m-%d %H:%M:%S") if isinstance(v, dt.datetime) else v
                      for k, v in params.items()}
        try:
            cur = conn.cursor()
            try:
                cur.execute(*bind(sql, params, style))
                return cur.fetchmany(MAX_ROWS)
            finally:
                cur.close()
        finally:
            conn.close()

    def ping(self):
        if not self.configured:
            self.last_error = "nicht konfiguriert"
            return False
        try:
            conn, _ = self._connect()
            conn.close()
            self.last_error = ""
            return True
        except Exception as e:
            self.last_error = str(e)
            return False

    # --- Aktuelle Werte (Tabelle current) -----------------------------------
    def poll(self):
        """Liest die Tabelle "current" einmal komplett."""
        with self._poll_lock:
            try:
                rows = self.query(self.s["current_query"])
            except Exception as e:
                if str(e) != self.last_error:
                    log.warning("FHEM-Datenbank %s: %s", self.name, e)
                self.last_error = str(e)
                self.polled_at = time.time()
                return False
            now = time.time()
            vals = {}
            for row in rows:
                key = "%s:%s" % (_text(row[0]), _text(row[1]))
                ts = (_epoch(row[3]) if len(row) > 3 else None) or now
                old = vals.get(key)
                if old is None or ts >= old[1]:  # ältere DbLog-Versionen haben doppelte Zeilen in current
                    vals[key] = (_text(row[2]).strip(), ts)
            with self.lock:
                self.values = vals
            self.last_error = ""
            self.polled_at = now
            return True

    def _ensure_poller(self):
        self.last_use = time.time()
        if self._poller is not None and self._poller.is_alive():
            return
        with self._poll_lock:
            if self._poller is not None and self._poller.is_alive():
                return
            self._poller = threading.Thread(target=self._poll_loop, name="fhemdb-" + self.name, daemon=True)
            first = not self.polled_at
        if first:
            self.poll()  # erster Abruf sofort, damit die Seite nicht leer startet
        self._poller.start()

    def _poll_loop(self):
        while not self._stopped and time.time() - self.last_use < IDLE_SECONDS:
            if self._wake.wait(self.s["interval"]) or self._stopped:
                break
            self.poll()

    def stop(self):
        self._stopped = True
        self._wake.set()

    def start(self):
        pass  # Abfragen beginnen erst, wenn ein Dashboard Werte braucht

    def get(self, key):
        self._ensure_poller()
        with self.lock:
            return self.values.get(key)

    def topics(self):
        self._ensure_poller()
        with self.lock:
            return sorted(self.values.items())

    def status(self):
        """(ok, Text) für die Verwaltung."""
        if self.last_error:
            return False, self.last_error
        if not self.polled_at:
            ok = self.ping()
            return ok, ("erreichbar" if ok else self.last_error)
        return True, "%d Werte, zuletzt gelesen %s" % (len(self.values), time.strftime("%H:%M:%S",
                                                                                        time.localtime(self.polled_at)))

    # --- Verläufe (Tabelle history) -----------------------------------------
    def series(self, source, hours, query=None, value_map=None, max_points=600):
        """Liefert [(datetime, float), ...] für "GERÄT:READING", ausgedünnt auf max_points."""
        end = dt.datetime.now()
        start = end - dt.timedelta(hours=float(hours))
        device, _, reading = source.partition(":")
        rows = self.query(query or self.s["history_query"],
                          {"source": source, "device": device, "reading": reading, "start": start, "end": end})
        points = []
        for row in rows:
            ts, val = row[0], row[1]
            if not isinstance(ts, dt.datetime):
                e = _epoch(ts)
                if e is None:
                    continue
                ts = dt.datetime.fromtimestamp(e)
            num = to_number(val, value_map)
            if num is not None:
                points.append((ts, num))
        return _downsample(points, max_points)
