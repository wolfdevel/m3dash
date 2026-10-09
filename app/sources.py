"""Quellen (Konnektoren) für Widget-Werte, ähnlich wie "source" in evcc.

Jede Quelle hat einen Namen, den Widgets mit "source" auswählen. Zwei Quellen kommen aus den
Umgebungsvariablen und heißen fest "mqtt" (immer) und "fhem" (wenn DB_HOST bzw. DB_PATH gesetzt ist).
Weitere legt ein Admin unter Verwaltung > Quellen an; sie liegen in der App-Datenbank.
"""
import logging
import re
import threading

from . import fhemdb, mqtt_client

log = logging.getLogger("sources")

KINDS = {
    "mqtt": ("MQTT-Broker", mqtt_client.MqttBridge, mqtt_client.SETTINGS),
    "fhemdb": ("FHEM-Datenbank (DbLog)", fhemdb.FhemDb, fhemdb.SETTINGS),
}
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,30}$")


def builtin_sources(cfg):
    out = {"mqtt": mqtt_client.MqttBridge("mqtt", {
        "host": cfg.MQTT_HOST, "port": cfg.MQTT_PORT, "user": cfg.MQTT_USER, "password": cfg.MQTT_PASSWORD,
        "tls": cfg.MQTT_TLS, "subscribe": cfg.MQTT_SUBSCRIBE, "client_id": cfg.MQTT_CLIENT_ID,
        "max_topics": cfg.MQTT_MAX_TOPICS,
    }, builtin=True)}
    if cfg.DB_HOST or (cfg.DB_BACKEND == "sqlite" and cfg.DB_PATH):
        out["fhem"] = fhemdb.FhemDb("fhem", {
            "backend": cfg.DB_BACKEND, "host": cfg.DB_HOST, "port": cfg.DB_PORT, "database": cfg.DB_NAME,
            "user": cfg.DB_USER, "password": cfg.DB_PASSWORD, "path": cfg.DB_PATH,
            "interval": cfg.CURRENT_INTERVAL, "current_query": cfg.CURRENT_QUERY,
            "history_query": cfg.HISTORY_QUERY,
        }, builtin=True)
    return out


def create(name, kind, settings):
    if kind not in KINDS:
        raise ValueError("Unbekannter Quellentyp '%s'" % kind)
    return KINDS[kind][1](name, settings)


class Sources:
    def __init__(self, cfg, store):
        self.store = store
        self.lock = threading.Lock()
        self.started = False
        self.items = builtin_sources(cfg)
        for row in store.sources():
            if row["name"] in self.items:
                log.warning("Quelle '%s' überspringen: der Name ist durch die Umgebungsvariablen belegt", row["name"])
                continue
            try:
                self.items[row["name"]] = create(row["name"], row["kind"], row["settings"])
            except Exception as e:
                log.warning("Quelle '%s' fehlerhaft: %s", row["name"], e)

    @property
    def mqtt(self):
        """Standard-Broker: Schalter senden hierüber, wenn ihre Quelle kein MQTT-Broker ist."""
        return self.items["mqtt"]

    def get(self, name):
        return self.items.get(name or "mqtt")

    def all(self):
        return sorted(self.items.values(), key=lambda s: (not s.builtin, s.name))

    def kinds(self):
        return {n: s.kind for n, s in self.items.items()}

    def start(self):
        self.started = True
        for s in list(self.items.values()):
            s.start()

    def save(self, name, kind, settings, old_name=None):
        """Quelle anlegen oder ändern (nur aus der Verwaltung, nicht für die festen Quellen)."""
        src = create(name, kind, settings)  # prüft die Einstellungen, bevor etwas gespeichert wird
        self.store.save_source(old_name or name, name, kind, settings)
        with self.lock:
            old = self.items.pop(old_name or name, None)
            self.items[name] = src
        if old is not None:
            old.stop()
        if self.started:
            src.start()
        return src

    def delete(self, name):
        self.store.delete_source(name)
        with self.lock:
            old = self.items.pop(name, None)
        if old is not None:
            old.stop()
