"""Konfiguration ausschließlich über Umgebungsvariablen (Portainer-Stack / docker-compose)."""
import os


def _bool(name, default=False):
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on", "ja")


def _int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


class Config:
    # Web
    SECRET_KEY = os.environ.get("SECRET_KEY", "")
    DATA_DIR = os.environ.get("DATA_DIR", "/data")
    PORT = _int("PORT", 8080)
    SESSION_DAYS = _int("SESSION_DAYS", 365)  # lange Sitzungen: Tippen auf dem Kindle ist mühsam
    ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

    # MQTT
    MQTT_HOST = os.environ.get("MQTT_HOST", "mqtt3")
    MQTT_PORT = _int("MQTT_PORT", 1883)
    MQTT_USER = os.environ.get("MQTT_USER", "")
    MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD", "")
    MQTT_TLS = _bool("MQTT_TLS", False)
    # Eindeutig je Container, sonst werfen sich zwei Instanzen gegenseitig vom Broker
    MQTT_CLIENT_ID = os.environ.get("MQTT_CLIENT_ID") or "smarthome-dashboard-" + os.urandom(3).hex()
    # Kommagetrennte Liste von Abos, z.B. "zigbee2mqtt/#,tasmota/#"
    MQTT_SUBSCRIBE = [t.strip() for t in os.environ.get("MQTT_SUBSCRIBE", "#").split(",") if t.strip()]
    # Nur auf diese Topics darf das Dashboard publizieren (MQTT-Wildcards erlaubt)
    MQTT_PUBLISH_ALLOW = [t.strip() for t in os.environ.get("MQTT_PUBLISH_ALLOW", "m3dash/#").split(",") if t.strip()]
    MQTT_MAX_TOPICS = _int("MQTT_MAX_TOPICS", 10000)

    # MariaDB (Historie, nur lesend)
    DB_HOST = os.environ.get("DB_HOST", "")
    DB_PORT = _int("DB_PORT", 3306)
    DB_USER = os.environ.get("DB_USER", "")
    DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
    DB_NAME = os.environ.get("DB_NAME", "fhem")
    # SQL für eine Zeitreihe. Platzhalter: %(source)s, %(device)s, %(reading)s, %(start)s, %(end)s
    # ("source" im Chart-Widget hat die Form "GERÄT:READING"). Muss (Zeitstempel, Wert) liefern.
    # Standard: FHEM-DbLog-Tabelle "history".
    HISTORY_QUERY = os.environ.get(
        "HISTORY_QUERY",
        "SELECT TIMESTAMP, VALUE FROM history "
        "WHERE DEVICE = %(device)s AND READING = %(reading)s "
        "AND TIMESTAMP BETWEEN %(start)s AND %(end)s ORDER BY TIMESTAMP",
    )
    HISTORY_MAX_POINTS = _int("HISTORY_MAX_POINTS", 600)
    CHART_CACHE_SECONDS = _int("CHART_CACHE_SECONDS", 60)
    TZ = os.environ.get("TZ", "Europe/Vienna")
