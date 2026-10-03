import functools
import secrets

from flask import abort, current_app, g, redirect, request, session, url_for


def current_user():
    if "user" not in g:
        uid = session.get("uid")
        g.user = current_app.store.user(uid) if uid else None
    return g.user


def login_required(fn):
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        if current_user() is None:
            return redirect(url_for("views.login", next=request.full_path))
        return fn(*a, **kw)
    return wrapper


def admin_required(fn):
    @functools.wraps(fn)
    @login_required
    def wrapper(*a, **kw):
        if current_user()["role"] != "admin":
            abort(403)
        return fn(*a, **kw)
    return wrapper


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)
    return session["csrf"]


def check_csrf():
    sent = request.form.get("csrf") or request.headers.get("X-CSRF-Token")
    if not sent or not secrets.compare_digest(sent, session.get("csrf", "")):
        abort(400, "Ungültiges Formular-Token, bitte Seite neu laden.")


def theme_for(user):
    """Explizites ?theme= > Benutzereinstellung > Erkennung (Kindle -> E-Ink)."""
    t = request.args.get("theme")
    if t in ("light", "dark", "eink"):
        session["theme"] = t
    elif t == "auto":
        session.pop("theme", None)
    t = session.get("theme")
    if t in ("light", "dark", "eink"):
        return t
    if user and user["theme"] in ("light", "dark", "eink"):
        return user["theme"]
    ua = request.headers.get("User-Agent", "")
    if "Kindle" in ua or "Kobo" in ua or "PocketBook" in ua:
        return "eink"
    return "light"
