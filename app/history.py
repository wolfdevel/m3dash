"""Hilfsfunktionen für Verläufe: FHEM-Texte in Zahlen umsetzen und Messreihen ausdünnen."""
import re


# FHEM speichert VALUE als Text: Zahlen ("21.5", "21.5 °C") und Zustände ("on", "off", "open")
STATE_VALUES = {"on": 1, "off": 0, "true": 1, "false": 0, "open": 1, "closed": 0, "yes": 1, "no": 0,
                "present": 1, "absent": 0, "motion": 1, "nomotion": 0, "an": 1, "aus": 0}
_NUM = re.compile(r"^\s*(-?\d+(?:[.,]\d+)?)")


def to_number(val, value_map=None):
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, bytes):
        val = val.decode("utf-8", "replace")
    if value_map and val in value_map:
        return float(value_map[val])
    m = _NUM.match(val)
    if m:
        return float(m.group(1).replace(",", "."))
    key = val.strip().lower()
    return float(STATE_VALUES[key]) if key in STATE_VALUES else None


def _downsample(points, max_points):
    """Mittelwert-Bucketing, damit Diagramme auch bei vielen Messwerten schnell bleiben."""
    if len(points) <= max_points or max_points < 2:
        return points
    size = len(points) / float(max_points)
    out = []
    for i in range(max_points):
        chunk = points[int(i * size): int((i + 1) * size)]
        if not chunk:
            continue
        mid = chunk[len(chunk) // 2][0]
        out.append((mid, sum(p[1] for p in chunk) / len(chunk)))
    return out
