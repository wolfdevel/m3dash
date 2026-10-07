"""Dashboard-Konfiguration prüfen und Widget-Zustände aus MQTT-Werten berechnen.

Das Ergebnis ist bewusst "dumm" für den Browser: fertiger Text, CSS-Klasse, Balkenbreite oder
Bild-URL. So reicht im Browser ES3-JavaScript (Kindle 4, iPad 2) oder sogar gar keines.
"""
import json
import re
import time

from paho.mqtt.client import topic_matches_sub

CHART_STYLES = ("line", "step", "area", "bar", "points")
TYPES = ("heading", "value", "text", "gauge", "bar", "switch", "button", "chart")
SIZES = ("xs", "s", "m", "l", "xl")
# Standard-Symbolgröße (px) je Größenstufe
ICON_SIZES = {"xs": 24, "s": 36, "m": 48, "l": 64, "xl": 80}
# Skalierung der Gauge-Bilder je Größenstufe
GAUGE_SCALE = {"xs": 0.5, "s": 0.7, "m": 1.0, "l": 1.3, "xl": 1.6}
LEGEND = ("top", "bottom", "inside", "none")

DEFAULTS = {
    "width": 1,
    "decimals": 1,
    "unit": "",
    "stale_after": 0,  # Sekunden; 0 = nie als veraltet markieren
}


def _num(v, name, wi):
    try:
        return float(v)
    except (TypeError, ValueError):
        raise ValueError("Widget %d: '%s' muss eine Zahl sein" % (wi + 1, name))


def publish_allowed(topic, allow):
    return any(topic_matches_sub(pattern, topic) for pattern in allow)


_UMLAUTE = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}


def subtopic_name(text):
    """Aus einer Beschriftung einen Topic-Namen machen: "Stehlampe Küche" -> "stehlampe_kueche"."""
    s = "".join(_UMLAUTE.get(c, c) for c in str(text).lower())
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")


def _command_topic(n, i, prefix):
    """Sende-Topic eines Schalters/Buttons: Präfix + eigenes Subtopic ("name", sonst aus "label")."""
    if n.get("command_topic"):
        return n["command_topic"]
    name = str(n.get("name") or subtopic_name(n.get("label", "")))
    if not name:
        raise ValueError("Widget %d (%s): 'name' (Subtopic) oder 'label' fehlt" % (i + 1, n["type"]))
    if not re.match(r"^[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*$", name):
        raise ValueError("Widget %d: 'name' darf nur Buchstaben, Ziffern, _ - und / enthalten" % (i + 1))
    return prefix + name


def _icon_url(v, base, wi):
    """Symbol: volle URL (http/https), absoluter Pfad oder Dateiname unter "icon_base" (Standard /icons/)."""
    if not v:
        return ""
    v = str(v).strip()
    if re.match(r"^https?://", v, re.I) or v.startswith("/"):
        return v
    if ":" in v or ".." in v:
        raise ValueError("Widget %d: ungültiges Symbol '%s'" % (wi + 1, v))
    return base + v


def _int(v, name, wi, lo, hi):
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        raise ValueError("Widget %d: '%s' muss eine Zahl sein" % (wi + 1, name))
    if not lo <= n <= hi:
        raise ValueError("Widget %d: '%s' muss zwischen %d und %d liegen" % (wi + 1, name, lo, hi))
    return n


