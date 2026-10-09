import json
import os
import re
import time

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from werkzeug.utils import secure_filename

from . import fhemdb, sources as sources_mod, widgets
from .auth import admin_required, check_csrf, current_user
from .store import ROLES

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.route("/")
@admin_required
def status():
    return render_template("admin/status.html", cfg=current_app.cfg, sources=current_app.sources.all(),
                           kinds=sources_mod.KINDS, log=current_app.store.action_log(50),
                           fmt_ts=lambda t: time.strftime("%d.%m. %H:%M:%S", time.localtime(t)))


# --- Benutzer --------------------------------------------------------------

@bp.route("/users")
@admin_required
def users():
    return render_template("admin/users.html", users=current_app.store.users())


@bp.route("/users/new", methods=["GET", "POST"])
@bp.route("/users/<int:uid>", methods=["GET", "POST"])
@admin_required
def user_edit(uid=None):
    store = current_app.store
    u = store.user(uid) if uid else None
    if uid and u is None:
        abort(404)
    if request.method == "POST":
        check_csrf()
        role = request.form.get("role", "viewer")
        if role not in ROLES:
            abort(400)
        pw = request.form.get("password", "")
        if u is None:
            name = request.form.get("username", "").strip()
            if not re.match(r"^[A-Za-z0-9_.@-]{2,40}$", name):
                flash("Benutzername: 2–40 Zeichen, nur Buchstaben, Ziffern und _ . @ -")
                return render_template("admin/user_edit.html", u=None, dashboards=store.dashboards(), access=set(), roles=ROLES)
            if store.user_by_name(name):
                flash("Benutzername ist schon vergeben.")
                return render_template("admin/user_edit.html", u=None, dashboards=store.dashboards(), access=set(), roles=ROLES)
            if len(pw) < 6:
                flash("Passwort muss mindestens 6 Zeichen haben.")
                return render_template("admin/user_edit.html", u=None, dashboards=store.dashboards(), access=set(), roles=ROLES)
            uid = store.create_user(name, pw, role)
        else:
            if u["role"] == "admin" and role != "admin" and store.admin_count() <= 1:
                flash("Der letzte Admin kann nicht herabgestuft werden.")
                return redirect(url_for("admin.user_edit", uid=uid))
            if pw:
                if len(pw) < 6:
                    flash("Passwort muss mindestens 6 Zeichen haben.")
                    return redirect(url_for("admin.user_edit", uid=uid))
                store.set_password(uid, pw)
        start = request.form.get("start_dashboard")
        store.update_user(uid, role=role, theme=request.form.get("theme", "auto"),
                          start_dashboard=int(start) if start else None)
        store.set_access(uid, [int(d) for d in request.form.getlist("dashboards")])
        flash("Benutzer gespeichert.")
        return redirect(url_for("admin.users"))
    return render_template("admin/user_edit.html", u=u, dashboards=store.dashboards(),
                           access=store.access_ids(uid) if uid else set(), roles=ROLES)


@bp.route("/users/<int:uid>/delete", methods=["POST"])
@admin_required
def user_delete(uid):
    check_csrf()
    store = current_app.store
    u = store.user(uid)
    if u is None:
        abort(404)
    if u["id"] == current_user()["id"]:
        flash("Das eigene Konto kann nicht gelöscht werden.")
    elif u["role"] == "admin" and store.admin_count() <= 1:
        flash("Der letzte Admin kann nicht gelöscht werden.")
    else:
        store.delete_user(uid)
        flash("Benutzer gelöscht.")
    return redirect(url_for("admin.users"))


# --- Dashboards ------------------------------------------------------------

@bp.route("/dashboards")
@admin_required
def dashboards():
    return render_template("admin/dashboards.html", dashboards=current_app.store.dashboards())


