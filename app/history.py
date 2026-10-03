"""Lesender Zugriff auf die MariaDB mit den historischen Sensorwerten."""
import datetime as dt
import logging
import re

import pymysql

log = logging.getLogger("history")


class History:
    def __init__(self, cfg):
        self.cfg = cfg
        self.last_error = ""

    @property
    def configured(self):
        return bool(self.cfg.DB_HOST and self.cfg.DB_NAME)

    def _connect(self):
        return pymysql.connect(
            host=self.cfg.DB_HOST,
            port=self.cfg.DB_PORT,
            user=self.cfg.DB_USER,
            password=self.cfg.DB_PASSWORD,
            database=self.cfg.DB_NAME,
            connect_timeout=5,
            read_timeout=20,
            charset="utf8mb4",
        )

    def ping(self):
        if not self.configured:
            self.last_error = "nicht konfiguriert"
            return False
        try:
            conn = self._connect()
            conn.close()
            self.last_error = ""
            return True
        except Exception as e:
            self.last_error = str(e)
            return False

    def series(self, source, hours, query=None, value_map=None):
        """Liefert [(datetime, float), ...] für eine Quelle, ausgedünnt auf HISTORY_MAX_POINTS."""
        if not self.configured:
            raise RuntimeError("MariaDB ist nicht konfiguriert (DB_HOST/DB_NAME)")
        end = dt.datetime.now()
        start = end - dt.timedelta(hours=float(hours))
        sql = query or self.cfg.HISTORY_QUERY
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                device, _, reading = source.partition(":")
                cur.execute(sql, {"source": source, "device": device, "reading": reading,
                                  "start": start, "end": end})
                rows = cur.fetchall()
        finally:
            conn.close()
        points = []
        for row in rows:
            ts, val = row[0], row[1]
            if isinstance(ts, (int, float)):  # Unix-Zeitstempel (s oder ms)
                ts = dt.datetime.fromtimestamp(ts / 1000.0 if ts > 1e11 else ts)
            elif isinstance(ts, str):
                ts = dt.datetime.fromisoformat(ts)
            num = to_number(val, value_map)
            if num is not None:
                points.append((ts, num))
        return _downsample(points, self.cfg.HISTORY_MAX_POINTS)


# FHEM speichert VALUE als Text: Zahlen ("21.5", "21.5 °C") und Zustände ("on", "off", "open")
STATE_VALUES = {"on": 1, "off": 0, "true": 1, "false": 0, "open": 1, "closed": 0, "yes": 1, "no": 0,
                "present": 1, "absent": 0, "motion": 1, "nomotion": 0, "an": 1, "aus": 0}
_NUM = re.compile(r"^\s*(-?\d+(?:[.,]\d+)?)")


def to_number(val, value_map=None):
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, bytes):
        val = val.decode("utf-8", "replace")
    if value_map and val in value_map:
        return float(value_map[val])
    m = _NUM.match(val)
    if m:
        return float(m.group(1).replace(",", "."))
    key = val.strip().lower()
    return float(STATE_VALUES[key]) if key in STATE_VALUES else None


def _downsample(points, max_points):
    """Mittelwert-Bucketing, damit Diagramme auch bei vielen Messwerten schnell bleiben."""
    if len(points) <= max_points or max_points < 2:
        return points
    size = len(points) / float(max_points)
    out = []
    for i in range(max_points):
        chunk = points[int(i * size): int((i + 1) * size)]
        if not chunk:
            continue
        mid = chunk[len(chunk) // 2][0]
        out.append((mid, sum(p[1] for p in chunk) / len(chunk)))
    return out