def normalize(config, publish_allow=None, command_prefix="m3dash/stat/"):
    """Prüft eine Dashboard-Konfiguration (dict) und ergänzt Standardwerte."""
    if not isinstance(config, dict):
        raise ValueError("Die Konfiguration muss ein JSON-Objekt sein")
    out = {
        "columns": int(config.get("columns", 3)),
        "refresh": max(2, int(config.get("refresh", 10))),
        "chart_refresh": max(30, int(config.get("chart_refresh", 300))),
        "size": str(config.get("size", "m")),
        "frame": bool(config.get("frame", True)),
        "mobile_stack": bool(config.get("mobile_stack", True)),
        "icon_base": str(config.get("icon_base", "/icons/")),
        "widgets": [],
    }
    if not 1 <= out["columns"] <= 12:
        raise ValueError("'columns' muss zwischen 1 und 12 liegen")
    if out["size"] not in SIZES:
        raise ValueError("'size' muss einer der Werte %s sein" % ", ".join(SIZES))
    if out["icon_base"] and not out["icon_base"].endswith("/"):
        out["icon_base"] += "/"
    widgets = config.get("widgets", [])
    if not isinstance(widgets, list):
        raise ValueError("'widgets' muss eine Liste sein")
    for i, w in enumerate(widgets):
        if not isinstance(w, dict):
            raise ValueError("Widget %d ist kein Objekt" % (i + 1))
        t = w.get("type")
        if t not in TYPES:
            raise ValueError("Widget %d: unbekannter Typ '%s' (erlaubt: %s)" % (i + 1, t, ", ".join(TYPES)))
        n = dict(DEFAULTS)
        n.update(w)
        n["width"] = max(1, min(out["columns"], int(n["width"])))
        n["label"] = str(n.get("label", ""))
        n["size"] = str(n.get("size", out["size"]))
        if n["size"] not in SIZES:
            raise ValueError("Widget %d: 'size' muss einer der Werte %s sein" % (i + 1, ", ".join(SIZES)))
        n["frame"] = bool(n.get("frame", out["frame"]))
        if n.get("font_size") is not None:
            n["font_size"] = _int(n["font_size"], "font_size", i, 6, 200)
        if t != "chart" and n.get("height") is not None:
            n["height"] = _int(n["height"], "height", i, 0, 2000)
        n["align"] = str(n.get("align", ""))
        if n["align"] not in ("", "left", "center", "right"):
            raise ValueError("Widget %d: 'align' muss left, center oder right sein" % (i + 1))
        for k in ("icon", "icon_on", "icon_off"):
            n[k] = _icon_url(n.get(k), out["icon_base"], i)
        if t == "switch" and (n["icon"] or n["icon_on"] or n["icon_off"]):
            n["icon"] = n["icon"] or n["icon_off"] or n["icon_on"]
            n["icon_on"] = n["icon_on"] or n["icon"]
            n["icon_off"] = n["icon_off"] or n["icon"]
        n["icon_size"] = _int(n.get("icon_size", ICON_SIZES[n["size"]]), "icon_size", i, 8, 400)
        if t in ("value", "text", "gauge", "bar") and not n.get("topic"):
            raise ValueError("Widget %d (%s): 'topic' fehlt" % (i + 1, t))
        if t in ("gauge", "bar"):
            n["min"] = _num(n.get("min", 0), "min", i)
            n["max"] = _num(n.get("max", 100), "max", i)
            if n["max"] <= n["min"]:
                raise ValueError("Widget %d: 'max' muss größer als 'min' sein" % (i + 1))
        if t in ("switch", "button"):
            n["command_topic"] = _command_topic(n, i, command_prefix)
        if t == "switch":
            n.setdefault("topic", n["command_topic"])
            n.setdefault("on_value", "ON")
            n.setdefault("off_value", "OFF")
            n.setdefault("payload_on", n["on_value"])
            n.setdefault("payload_off", n["off_value"])
            n.setdefault("text_on", "AN")
            n.setdefault("text_off", "AUS")
            n["show_text"] = bool(n.get("show_text", not (n["icon"] or n["icon_on"] or n["icon_off"])))
        if t == "button":
            n.setdefault("payload", "")
            n.setdefault("text", "" if n["icon"] else (n["label"] or "Ausführen"))
        if t in ("switch", "button"):
            if publish_allow is not None and not publish_allowed(n["command_topic"], publish_allow):
                raise ValueError("Widget %d: Senden auf '%s' ist nicht erlaubt (erlaubt: %s)"
                                 % (i + 1, n["command_topic"], ", ".join(publish_allow)))
            n["retain"] = bool(n.get("retain", False))
            n["qos"] = int(n.get("qos", 0))
            n["confirm"] = bool(n.get("confirm", False))
        if t == "chart":
            series = n.get("series")
            if isinstance(series, str):
                series = [{"source": series}]
            if not series or not isinstance(series, list):
                raise ValueError("Widget %d (chart): 'series' fehlt" % (i + 1))
            for s in series:
                if not isinstance(s, dict) or not s.get("source"):
                    raise ValueError("Widget %d (chart): jede Serie braucht 'source'" % (i + 1))
                if s.get("style", "line") not in CHART_STYLES:
                    raise ValueError("Widget %d (chart): unbekannter 'style' '%s' (erlaubt: %s)"
                                     % (i + 1, s.get("style"), ", ".join(CHART_STYLES)))
                if s.get("axis", "left") not in ("left", "right"):
                    raise ValueError("Widget %d (chart): 'axis' muss 'left' oder 'right' sein" % (i + 1))
            n["series"] = series
            n["hours"] = _num(n.get("hours", 24), "hours", i)
            n["height"] = _int(n.get("height", 220), "height", i, 80, 1200)
            if n.get("legend", "top") not in LEGEND:
                raise ValueError("Widget %d (chart): 'legend' muss einer der Werte %s sein" % (i + 1, ", ".join(LEGEND)))
        out["widgets"].append(n)
    return out


def parse(text, command_prefix="m3dash/stat/"):
    try:
        data = json.loads(text)
    except ValueError as e:
        raise ValueError("Kein gültiges JSON: %s" % e)
    return normalize(data, command_prefix=command_prefix)


def topics_of(config):
    ts = set()
    for w in config["widgets"]:
        if w.get("topic"):
            ts.add(w["topic"])
    return ts


