"""Read Google Calendar iCal feeds (classes, assignments, exams).

Uses each calendar's private "secret address in iCal format" rather than the
Calendar API: no OAuth, no credentials file, no API enablement, and read-only -
the URL can't modify the calendar or reach anything else in the Google account.

The URLs are secrets (anyone holding one can read that calendar), so they live in
.env, which is gitignored. If one leaks, Google Calendar's "Reset" button on the
same settings page invalidates it immediately.

recurring_ical_events is used deliberately: weekly classes are recurring events,
and a naive parser reports only the original occurrence rather than this week's.
"""
import os
from datetime import datetime, timedelta, date
import requests
from dotenv import load_dotenv
from . import register

load_dotenv()

FEEDS = {
    "classes": "GCAL_CLASSES_ICS",
    "assignments": "GCAL_ASSIGNMENTS_ICS",
    "exams": "GCAL_FINALS_ICS",
}
_cache = {}
CACHE_TTL = timedelta(minutes=15)

def _fetch(env_key):
    url = os.getenv(env_key)
    if not url:
        return None
    hit = _cache.get(env_key)
    if hit and datetime.now() - hit[0] < CACHE_TTL:
        return hit[1]
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    _cache[env_key] = (datetime.now(), r.text)
    return r.text

def _events_between(ics_text, start, end):
    import icalendar
    import recurring_ical_events
    cal = icalendar.Calendar.from_ical(ics_text)
    out = []
    for ev in recurring_ical_events.of(cal).between(start, end):
        dt = ev.get("DTSTART")
        when = dt.dt if dt else None
        out.append({
            "summary": str(ev.get("SUMMARY", "(untitled)")),
            "location": str(ev.get("LOCATION", "") or ""),
            "when": when,
        })
    out.sort(key=lambda e: (
        e["when"] if isinstance(e["when"], datetime)
        else datetime.combine(e["when"], datetime.min.time()) if isinstance(e["when"], date)
        else datetime.max))
    return out

def _fmt(e):
    w = e["when"]
    if isinstance(w, datetime):
        stamp = w.strftime("%a %d %b %I:%M %p").replace(" 0", " ")
    elif isinstance(w, date):
        stamp = w.strftime("%a %d %b") + " (all day)"
    else:
        stamp = "time unknown"
    loc = f" @ {e['location']}" if e["location"] else ""
    return f"{stamp} - {e['summary']}{loc}"

def collect(days_ahead=7):
    """Returns {kind: [lines]} for configured feeds, plus which are missing."""
    now = datetime.now()
    start, end = now - timedelta(hours=12), now + timedelta(days=days_ahead)
    result, missing = {}, []
    for kind, env_key in FEEDS.items():
        try:
            text = _fetch(env_key)
        except Exception as e:
            result[kind] = [f"(couldn't fetch {kind}: {e})"]
            continue
        if text is None:
            missing.append(kind)
            continue
        try:
            result[kind] = [_fmt(e) for e in _events_between(text, start, end)]
        except Exception as e:
            result[kind] = [f"(couldn't parse {kind}: {e})"]
    return result, missing

def briefing_section(days_ahead=7):
    """Compact text block for the daily briefing."""
    data, missing = collect(days_ahead)
    if not data and missing:
        return ("No calendar feeds configured - tell the user you can include his "
                "timetable and deadlines once he adds a calendar feed, and don't guess.")
    parts = []
    for kind, lines in data.items():
        if not lines:
            parts.append(f"{kind.upper()}: nothing in the next {days_ahead} days.")
        else:
            parts.append(f"{kind.upper()}:\n" + "\n".join(f"- {l}" for l in lines[:12]))
    if missing:
        parts.append(f"(not configured: {', '.join(missing)})")
    return "\n\n".join(parts)

@register({"name": "check_calendar",
    "description": "Check the user's university calendar: class timetable, assignment "
                    "deadlines, and exams. Use for questions like 'what classes do I "
                    "have today', 'what's due this week', 'when is my next exam'.",
    "parameters": {"type": "object", "properties": {
        "days_ahead": {"type": "integer",
                        "description": "how many days forward to look, default 7"}},
        "required": []}})
def check_calendar(days_ahead: int = 7):
    days = max(1, min(int(days_ahead or 7), 60))
    return briefing_section(days)
