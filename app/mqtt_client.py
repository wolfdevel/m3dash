"""MQTT-Quelle: hält den letzten Wert jedes abonnierten Topics im Speicher und publiziert Aktionen."""
import logging
import os
import ssl
import threading
import time

import paho.mqtt.client as mqtt

log = logging.getLogger("mqtt")

# Einstellungen einer MQTT-Quelle (Verwaltung > Quellen); Standardwerte für neue Quellen
SETTINGS = {
    "host": "",
    "port": 1883,
    "user": "",
    "password": "",
    "tls": False,
    "subscribe": ["#"],
    "max_topics": 10000,
}


class MqttBridge:
    kind = "mqtt"

    def __init__(self, name, settings, builtin=False):
        self.name = name
        self.builtin = builtin
        self.s = dict(SETTINGS)
        self.s.update(settings)
        self.values = {}  # topic -> (payload_str, timestamp)
        self.lock = threading.Lock()
        self.connected = False
        self.last_error = ""
        self._stopped = False
        # Eindeutig je Container und Quelle, sonst werfen sich zwei Instanzen gegenseitig vom Broker
        client_id = self.s.get("client_id") or "smarthome-dashboard-" + os.urandom(3).hex()
        self.client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        if self.s["user"]:
            self.client.username_pw_set(self.s["user"], self.s["password"] or None)
        if self.s["tls"]:
            self.client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)

    @property
    def target(self):
        return "%s:%s" % (self.s["host"], self.s["port"])

    def start(self):
        def run():
            while not self._stopped:
                try:
                    self.client.connect(self.s["host"], int(self.s["port"]), keepalive=60)
                    self.client.loop_forever(retry_first_connection=True)
                except Exception as e:  # Broker nicht erreichbar: weiter versuchen
                    self.last_error = str(e)
                    self.connected = False
                    log.warning("MQTT-Verbindung %s fehlgeschlagen: %s", self.name, e)
                    time.sleep(5)

        threading.Thread(target=run, name="mqtt-" + self.name, daemon=True).start()

    def stop(self):
        self._stopped = True
        try:
            self.client.disconnect()
        except Exception:
            pass

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code.is_failure:
            self.last_error = str(reason_code)
            log.warning("MQTT %s verweigert: %s", self.name, reason_code)
            return
        self.connected = True
        self.last_error = ""
        for t in self.s["subscribe"]:
            client.subscribe(t)
        log.info("MQTT %s verbunden mit %s, abonniert: %s", self.name, self.target, self.s["subscribe"])

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        self.connected = False
        if reason_code != 0:
            self.last_error = str(reason_code)

    def _on_message(self, client, userdata, msg):
        try:
            payload = msg.payload.decode("utf-8", "replace")
        except Exception:
            payload = repr(msg.payload)
        with self.lock:
            if msg.topic not in self.values and len(self.values) >= int(self.s["max_topics"]):
                return
            self.values[msg.topic] = (payload, time.time())

    def get(self, topic):
        with self.lock:
            return self.values.get(topic)

    def topics(self):
        with self.lock:
            return sorted(self.values.items())

    def status(self):
        """(ok, Text) für die Verwaltung."""
        if self.connected:
            return True, "verbunden, %d Topics mit Werten" % len(self.values)
        return False, "getrennt " + self.last_error

    def publish(self, topic, payload, retain=False, qos=0):
        info = self.client.publish(topic, payload, qos=qos, retain=retain)
        return info.rc == mqtt.MQTT_ERR_SUCCESS
