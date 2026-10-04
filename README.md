# Smart-Home-Dashboard

Konfigurierbare Dashboards mit Benutzerverwaltung für MQTT-Livewerte und MariaDB-Historie.
Läuft als ein Docker-Container (Portainer-Stack) und funktioniert auch auf alten Geräten
wie Kindle 4 (E-Ink) und iPad 2.

## Architektur

| Teil | Lösung | Warum |
|---|---|---|
| Webserver | Python 3.12, Flask, waitress (ein Prozess, mehrere Threads) | schlank, MQTT-Cache im Speicher |
| Seiten | serverseitig gerendertes HTML, CSS ohne Flexbox/Grid/Variablen | Kindle 4 (WebKit 533) und iOS 9 |
| Livewerte | MQTT-Client im Server hält den letzten Wert je Topic; Browser fragt alle *n* Sekunden per `XMLHttpRequest` (ES3-JavaScript) nach | keine WebSockets/fetch nötig; ohne JavaScript lädt die Seite per Meta-Refresh neu |
| Gauges, Diagramme | serverseitig als PNG (matplotlib), E-Ink in Graustufen | kein SVG/Canvas im Browser nötig |
| Historie | lesende SQL-Abfrage auf die MariaDB, frei konfigurierbar (`HISTORY_QUERY`) | Tabellenstruktur bleibt deine |
| Aktionen | Schaltflächen/Schalter senden per `POST` an den Server, der auf den Broker publiziert | Broker-Zugangsdaten bleiben im Server; funktioniert auch ohne JavaScript |
| Benutzer | eigene SQLite-DB im Volume `/data`, Rollen `viewer` / `operator` / `admin`, Dashboard-Freigaben je Benutzer | Kindle-Konto z. B. nur lesend |

Darstellung je Benutzer: Hell, Dunkel, E-Ink oder Automatisch (Kindle → E-Ink). Zum Testen `?theme=eink` an die URL hängen.

## Installation mit Portainer

1. Diesen Ordner in ein Git-Repository legen.
2. Portainer → *Stacks* → *Add stack* → *Repository*, Repository-URL angeben, Compose-Pfad `docker-compose.yml`.
3. Unter *Environment variables* mindestens setzen: `ADMIN_PASSWORD`, `MQTT_HOST`, ggf. `MQTT_USER`/`MQTT_PASSWORD`,
   `DB_HOST`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `HISTORY_QUERY`.
4. *Deploy*. Danach `http://<docker-host>:8080` öffnen (anderer Port: `DASHBOARD_PORT` setzen) und als `admin` anmelden.

Alternativ auf dem Docker-Host: `docker compose up -d --build`.

## Konfiguration (Umgebungsvariablen)

| Variable | Standard | Bedeutung |
|---|---|---|
| `DASHBOARD_PORT` | `8080` | Port, unter dem das Dashboard erreichbar ist (im Container: `PORT`) |
| `ADMIN_USER` / `ADMIN_PASSWORD` | `admin` / zufällig (steht im Log) | erster Admin, nur beim ersten Start |
| `SECRET_KEY` | wird erzeugt und in `/data` gespeichert | signiert die Login-Cookies |
| `SESSION_DAYS` | `365` | wie lange „Angemeldet bleiben“ hält |
| `MQTT_HOST`, `MQTT_PORT` | `mqtt3`, `1883` | Broker |
| `MQTT_USER`, `MQTT_PASSWORD`, `MQTT_TLS` | leer, leer, `false` | Zugang |
| `MQTT_SUBSCRIBE` | `#` | kommagetrennte Abos, z. B. `zigbee2mqtt/#,tasmota/#` |
| `MQTT_COMMAND_PREFIX` | `m3dash/stat/` | Schalter und Buttons senden auf Präfix + eigenes Subtopic (`"name"` im Widget, sonst aus der Beschriftung) |
| `MQTT_PUBLISH_ALLOW` | `m3dash/stat/#` | nur auf diese Topics darf gesendet werden (wird beim Speichern und beim Senden geprüft) |
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | – , `3306`, `fhem` | MariaDB (ein Nur-Lese-Benutzer reicht) |
| `HISTORY_QUERY` | siehe unten | SQL, das `(Zeitstempel, Wert)` liefert |
| `HISTORY_MAX_POINTS` | `600` | Diagramme werden auf so viele Punkte gemittelt |
| `CHART_CACHE_SECONDS` | `60` | wie lange ein gerendertes Diagramm wiederverwendet wird |
| `TZ` | `Europe/Vienna` | Zeitzone für Achsen und Protokoll |

### Historie aus FHEM (DbLog)

Standardmäßig liest das Dashboard die FHEM-Tabelle `history`. Im Diagramm-Widget wird eine Serie als
`"source": "GERÄT:READING"` angegeben, z. B. `"Wohnzimmer_Sensor:temperature"`.

```sql
SELECT TIMESTAMP, VALUE FROM history
WHERE DEVICE = %(device)s AND READING = %(reading)s
  AND TIMESTAMP BETWEEN %(start)s AND %(end)s ORDER BY TIMESTAMP
```

`VALUE` ist in FHEM Text. Zahlen am Anfang werden übernommen (`21.5`, `21.5 °C`), Zustände wie
`on/off`, `open/closed`, `present/absent` werden zu 1/0 (gut für `"step": true`). Eigene Zuordnungen gehen
pro Serie mit `"map": {"heizen": 1, "aus": 0}`. Wer eine andere Abfrage braucht, setzt `HISTORY_QUERY`
(Platzhalter `%(source)s`, `%(device)s`, `%(reading)s`, `%(start)s`, `%(end)s`) oder pro Serie `"query"`.

Für schnelle Diagramme sollte es einen Index über `(DEVICE, READING, TIMESTAMP)` geben; FHEM legt ihn als
`Search_Idx` normalerweise selbst an.

### Hostnamen im Container

`mqtt3` (und der MariaDB-Host) müssen aus dem Container auflösbar sein. Laufen sie als Container auf demselben
Docker-Host, den Dashboard-Container in dasselbe Docker-Netz hängen; sonst die IP-Adresse eintragen.

## Dashboards konfigurieren

Verwaltung → Dashboards → *Neues Dashboard*. Die Konfiguration ist JSON; ein vollständiges Beispiel ist
vorausgefüllt (auch in `examples/wohnzimmer.json`), eine Kurzreferenz steht unter dem Editor.
Unter *MQTT-Topics* sieht man alle Topics mit ihrem letzten Wert, um Topics und `json_path` zu finden.

Widget-Typen: `heading`, `value`, `text`, `gauge`, `bar`, `switch`, `button`, `chart`.

## Lokale Entwicklung

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
DATA_DIR=./data MQTT_HOST=127.0.0.1 ADMIN_PASSWORD=test1234 PORT=8080 .venv/bin/python wsgi.py
```