@bp.route("/dashboards/new", methods=["GET", "POST"])
@bp.route("/dashboards/<int:did>", methods=["GET", "POST"])
@admin_required
def dashboard_edit(did=None):
    store = current_app.store
    d = store.dashboard(did) if did else None
    if did and d is None:
        abort(404)
    form = {
        "title": d["title"] if d else "",
        "slug": d["slug"] if d else "",
        "sort": d["sort"] if d else 0,
        "config": d["config"] if d else json.dumps(widgets.EXAMPLE, ensure_ascii=False, indent=2),
    }
    if request.method == "POST":
        check_csrf()
        form.update({k: request.form.get(k, "") for k in ("title", "slug", "sort", "config")})
        slug = form["slug"].strip().lower() or re.sub(r"[^a-z0-9]+", "-", form["title"].lower()).strip("-")
        error = None
        if not form["title"].strip():
            error = "Titel fehlt."
        elif not re.match(r"^[a-z0-9][a-z0-9-]{0,40}$", slug):
            error = "Kurzname (URL): nur a–z, 0–9 und Bindestrich."
        else:
            other = store.dashboard_by_slug(slug)
            if other and other["id"] != did:
                error = "Kurzname ist schon vergeben."
        if error is None:
            try:
                data = json.loads(form["config"])
                checked = widgets.normalize(data, current_app.cfg.MQTT_PUBLISH_ALLOW, current_app.cfg.MQTT_COMMAND_PREFIX)  # nur prüfen; gespeichert wird die Eingabe des Admins
                widgets.check_sources(checked, current_app.sources.kinds())
            except ValueError as e:
                error = str(e)
        if error:
            flash(error)
            form["slug"] = slug
            return render_template("admin/dashboard_edit.html", d=d, form=form, types=widgets.TYPES,
                               prefix=current_app.cfg.MQTT_COMMAND_PREFIX), 400
        try:
            sort = int(form["sort"] or 0)
        except ValueError:
            sort = 0
        did = store.save_dashboard(did, slug, form["title"].strip(), data, sort)
        flash("Dashboard gespeichert.")
        if request.form.get("view"):
            return redirect(url_for("views.dashboard", slug=slug))
        return redirect(url_for("admin.dashboard_edit", did=did))
    return render_template("admin/dashboard_edit.html", d=d, form=form, types=widgets.TYPES,
                               prefix=current_app.cfg.MQTT_COMMAND_PREFIX)


@bp.route("/dashboards/<int:did>/delete", methods=["POST"])
@admin_required
def dashboard_delete(did):
    check_csrf()
    current_app.store.delete_dashboard(did)
    flash("Dashboard gelöscht.")
    return redirect(url_for("admin.dashboards"))


# --- Werte-Browser (MQTT-Topics bzw. FHEM-Readings) ------------------------

@bp.route("/topics")
@admin_required
def topics():
    q = request.args.get("q", "").strip()
    src = current_app.sources.get(request.args.get("source") or "mqtt")
    if src is None:
        abort(404)
    rows = []
    now = time.time()
    all_rows = src.topics()
    for topic, (payload, ts) in all_rows:
        if q and q.lower() not in topic.lower():
            continue
        rows.append((topic, payload[:300], int(now - ts)))
        if len(rows) >= 500:
            break
    return render_template("admin/topics.html", rows=rows, q=q, total=len(all_rows), src=src,
                           sources=current_app.sources.all())


# --- Quellen (Konnektoren) -------------------------------------------------

@bp.route("/sources")
@admin_required
def sources():
    return render_template("admin/sources.html", sources=current_app.sources.all(), kinds=sources_mod.KINDS)


def _settings_from_form(kind, old):
    """Einstellungen aus dem Formular; leeres Passwort = bisheriges behalten."""
    f = request.form
    if kind == "mqtt":
        s = {
            "host": f.get("host", "").strip(),
            "port": int(f.get("port") or 1883),
            "user": f.get("user", "").strip(),
            "tls": bool(f.get("tls")),
            "subscribe": [t.strip() for t in f.get("subscribe", "").split(",") if t.strip()] or ["#"],
        }
        if not s["host"]:
            raise ValueError("Host fehlt.")
    else:
        s = {
            "backend": f.get("backend", "mysql"),
            "host": f.get("host", "").strip(),
            "port": int(f.get("port") or 0),
            "database": f.get("database", "").strip(),
            "user": f.get("user", "").strip(),
            "path": f.get("path", "").strip(),
            "interval": max(2, int(f.get("interval") or 10)),
            "current_query": f.get("current_query", "").strip() or fhemdb.CURRENT_QUERY,
            "history_query": f.get("history_query", "").strip() or fhemdb.HISTORY_QUERY,
        }
        if s["backend"] not in fhemdb.BACKENDS:
            raise ValueError("Unbekanntes Backend.")
        if s["backend"] == "sqlite" and not s["path"]:
            raise ValueError("Pfad zur SQLite-Datei fehlt.")
        if s["backend"] != "sqlite" and not (s["host"] and s["database"]):
            raise ValueError("Host und Datenbank fehlen.")
    s["password"] = f.get("password", "") or (old or {}).get("password", "")
    if f.get("clear_password"):
        s["password"] = ""
    return s


