import json
import os
import time

from flask import (Blueprint, Response, abort, current_app, flash, jsonify, redirect, render_template,
                   request, send_from_directory, session, url_for)

from . import render, widgets
from .auth import check_csrf, csrf_token, current_user, login_required, theme_for

bp = Blueprint("views", __name__)

_parsed = {}
_failed_logins = {}


def load_dashboard(slug=None, dash_id=None):
    row = current_app.store.dashboard_by_slug(slug) if slug else current_app.store.dashboard(dash_id)
    if row is None:
        abort(404)
    if not current_app.store.can_view(current_user(), row["id"]):
        abort(403)
    key = (row["id"], row["updated"])
    if key not in _parsed:
        _parsed[key] = widgets.parse(row["config"], current_app.cfg.MQTT_COMMAND_PREFIX)
    return row, _parsed[key]


@bp.app_context_processor
def inject():
    u = current_user()
    return {
        "user": u,
        "csrf_token": csrf_token,
        "theme": theme_for(u),
        "nav": current_app.store.dashboards_for(u) if u else [],
    }


@bp.after_app_request
def no_cache(resp):
    if resp.mimetype in ("text/html", "application/json"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


# --- Anmeldung -------------------------------------------------------------

@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        check_csrf()
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
        fails = [t for t in _failed_logins.get(ip, []) if time.time() - t < 600]
        if len(fails) >= 10:
            flash("Zu viele Fehlversuche. Bitte in 10 Minuten erneut versuchen.")
            return render_template("login.html"), 429
        u = current_app.store.check_login(request.form.get("username", "").strip(), request.form.get("password", ""))
        if u is None:
            _failed_logins[ip] = fails + [time.time()]
            flash("Benutzername oder Passwort falsch.")
            return render_template("login.html"), 401
        _failed_logins.pop(ip, None)
        session.clear()
        session.permanent = bool(request.form.get("remember"))
        session["uid"] = u["id"]
        nxt = request.args.get("next", "")
        if not nxt.startswith("/") or nxt.startswith("//"):
            nxt = url_for("views.index")
        return redirect(nxt)
    return render_template("login.html")


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("views.login"))


# --- Dashboards ------------------------------------------------------------

@bp.route("/")
@login_required
def index():
    u = current_user()
    dashes = current_app.store.dashboards_for(u)
    if u["start_dashboard"]:
        for d in dashes:
            if d["id"] == u["start_dashboard"]:
                return redirect(url_for("views.dashboard", slug=d["slug"]))
    if len(dashes) == 1:
        return redirect(url_for("views.dashboard", slug=dashes[0]["slug"]))
    return render_template("index.html", dashboards=dashes)


@bp.route("/d/<slug>")
@login_required
def dashboard(slug):
    row, cfg = load_dashboard(slug=slug)
    th = theme_for(current_user())
    refresh = cfg["refresh"] * (3 if th == "eink" else 1)  # E-Ink: seltener, schont Akku und Display
    states = [widgets.state(current_app.sources, w, i, row["id"], th) for i, w in enumerate(cfg["widgets"])]
    return render_template("dashboard.html", dash=row, cfg=cfg, states=states, refresh=refresh,
                           chart_bucket=_chart_bucket(cfg), now=time.strftime("%H:%M:%S"),
                           can_act=current_user()["role"] in ("operator", "admin"))