def _extract(raw, path):
    """Wert aus JSON-Payload holen, z.B. path='temperature' oder 'state.brightness' oder 'list.0'."""
    if not path:
        return raw
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    for part in str(path).split("."):
        if isinstance(data, dict):
            data = data.get(part)
        elif isinstance(data, list):
            try:
                data = data[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
        if data is None:
            return None
    if isinstance(data, bool):
        return "true" if data else "false"
    if isinstance(data, (dict, list)):
        return json.dumps(data, ensure_ascii=False)
    return str(data)


def _fmt_num(v, decimals):
    s = ("%." + str(int(decimals)) + "f") % v
    return s.replace(".", ",")  # deutsche Schreibweise


def raw_value(mqtt, w):
    """(Wert als String oder None, Alter in Sekunden oder None)."""
    entry = mqtt.get(w.get("topic")) if w.get("topic") else None
    if entry is None:
        return None, None
    payload, ts = entry
    return _extract(payload, w.get("json_path")), time.time() - ts


def state(mqtt, w, idx, dash_id, theme="light"):
    """Zustand eines Widgets für Anzeige/Polling: dict mit t (Text), c (CSS-Klasse), p (Prozent), i (Bild)."""
    t = w["type"]
    if t in ("heading", "chart"):
        return None
    if t == "button" and not w.get("topic"):
        return None
    val, age = raw_value(mqtt, w)
    stale = val is None or (w["stale_after"] and age is not None and age > w["stale_after"])
    cls = "stale" if stale else ""
    mapping = w.get("map") or {}
    if val is None:
        return {"t": "–", "c": "stale", "p": 0, "i": w.get("icon_off", "") if t == "switch" else ""}

    if t == "switch":
        on = val == str(w["on_value"])
        off = val == str(w["off_value"])
        text = w["text_on"] if on else (w["text_off"] if off else mapping.get(val, val))
        icon = (w["icon_on"] if on else w["icon_off"]) if w.get("icon_on") or w.get("icon_off") else ""
        return {"t": text, "c": ("on" if on else "off") + (" stale" if stale else ""), "p": 0, "i": icon}

    if val in mapping:
        return {"t": str(mapping[val]), "c": cls, "p": 0, "i": ""}

    num = None
    try:
        num = float(val.replace(",", "."))
    except (ValueError, AttributeError):
        pass

    if t in ("value", "button") or (t in ("gauge", "bar") and num is None):
        if num is not None and t != "button":
            text = _fmt_num(num, w["decimals"])
        else:
            text = val
        if w["unit"]:
            text = text + " " + w["unit"]
        return {"t": text, "c": cls + _level(w, num), "p": 0, "i": ""}

    if t == "text":
        return {"t": val, "c": cls, "p": 0, "i": ""}

    text = _fmt_num(num, w["decimals"]) + ((" " + w["unit"]) if w["unit"] else "")
    pct = (num - w["min"]) / (w["max"] - w["min"]) * 100.0
    pct = max(0.0, min(100.0, pct))
    if t == "bar":
        return {"t": text, "c": cls + _level(w, num), "p": round(pct, 1), "i": ""}
    # gauge: Bild-URL mit gerundetem Wert, damit der Browser-Cache greift
    q = round(num, int(w["decimals"]))
    return {"t": text, "c": cls + _level(w, num), "p": round(pct, 1),
            "i": "/img/gauge/%d/%d.png?th=%s&sz=%s&v=%s" % (dash_id, idx, theme, w["size"], q)}


def _level(w, num):
    """Optionale Schwellwerte: "warn_above", "warn_below" -> CSS-Klasse 'warn'."""
    if num is None:
        return ""
    if "warn_above" in w and num > float(w["warn_above"]):
        return " warn"
    if "warn_below" in w and num < float(w["warn_below"]):
        return " warn"
    return ""


EXAMPLE = {
    "columns": 3,
    "refresh": 10,
    "chart_refresh": 300,
    "widgets": [
        {"type": "heading", "label": "Wohnzimmer", "width": 3},
        {"type": "value", "label": "Temperatur", "topic": "zigbee2mqtt/wohnzimmer_sensor",
         "json_path": "temperature", "unit": "°C", "decimals": 1, "stale_after": 3600},
        {"type": "gauge", "label": "Luftfeuchte", "topic": "zigbee2mqtt/wohnzimmer_sensor",
         "json_path": "humidity", "unit": "%", "decimals": 0, "min": 0, "max": 100, "warn_above": 65},
        {"type": "bar", "label": "Batterie", "topic": "zigbee2mqtt/wohnzimmer_sensor",
         "json_path": "battery", "unit": "%", "decimals": 0, "min": 0, "max": 100, "warn_below": 20},
        {"type": "switch", "label": "Stehlampe", "topic": "zigbee2mqtt/stehlampe", "json_path": "state",
         "name": "stehlampe", "payload_on": "{\"state\":\"ON\"}",
         "payload_off": "{\"state\":\"OFF\"}"},
        {"type": "button", "label": "Garagentor", "text": "Öffnen/Schließen",
         "name": "garagentor", "payload": "TOGGLE", "confirm": True},
        {"type": "text", "label": "Status Waschmaschine", "topic": "haushalt/waschmaschine/status",
         "map": {"0": "Aus", "1": "Läuft", "2": "Fertig"}},
        {"type": "chart", "label": "Temperatur letzte 24 h", "width": 3, "hours": 24,
         "series": [{"source": "Wohnzimmer_Sensor:temperature", "label": "Wohnzimmer"}]},
    ],
}
