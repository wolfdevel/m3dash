"""MQTT-Anbindung: hält den letzten Wert jedes abonnierten Topics im Speicher und publiziert Aktionen."""
import logging
import ssl
import threading
import time

import paho.mqtt.client as mqtt

log = logging.getLogger("mqtt")


class MqttBridge:
    def __init__(self, cfg):
        self.cfg = cfg
        self.values = {}  # topic -> (payload_str, timestamp)
        self.lock = threading.Lock()
        self.connected = False
        self.last_error = ""
        self.client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=cfg.MQTT_CLIENT_ID,
        )
        if cfg.MQTT_USER:
            self.client.username_pw_set(cfg.MQTT_USER, cfg.MQTT_PASSWORD or None)
        if cfg.MQTT_TLS:
            self.client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)

    def start(self):
        def run():
            while True:
                try:
                    self.client.connect(self.cfg.MQTT_HOST, self.cfg.MQTT_PORT, keepalive=60)
                    self.client.loop_forever(retry_first_connection=True)
                except Exception as e:  # Broker nicht erreichbar: weiter versuchen
                    self.last_error = str(e)
                    self.connected = False
                    log.warning("MQTT-Verbindung fehlgeschlagen: %s", e)
                    time.sleep(5)

        threading.Thread(target=run, name="mqtt", daemon=True).start()

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code.is_failure:
            self.last_error = str(reason_code)
            log.warning("MQTT verweigert: %s", reason_code)
            return
        self.connected = True
        self.last_error = ""
        for t in self.cfg.MQTT_SUBSCRIBE:
            client.subscribe(t)
        log.info("MQTT verbunden mit %s:%s, abonniert: %s", self.cfg.MQTT_HOST, self.cfg.MQTT_PORT, self.cfg.MQTT_SUBSCRIBE)

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
            if msg.topic not in self.values and len(self.values) >= self.cfg.MQTT_MAX_TOPICS:
                return
            self.values[msg.topic] = (payload, time.time())

    def get(self, topic):
        with self.lock:
            return self.values.get(topic)

    def topics(self):
        with self.lock:
            return sorted(self.values.items())

    def publish(self, topic, payload, retain=False, qos=0):
        info = self.client.publish(topic, payload, qos=qos, retain=retain)
        return info.rc == mqtt.MQTT_ERR_SUCCESS
