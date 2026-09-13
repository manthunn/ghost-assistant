"""Fetch a short dossier on a place for the face's city card.

The face runs in pywebview off a file:// URL, so browser-side fetches hit
CORS walls (Google News has none open). Everything network-side therefore
happens here, in Python, exposed to the page through pywebview's js_api as
`pywebview.api.city_brief(name, lat, lon)`.

Three free, keyless sources:
  - Wikipedia REST summary for the one-paragraph description.
  - Open-Meteo for current weather, and its timezone lookup for local time.
  - Google News RSS search for the latest headlines.

Each source fails independently; a dead one leaves its field empty rather
than sinking the card. Results are cached for ten minutes so clicking the
same city twice does not refetch.
"""
import datetime as _dt
import re
import threading
import time
import xml.etree.ElementTree as ET

import requests

UA = {"User-Agent": "Ghost/1.0 (personal desktop assistant; contact via GitHub manthunn)"}
TIMEOUT = 6
CACHE_TTL = 600

_cache = {}
_lock = threading.Lock()

# Open-Meteo WMO weather codes, condensed to what fits on one line.
_WMO = {0: "CLEAR", 1: "MOSTLY CLEAR", 2: "PARTLY CLOUDY", 3: "OVERCAST",
        45: "FOG", 48: "FOG", 51: "DRIZZLE", 53: "DRIZZLE", 55: "DRIZZLE",
        61: "RAIN", 63: "RAIN", 65: "HEAVY RAIN", 71: "SNOW", 73: "SNOW",
        75: "HEAVY SNOW", 80: "SHOWERS", 81: "SHOWERS", 82: "HEAVY SHOWERS",
        95: "THUNDERSTORM", 96: "THUNDERSTORM", 99: "THUNDERSTORM"}


def _wiki(name):
    r = requests.get("https://en.wikipedia.org/api/rest_v1/page/summary/"
                     + requests.utils.quote(name.title()), headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    d = r.json()
    return {"summary": d.get("extract", ""), "title": d.get("title", name),
            "description": d.get("description", "")}


def _weather(lat, lon):
    r = requests.get("https://api.open-meteo.com/v1/forecast",
                     params={"latitude": lat, "longitude": lon,
                             "current": "temperature_2m,weather_code,wind_speed_10m,relative_humidity_2m",
                             "timezone": "auto"}, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    d = r.json()
    cur = d.get("current", {})
    offset = int(d.get("utc_offset_seconds", 0))
    local = _dt.datetime.now(_dt.timezone(_dt.timedelta(seconds=offset)))
    return {"temp_c": cur.get("temperature_2m"),
            "condition": _WMO.get(cur.get("weather_code"), "--"),
            "wind_kmh": cur.get("wind_speed_10m"),
            "humidity": cur.get("relative_humidity_2m"),
            "timezone": d.get("timezone", ""),
            "local_time": local.strftime("%H:%M"),
            "utc_offset": f"UTC{offset / 3600:+.0f}" if offset % 3600 == 0 else f"UTC{offset / 3600:+.1f}"}


def _age(pub):
    try:
        t = _dt.datetime.strptime(pub, "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=_dt.timezone.utc)
    except Exception:
        return ""
    mins = int((_dt.datetime.now(_dt.timezone.utc) - t).total_seconds() // 60)
    if mins < 60:
        return f"{mins}m"
    if mins < 1440:
        return f"{mins // 60}h"
    return f"{mins // 1440}d"


def _news(name, limit=6):
    r = requests.get("https://news.google.com/rss/search",
                     params={"q": name, "hl": "en-AU", "gl": "AU", "ceid": "AU:en"},
                     headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    out = []
    for item in root.iter("item"):
        title = item.findtext("title") or ""
        source = item.findtext("source") or ""
        # Google appends " - Source" to titles; the source tag is cleaner.
        title = re.sub(r"\s+-\s+[^-]+$", "", title) if source else title
        out.append({"title": title.strip(), "source": source.strip(),
                    "age": _age(item.findtext("pubDate") or ""),
                    "link": item.findtext("link") or ""})
        if len(out) >= limit:
            break
    return out


def city_brief(name, lat, lon):
    """Everything the card shows, as one dict. Never raises."""
    key = name.strip().lower()
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < CACHE_TTL:
            return hit[1]
    out = {"name": name, "lat": lat, "lon": lon, "summary": "", "description": "",
           "weather": None, "news": [], "errors": []}
    for field, fn, args in (("wiki", _wiki, (name,)), ("weather", _weather, (lat, lon)),
                            ("news", _news, (name,))):
        try:
            v = fn(*args)
        except Exception as e:
            out["errors"].append(f"{field}: {type(e).__name__}")
            continue
        if field == "wiki":
            out.update(v)
        else:
            out[field] = v
    with _lock:
        _cache[key] = (time.time(), out)
    return out


if __name__ == "__main__":
    import json, sys
    print(json.dumps(city_brief(sys.argv[1] if len(sys.argv) > 1 else "Melbourne",
                                -37.81, 144.96), indent=2)[:3000])
