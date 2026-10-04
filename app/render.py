"""Serverseitiges Rendern von Gauges und Diagrammen als PNG.

PNG funktioniert in jedem Browser, auch ohne SVG/Canvas (Kindle 4). Für E-Ink wird
auf Graustufen umgerechnet.
"""
import io
import math
import threading
import time

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Wedge  # noqa: E402
from PIL import Image  # noqa: E402

_lock = threading.Lock()  # pyplot ist nicht threadsicher
_cache = {}
_CACHE_MAX = 300

THEMES = {
    "light": {"bg": "#ffffff", "fg": "#222222", "muted": "#888888", "track": "#e3e3e3", "grid": "#e8e8e8",
              "series": ["#1f6fb5", "#d9822b", "#2e9d5b", "#b8432f", "#7a52b3", "#5a6b7a"],
              "accent": "#1f6fb5", "warn": "#c0392b"},
    "dark": {"bg": "#1b1e22", "fg": "#eeeeee", "muted": "#9aa3ad", "track": "#3a3f45", "grid": "#2c3136",
             "series": ["#5aa9f0", "#f0a35a", "#5ccf8c", "#ef7b66", "#b392f0", "#9fb0c0"],
             "accent": "#5aa9f0", "warn": "#ef7b66"},
    "eink": {"bg": "#ffffff", "fg": "#000000", "muted": "#444444", "track": "#cccccc", "grid": "#bbbbbb",
             "series": ["#000000", "#555555", "#888888", "#222222", "#666666", "#999999"],
             "accent": "#000000", "warn": "#000000"},
}
LINESTYLES = ["-", "--", ":", "-."]


def _cached(key, ttl, fn):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    data = fn()
    if len(_cache) > _CACHE_MAX:
        for k in sorted(_cache, key=lambda k: _cache[k][0])[: _CACHE_MAX // 2]:
            _cache.pop(k, None)
    _cache[key] = (now, data)
    return data


def _to_png(fig, theme):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, facecolor=THEMES[theme]["bg"])
    plt.close(fig)
    if theme == "eink":
        img = Image.open(io.BytesIO(buf.getvalue())).convert("L")
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def gauge(value, w, theme, width=240):
    key = ("g", value, w["min"], w["max"], w.get("unit"), w.get("decimals"), str(w.get("zones")),
           w.get("warn_above"), w.get("warn_below"), theme, width)
    return _cached(key, 3600, lambda: _gauge(value, w, theme, width))


def _gauge(value, w, theme, width):
    c = THEMES[theme]
    lo, hi = w["min"], w["max"]
    frac = 0.0 if value is None else max(0.0, min(1.0, (value - lo) / (hi - lo)))
    warn = value is not None and (
        ("warn_above" in w and value > float(w["warn_above"])) or ("warn_below" in w and value < float(w["warn_below"]))
    )
    with _lock:
        h = width * 0.62
        fig = plt.figure(figsize=(width / 100.0, h / 100.0), dpi=100)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_xlim(-1.15, 1.15)
        ax.set_ylim(-0.35, 1.1)
        ax.set_aspect("equal")
        ax.axis("off")
        fig.patch.set_facecolor(c["bg"])
        ax.add_patch(Wedge((0, 0), 1.0, 0, 180, width=0.25, color=c["track"]))
        for z in w.get("zones") or []:  # [[von, bis, "#farbe"], ...]
            try:
                a1 = 180 - (float(z[1]) - lo) / (hi - lo) * 180
                a2 = 180 - (float(z[0]) - lo) / (hi - lo) * 180
                ax.add_patch(Wedge((0, 0), 1.0, max(0, a1), min(180, a2), width=0.08,
                                   color=z[2] if theme != "eink" else c["muted"]))
            except (TypeError, ValueError, IndexError):
                pass
        if value is not None:
            ax.add_patch(Wedge((0, 0), 1.0, 180 - frac * 180, 180, width=0.25,
                               color=c["warn"] if warn else c["accent"]))
            ang = math.radians(180 - frac * 180)
            ax.plot([0, 0.78 * math.cos(ang)], [0, 0.78 * math.sin(ang)], color=c["fg"], lw=2.5,
                    solid_capstyle="round")
            ax.add_patch(plt.Circle((0, 0), 0.06, color=c["fg"]))
            txt = ("%." + str(int(w.get("decimals", 1))) + "f") % value
            txt = txt.replace(".", ",") + ((" " + w["unit"]) if w.get("unit") else "")
        else:
            txt = "–"
        ax.text(0, -0.24, txt, ha="center", va="center", fontsize=max(10, width / 14.0),
                color=c["fg"], fontweight="bold")
        fmt = lambda v: ("%g" % v).replace(".", ",")  # noqa: E731
        ax.text(-0.875, -0.1, fmt(lo), ha="center", va="center", fontsize=max(7, width / 28.0), color=c["muted"])
        ax.text(0.875, -0.1, fmt(hi), ha="center", va="center", fontsize=max(7, width / 28.0), color=c["muted"])
        return _to_png(fig, theme)


