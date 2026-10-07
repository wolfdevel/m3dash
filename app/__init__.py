import datetime as dt
import logging
import os
import secrets

from flask import Flask

from .config import Config
from .history import History
from .mqtt_client import MqttBridge
from .store import Store


def _secret_key(cfg):
    if cfg.SECRET_KEY:
        return cfg.SECRET_KEY
    # Ohne SECRET_KEY: einmalig erzeugen und im Volume ablegen, damit Logins Neustarts überleben
    path = os.path.join(cfg.DATA_DIR, "secret_key")
    if os.path.exists(path):
        with open(path) as f:
            return f.read().strip()
    key = secrets.token_hex(32)
    os.makedirs(cfg.DATA_DIR, exist_ok=True)
    with open(path, "w") as f:
        f.write(key)
    os.chmod(path, 0o600)
    return key


def create_app(cfg=Config, start_mqtt=True):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=_secret_key(cfg),
        PERMANENT_SESSION_LIFETIME=dt.timedelta(days=cfg.SESSION_DAYS),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        JSON_AS_ASCII=False,
        MAX_CONTENT_LENGTH=8 * 1024 * 1024,  # Symbol-Upload
    )
    app.json.ensure_ascii = False
    app.cfg = cfg
    app.store = Store(os.path.join(cfg.DATA_DIR, "app.db"))
    app.mqtt = MqttBridge(cfg)
    app.history = History(cfg)

    if app.store.user_count() == 0:
        pw = cfg.ADMIN_PASSWORD or secrets.token_urlsafe(10)
        app.store.create_user(cfg.ADMIN_USER, pw, "admin")
        if cfg.ADMIN_PASSWORD:
            app.logger.warning("Erster Admin '%s' angelegt (Passwort aus ADMIN_PASSWORD).", cfg.ADMIN_USER)
        else:
            app.logger.warning("Erster Admin '%s' angelegt, Passwort: %s  (bitte nach dem Login ändern)",
                               cfg.ADMIN_USER, pw)

    from . import admin, views
    app.register_blueprint(views.bp)
    app.register_blueprint(admin.bp)

    if start_mqtt:
        app.mqtt.start()
    return app
