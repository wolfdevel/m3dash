import json
import re
import time

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for

from . import widgets
from .auth import admin_required, check_csrf, current_user
from .store import ROLES

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.route("/")
@admin_required
def status():
    m = current_app.mqtt
    h = current_app.history
    db_ok = h.ping()
    return render_template("admin/status.html", mqtt=m, cfg=current_app.cfg, db_ok=db_ok, db_error=h.last_error,
                           topic_count=len(m.values), log=current_app.store.action_log(50),
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
                widgets.normalize(data, current_app.cfg.MQTT_PUBLISH_ALLOW)  # nur prüfen; gespeichert wird die Eingabe des Admins
            except ValueError as e:
                error = str(e)
        if error:
            flash(error)
            form["slug"] = slug
            return render_template("admin/dashboard_edit.html", d=d, form=form, types=widgets.TYPES), 400
        try:
            sort = int(form["sort"] or 0)
        except ValueError:
            sort = 0
        did = store.save_dashboard(did, slug, form["title"].strip(), data, sort)
        flash("Dashboard gespeichert.")
        if request.form.get("view"):
            return redirect(url_for("views.dashboard", slug=slug))
        return redirect(url_for("admin.dashboard_edit", did=did))
    return render_template("admin/dashboard_edit.html", d=d, form=form, types=widgets.TYPES)


@bp.route("/dashboards/<int:did>/delete", methods=["POST"])
@admin_required
def dashboard_delete(did):
    check_csrf()
    current_app.store.delete_dashboard(did)
    flash("Dashboard gelöscht.")
    return redirect(url_for("admin.dashboards"))


# --- Topic-Browser ---------------------------------------------------------

@bp.route("/topics")
@admin_required
def topics():
    q = request.args.get("q", "").strip()
    rows = []
    now = time.time()
    for topic, (payload, ts) in current_app.mqtt.topics():
        if q and q.lower() not in topic.lower():
            continue
        rows.append((topic, payload[:300], int(now - ts)))
        if len(rows) >= 500:
            break
    return render_template("admin/topics.html", rows=rows, q=q, total=len(current_app.mqtt.values))