def chart(series_data, w, theme, width=600, height=220, cache_key=None, ttl=60):
    """series_data: [(label, [(datetime, float), ...]), ...]"""
    if cache_key is not None:
        return _cached(("c", cache_key, theme, width, height), ttl,
                       lambda: _chart(series_data(), w, theme, width, height))
    return _chart(series_data, w, theme, width, height)


STYLES = ("line", "step", "area", "bar", "points")


def _draw(ax, style, xs, ys, kw):
    if style == "step":
        ax.step(xs, ys, where="post", **kw)
    elif style == "area":
        ax.plot(xs, ys, **kw)
        ax.fill_between(xs, ys, step=None, color=kw["color"], alpha=0.25, linewidth=0)
    elif style == "bar":
        span = mdates.date2num(xs[-1]) - mdates.date2num(xs[0]) if len(xs) > 1 else 1 / 24.0
        bw = 0.8 * span / max(1, len(xs))  # Breite in Tagen
        ax.bar(xs, ys, width=bw, color=kw["color"], label=kw["label"], align="center", linewidth=0)
    elif style == "points":
        ax.plot(xs, ys, marker="o", markersize=3, linestyle="none", color=kw["color"], label=kw["label"])
    else:
        ax.plot(xs, ys, **kw)


def _style_axis(ax, c, theme, side):
    for spine in ("top", "right" if side == "left" else "left"):
        ax.spines[spine].set_visible(False)
    for spine in (side, "bottom"):
        ax.spines[spine].set_color(c["muted"])
    ax.tick_params(colors=c["muted"], labelsize=10 if theme == "eink" else 8)


def _limits(ax, lo, hi):
    if lo is not None:
        ax.set_ylim(bottom=float(lo))
    if hi is not None:
        ax.set_ylim(top=float(hi))


def _chart(series_data, w, theme, width, height):
    """series_data: [(label, punkte, fehler, serien_config), ...]"""
    c = THEMES[theme]
    default_style = "step" if w.get("step") else w.get("style", "line")
    with _lock:
        fig = plt.figure(figsize=(width / 100.0, height / 100.0), dpi=100)
        ax = fig.add_subplot(111)
        fig.patch.set_facecolor(c["bg"])
        ax.set_facecolor(c["bg"])
        _style_axis(ax, c, theme, "left")
        ax.grid(True, color=c["grid"], linewidth=0.6)
        ax2 = None
        if any((s or {}).get("axis") == "right" for _, _, _, s in series_data):
            ax2 = ax.twinx()
            _style_axis(ax2, c, theme, "right")
        any_data = False
        for i, (label, pts, err, s) in enumerate(series_data):
            if not pts:
                continue
            any_data = True
            s = s or {}
            target = ax2 if s.get("axis") == "right" and ax2 is not None else ax
            color = s.get("color") if theme != "eink" and s.get("color") else c["series"][i % len(c["series"])]
            kw = dict(color=color, lw=1.6 if theme != "eink" else 2.0, label=label)
            if theme == "eink":
                kw["linestyle"] = LINESTYLES[i % len(LINESTYLES)]
            _draw(target, s.get("style", default_style), [p[0] for p in pts], [p[1] for p in pts], kw)
        if not any_data:
            msg = "; ".join(e for _, _, e, _ in series_data if e) or "Keine Daten im Zeitraum"
            ax.text(0.5, 0.5, msg[:120], ha="center", va="center", transform=ax.transAxes,
                    color=c["muted"], fontsize=9, wrap=True)
            ax.set_xticks([])
            ax.set_yticks([])
            if ax2 is not None:
                ax2.set_yticks([])
        else:
            hours = float(w.get("hours", 24))
            fmt = "%H:%M" if hours <= 36 else "%d.%m."
            ax.xaxis.set_major_formatter(mdates.DateFormatter(fmt))
            ax.xaxis.set_major_locator(mdates.AutoDateLocator(maxticks=max(3, width // 90)))
            _limits(ax, w.get("ymin"), w.get("ymax"))
            if w.get("unit"):
                ax.set_ylabel(w["unit"], color=c["muted"], fontsize=8)
            if ax2 is not None:
                _limits(ax2, w.get("y2min"), w.get("y2max"))
                if w.get("unit2"):
                    ax2.set_ylabel(w["unit2"], color=c["muted"], fontsize=8)
            handles, labels = ax.get_legend_handles_labels()
            if ax2 is not None:
                h2, l2 = ax2.get_legend_handles_labels()
                handles, labels = handles + h2, labels + l2
            if len(handles) > 1:
                leg = ax.legend(handles, labels, loc="lower left", bbox_to_anchor=(0, 1.0), ncol=min(4, len(handles)),
                                fontsize=10 if theme == "eink" else 8, frameon=False, borderaxespad=0.2)
                for t in leg.get_texts():
                    t.set_color(c["fg"])
        fig.tight_layout(pad=0.6)
        return _to_png(fig, theme)