@bp.route("/sources/new/<kind>", methods=["GET", "POST"])
@bp.route("/sources/<name>/edit", methods=["GET", "POST"])
@admin_required
def source_edit(kind=None, name=None):
    reg = current_app.sources
    src = reg.get(name) if name else None
    if name and src is None:
        abort(404)
    if src is not None:
        if src.builtin:
            abort(403)
        kind = src.kind
    if kind not in sources_mod.KINDS:
        abort(404)
    settings = dict(src.s) if src else dict(sources_mod.KINDS[kind][2])
    form_name = name or ""
    if request.method == "POST":
        check_csrf()
        form_name = request.form.get("name", "").strip().lower()
        error = None
        try:
            settings = _settings_from_form(kind, src.s if src else None)
        except ValueError as e:
            error = str(e)
        if error is None:
            if not sources_mod.NAME_RE.match(form_name):
                error = "Name: 1–31 Zeichen, nur a–z, 0–9, _ und -."
            elif form_name != name and reg.get(form_name) is not None:
                error = "Der Name ist schon vergeben."
        if error is None:
            try:
                new = reg.save(form_name, kind, settings, old_name=name)
            except ValueError as e:
                error = str(e)
        if error:
            flash(error)
            return render_template("admin/source_edit.html", kind=kind, kinds=sources_mod.KINDS, name=form_name,
                                   s=settings, src=src, backends=fhemdb.BACKENDS), 400
        msg = "Quelle '%s' gespeichert." % form_name
        if kind == "fhemdb":  # FHEM-Datenbank gleich prüfen, MQTT verbindet sich im Hintergrund
            msg += " Verbindung: " + ("OK" if new.ping() else "fehlgeschlagen (%s)" % new.last_error)
        if name and form_name != name:
            msg += " Dashboards, die '%s' verwenden, bitte anpassen." % name
        flash(msg)
        return redirect(url_for("admin.sources"))
    return render_template("admin/source_edit.html", kind=kind, kinds=sources_mod.KINDS, name=form_name,
                           s=settings, src=src, backends=fhemdb.BACKENDS)


@bp.route("/sources/<name>/delete", methods=["POST"])
@admin_required
def source_delete(name):
    check_csrf()
    src = current_app.sources.get(name)
    if src is None:
        abort(404)
    if src.builtin:
        abort(403)
    current_app.sources.delete(name)
    flash("Quelle '%s' gelöscht." % name)
    return redirect(url_for("admin.sources"))


# --- Symbole ---------------------------------------------------------------

ICON_EXT = (".png", ".gif", ".jpg", ".jpeg", ".svg")


def _icon_dir():
    path = os.path.join(current_app.cfg.DATA_DIR, "icons")
    os.makedirs(path, exist_ok=True)
    return path


@bp.route("/icons", methods=["GET", "POST"])
@admin_required
def icons():
    folder = _icon_dir()
    if request.method == "POST":
        check_csrf()
        if request.form.get("delete"):
            name = secure_filename(request.form["delete"])
            if name and os.path.isfile(os.path.join(folder, name)):
                os.remove(os.path.join(folder, name))
                flash("Symbol %s gelöscht." % name)
            return redirect(url_for("admin.icons"))
        saved = []
        for f in request.files.getlist("files"):
            name = secure_filename(f.filename or "").lower()
            if not name:
                continue
            if not name.endswith(ICON_EXT):
                flash("%s übersprungen: nur %s erlaubt." % (name, ", ".join(ICON_EXT)))
                continue
            f.save(os.path.join(folder, name))
            saved.append(name)
        if saved:
            flash("Hochgeladen: %s" % ", ".join(saved))
        return redirect(url_for("admin.icons"))
    names = sorted(n for n in os.listdir(folder) if n.lower().endswith(ICON_EXT))
    return render_template("admin/icons.html", names=names)
