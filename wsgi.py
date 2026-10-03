"""Startpunkt: python wsgi.py (waitress, ein Prozess mit mehreren Threads).

Bewusst nur EIN Prozess: Die MQTT-Verbindung und der Werte-Cache leben im Speicher.
"""
from waitress import serve

from app import create_app
from app.config import Config

app = create_app()

if __name__ == "__main__":
    serve(app, host="0.0.0.0", port=Config.PORT, threads=16, ident="smarthome-dashboard")
