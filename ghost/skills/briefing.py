"""Daily briefing: one call that gathers everything Ghost should tell Manny on
the first session of the day.

Pulls from the sources Ghost actually has - the clock, live web search for
weather, the Outlook inbox, and the local to-do list. Class timetable and
assignment deadlines need a calendar feed (Monash Moodle iCal export or Google
Calendar); until one is configured those sections say so rather than inventing
anything.
"""
import json
import pathlib
from datetime import datetime, timedelta
from . import register

STATE_FILE = pathlib.Path(__file__).resolve().parent.parent / "briefing_state.json"
REBRIEF_AFTER = timedelta(hours=6)

def _load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}

def should_brief():
    """True if Ghost hasn't briefed in REBRIEF_AFTER (or ever)."""
    last = _load_state().get("last_briefed")
    if not last:
        return True
    try:
        return datetime.now() - datetime.fromisoformat(last) >= REBRIEF_AFTER
    except ValueError:
        return True

def mark_briefed():
    STATE_FILE.write_text(
        json.dumps({"last_briefed": datetime.now().isoformat(timespec="seconds")}, indent=2),
        encoding="utf-8")

def _weather():
    """wttr.in gives clean structured weather with no API key; web search is the
    fallback since it returns noisy snippets rather than an actual forecast."""
    try:
        import requests
        r = requests.get("https://wttr.in/Clayton,Melbourne?format=j1", timeout=10)
        d = r.json()
        cur = d["current_condition"][0]
        today = d["weather"][0]
        desc = cur["weatherDesc"][0]["value"]
        parts = [
            f"Now: {desc}, {cur['temp_C']}C (feels {cur['FeelsLikeC']}C), "
            f"humidity {cur['humidity']}%, wind {cur['windspeedKmph']} km/h",
            f"Today: low {today['mintempC']}C, high {today['maxtempC']}C",
        ]
        rain = [h for h in today.get("hourly", [])
                if int(h.get("chanceofrain", 0)) >= 40]
        if rain:
            parts.append(f"Rain likely around {rain[0]['time'][:-2] or '0'}:00 "
                          f"({rain[0]['chanceofrain']}% chance)")
        return " | ".join(parts)
    except Exception:
        try:
            from .browser import web_search
            return web_search("Clayton Melbourne VIC weather forecast today")[:600]
        except Exception as e:
            return f"(couldn't fetch weather: {e})"

def _inbox():
    try:
        from .outlook import read_inbox
        return read_inbox(5)
    except Exception as e:
        return f"(couldn't read inbox: {e})"

def _calendar():
    try:
        from .calendar_feed import briefing_section
        return briefing_section(7)
    except Exception as e:
        return f"(couldn't read calendar: {e})"

def _todos():
    try:
        from .todo import pending
        items = pending()
        if not items:
            return "No outstanding to-do items."
        return "\n".join(
            f"- {t['task']}" + (f" (due {t['due']})" if t.get("due") else "")
            for t in items)
    except Exception as e:
        return f"(couldn't read to-do list: {e})"

@register({"name": "daily_briefing",
    "description": "Gather the user's morning briefing: today's date, the local "
                    "weather, recent inbox messages, and outstanding to-do items. "
                    "Use this when the user asks for their briefing/rundown, or at "
                    "the start of a new day. Summarise it conversationally out loud - "
                    "greet him by name, lead with the date and weather, then the "
                    "important email and tasks. Keep it under about 8 sentences and "
                    "skip anything that's empty or unavailable.",
    "parameters": {"type": "object", "properties": {}, "required": []}})
def daily_briefing():
    now = datetime.now()
    mark_briefed()
    return "\n\n".join([
        f"DATE/TIME: {now.strftime('%A, %B %d, %Y, %I:%M %p')} (Clayton, Melbourne)",
        f"WEATHER SEARCH RESULTS:\n{_weather()}",
        f"INBOX:\n{_inbox()}",
        f"TO-DO LIST:\n{_todos()}",
        f"UNIVERSITY CALENDAR (next 7 days):\n{_calendar()}",
    ])
