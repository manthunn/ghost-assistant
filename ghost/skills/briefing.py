"""Daily briefing: one call that gathers everything Ghost should tell Manny on
the first session of the day.

Pulls from the sources Ghost actually has - the clock, live web search for
weather, the Outlook inbox, the local to-do list, and the Google Calendar API.
Any section without a working source says so rather than inventing anything.

Nothing here assumes it is morning. The briefing fires on a gap since the last
one, which can land at any hour, so the time of day is passed through to the
model and the framing shifts to tomorrow once the evening comes.
"""
import json
import pathlib
from datetime import datetime, timedelta
from ..clock import part_of_day, stamp, is_winding_down, is_small_hours
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

def should_brief(now=None):
    """True if Ghost should open with a briefing unprompted.

    Suppressed in the small hours. The gap rule alone fires on time-since-last,
    not on a schedule, so a 3am session would get the full rundown - weather,
    inbox, YouTube uploads - which is not what anyone wants at 3am.

    Two deliberate properties:
    - Only gates the *unprompted* briefing. daily_briefing stays registered, so
      asking for it at 3am still works.
    - Does not mark_briefed() when it declines, so the briefing is still owed
      later that morning rather than being silently swallowed by a 3am session.
    """
    now = now or datetime.now()
    if is_small_hours(now):
        return False
    last = _load_state().get("last_briefed")
    if not last:
        return True
    try:
        return now - datetime.fromisoformat(last) >= REBRIEF_AFTER
    except ValueError:
        return True

def briefing_prompt():
    """The opening request Ghost sends itself for an unprompted briefing.

    Time-aware because this fires on a gap, not on a schedule - it lands in the
    evening or the small hours as often as at breakfast. The old text was
    literally "Give me my daily briefing for today", which at 11pm asks for a
    rundown of a day that has almost finished.
    """
    if is_winding_down():
        return ("Give me my briefing. It is not morning - greet me accordingly, "
                "and focus on what is coming up next rather than on a day that "
                "is nearly over.")
    return "Give me my briefing for today."


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
    """Unread counts for every account, plus headlines from the open one.

    check_mail reads the folder tree, so it covers both the personal and Monash
    mailboxes without clicking between them; read_inbox then gives actual subject
    lines for whichever is currently shown.
    """
    parts = []
    try:
        from .outlook import check_mail
        parts.append(check_mail())
    except Exception as e:
        parts.append(f"(couldn't check unread counts: {e})")
    try:
        from .outlook import read_inbox
        parts.append("Most recent:\n" + read_inbox(5))
    except Exception as e:
        parts.append(f"(couldn't read recent mail: {e})")
    return "\n\n".join(parts)

def _calendar():
    try:
        from .calendar_feed import briefing_section
        return briefing_section(7)
    except Exception as e:
        return f"(couldn't read calendar: {e})"

def _youtube():
    try:
        from .youtube import briefing_section
        return briefing_section(24)
    except Exception as e:
        return f"(couldn't check YouTube: {e})"

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
    "description": "Gather the user's briefing: current date and time, local weather, "
                    "classes and assignment deadlines, recent inbox messages, "
                    "outstanding to-do items, and new videos from watched YouTube "
                    "channels. Use this when the user asks for their briefing/rundown, "
                    "or at the start of a session after a long gap. Summarise it "
                    "conversationally out loud - greet him by name, then lead with "
                    "what's most useful now, then important email and tasks. "
                    "IMPORTANT: match the greeting to the DATE/TIME line in the "
                    "result - it says which part of the day it actually is. Never say "
                    "'good morning' unless it is genuinely morning, and in the middle "
                    "of the night skip the greeting and just be brief. In the evening "
                    "or at night, lead with tomorrow's classes and what's due next "
                    "rather than a day that is nearly over. Keep it under about 8 "
                    "sentences and skip anything that's empty or unavailable.",
    "parameters": {"type": "object", "properties": {}, "required": []}})
def daily_briefing():
    now = datetime.now()
    mark_briefed()
    sections = [
        f"DATE/TIME: {stamp(now)} (Clayton, Melbourne) - it is currently "
        f"{part_of_day(now)}"
        + (". Today is nearly over, so lead with tomorrow and what's due next."
           if is_winding_down(now) else "."),
        f"WEATHER:\n{_weather()}",
        f"UNIVERSITY CALENDAR (next 7 days):\n{_calendar()}",
        f"INBOX:\n{_inbox()}",
        f"TO-DO LIST:\n{_todos()}",
    ]
    yt = _youtube()
    if yt.strip():
        sections.append(f"NEW YOUTUBE UPLOADS (last 24h):\n{yt}")
    return "\n\n".join(sections)