def _chart_bucket(cfg):
    return int(time.time() // cfg["chart_refresh"])


@bp.route("/api/d/<slug>/state")
@login_required
def dashboard_state(slug):
    row, cfg = load_dashboard(slug=slug)
    out = {}
    th = theme_for(current_user())
    for i, w in enumerate(cfg["widgets"]):
        s = widgets.state(current_app.sources, w, i, row["id"], th)
        if s is not None:
            out[str(i)] = s
    return jsonify({"w": out, "now": time.strftime("%H:%M:%S"), "cb": _chart_bucket(cfg),
                    "mqtt": current_app.mqtt.connected})


@bp.route("/d/<slug>/action/<int:idx>", methods=["POST"])
@login_required
def action(slug, idx):
    check_csrf()
    u = current_user()
    if u["role"] not in ("operator", "admin"):
        abort(403)
    row, cfg = load_dashboard(slug=slug)
    if idx >= len(cfg["widgets"]):
        abort(404)
    w = cfg["widgets"][idx]
    if w["type"] == "switch":
        val, _ = widgets.raw_value(current_app.sources, w)
        payload = w["payload_off"] if widgets.is_on(w, val) else w["payload_on"]
    elif w["type"] == "button":
        payload = w["payload"]
    else:
        abort(400)
    if not isinstance(payload, str):
        payload = json.dumps(payload, ensure_ascii=False)
    if not widgets.publish_allowed(w["command_topic"], current_app.cfg.MQTT_PUBLISH_ALLOW):
        abort(403, "Dieses Topic ist zum Senden nicht freigegeben.")
    # Gesendet wird über den Broker des Widgets, bei FHEM-Quellen über den Standard-Broker "mqtt"
    broker = current_app.sources.get(w["source"])
    if broker is None or broker.kind != "mqtt":
        broker = current_app.sources.mqtt
    ok = broker.connected and broker.publish(
        w["command_topic"], payload, retain=w["retain"], qos=w["qos"])
    current_app.store.log_action(u["username"], w["command_topic"], payload if ok else payload + "  [FEHLER]")
    msg = "Gesendet: %s" % (w["label"] or w["command_topic"]) if ok else "Senden fehlgeschlagen (MQTT nicht verbunden)"
    if request.form.get("ajax"):
        return jsonify({"ok": ok, "msg": msg}), (200 if ok else 503)
    flash(msg)
    return redirect(url_for("views.dashboard", slug=slug))


# --- Bilder ----------------------------------------------------------------

def _img_theme():
    # Das Theme steht in der Bild-URL, damit der Browser-Cache bei einem Wechsel nicht alte Bilder zeigt
    th = request.args.get("th")
    return th if th in render.THEMES else theme_for(current_user())


def _png(data, max_age):
    resp = Response(data, mimetype="image/png")
    resp.headers["Cache-Control"] = "private, max-age=%d" % max_age
    return resp


@bp.route("/img/gauge/<int:dash_id>/<int:idx>.png")
@login_required
def gauge_png(dash_id, idx):
    row, cfg = load_dashboard(dash_id=dash_id)
    if idx >= len(cfg["widgets"]) or cfg["widgets"][idx]["type"] != "gauge":
        abort(404)
    w = cfg["widgets"][idx]
    try:
        v = float(request.args.get("v"))
    except (TypeError, ValueError):
        v = None
    width = int((160 + 80 * min(w["width"], 2)) * widgets.GAUGE_SCALE[w["size"]])
    return _png(render.gauge(v, w, _img_theme(), width=width), 86400)


@bp.route("/img/chart/<int:dash_id>/<int:idx>.png")
@login_required
def chart_png(dash_id, idx):
    row, cfg = load_dashboard(dash_id=dash_id)
    if idx >= len(cfg["widgets"]) or cfg["widgets"][idx]["type"] != "chart":
        abort(404)
    w = cfg["widgets"][idx]
    try:
        width = max(200, min(1600, int(request.args.get("w", 0)) or 300 * w["width"]))
    except ValueError:
        width = 300 * w["width"]
    db = current_app.sources.get(w["source"])

    def load():
        data = []
        for s in w["series"]:
            try:
                if db is None or db.kind != "fhemdb":
                    raise RuntimeError("Quelle '%s' ist keine FHEM-Datenbank" % w["source"])
                pts = db.series(s["source"], s.get("hours", w["hours"]), s.get("query"), s.get("map"),
                                current_app.cfg.HISTORY_MAX_POINTS)
                data.append((s.get("label", s["source"]), pts, "", s))
            except Exception as e:
                current_app.logger.warning("Historie für %s: %s", s["source"], e)
                data.append((s.get("label", s["source"]), [], "Historie nicht verfügbar", s))
        return data

    key = (dash_id, row["updated"], idx)
    png = render.chart(load, w, _img_theme(), width=width, height=w["height"],
                       cache_key=key, ttl=current_app.cfg.CHART_CACHE_SECONDS)
    return _png(png, current_app.cfg.CHART_CACHE_SECONDS)


@bp.route("/icons/<path:name>")
@login_required
def icon(name):
    # Eigene Symbole: Dateien im Volume unter /data/icons (hochladen unter Verwaltung > Symbole)
    resp = send_from_directory(os.path.join(current_app.cfg.DATA_DIR, "icons"), name, max_age=86400)
    resp.headers["Cache-Control"] = "private, max-age=86400"
    # SVG-Dateien direkt geöffnet dürfen kein Skript ausführen
    resp.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; img-src data:"
    return resp


# --- Eigenes Konto ---------------------------------------------------------

@bp.route("/account", methods=["GET", "POST"])
@login_required
def account():
    u = current_user()
    store = current_app.store
    if request.method == "POST":
        check_csrf()
        if request.form.get("new_password"):
            if not store.check_login(u["username"], request.form.get("old_password", "")):
                flash("Aktuelles Passwort ist falsch.")
            elif len(request.form["new_password"]) < 6:
                flash("Das neue Passwort muss mindestens 6 Zeichen haben.")
            else:
                store.set_password(u["id"], request.form["new_password"])
                flash("Passwort geändert.")
        else:
            start = request.form.get("start_dashboard")
            store.update_user(u["id"], theme=request.form.get("theme", "auto"),
                              start_dashboard=int(start) if start else None)
            session.pop("theme", None)
            flash("Einstellungen gespeichert.")
        return redirect(url_for("views.account"))
    return render_template("account.html")
