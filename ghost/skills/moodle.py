"""Read the user's Monash Moodle (learning.monash.edu) dashboard.

Two separate paths, because no single mechanism covers everything asked for:

- Assignments, quizzes and deadlines come from Moodle's own per-user iCal
  calendar export - a secret URL, no login needed once it's pasted in, and
  Moodle-native so it carries assignment/quiz TYPE and quiz open/close times
  distinctly, unlike a generic timetable sync. Get it from:
  learning.monash.edu -> Calendar -> the gear icon -> Export calendar ->
  "This calendar" -> Get calendar URL, and put it in .env as
  MOODLE_ICAL_URL=... . This overlaps with calendar_feed.py's Google-synced
  "Assignments and quizzes" calendar, so it's offered as a Moodle-native
  cross-check rather than the primary deadline source.

- Course announcements are forum posts, not calendar entries, so no iCal
  export carries them, and Ghost has no Moodle login of its own. browser.py's
  read_webpage is deliberately anonymous and already refuses monash.edu for
  this exact reason. The only thing that can see them is the session already
  sitting in the user's Vivaldi window, so this reads that window's text
  instead - only works while a Moodle tab is open and signed in, and only
  sees whatever the page has actually rendered and scrolled into view.
"""
import os
import time
from datetime import datetime, timedelta, date
from dotenv import load_dotenv
from . import register

load_dotenv()

ICAL_URL = (os.getenv("MOODLE_ICAL_URL") or "").strip()
CACHE_TTL = timedelta(minutes=15)
_cal_cache = None  # (fetched_at, [events])


# Monash brands Moodle as "MonashELMS1", so a real tab title reads
# "Assignment 2 [Python] (page 1 of 5) | MonashELMS1 - Vivaldi" - it contains
# neither "moodle" nor "learning.monash.edu". Matching only on those two names
# meant this reported "no Moodle tab found" while one was open on screen.
MOODLE_TITLE_HINTS = ("monashelms", "moodle", "learning.monash.edu")


def _fmt_ical_hint():
    return ("Get it from learning.monash.edu -> Calendar -> the gear/settings "
            "icon -> Export calendar -> choose 'This calendar' (or tick the "
            "event types wanted) -> Export -> then copy the URL Moodle gives "
            "you (starts with .../calendar/export_execute.php). Save it in "
            ".env as MOODLE_ICAL_URL=<that url>.")


def _sort_key(w):
    """Sortable across tz-aware datetimes and all-day dates."""
    if isinstance(w, datetime):
        return w.timestamp() if w.tzinfo else w.astimezone().timestamp()
    if isinstance(w, date):
        return datetime.combine(w, datetime.min.time()).astimezone().timestamp()
    return float("inf")


def _parse(ics_bytes):
    """Raw iCal bytes -> [{"when","summary","course"}], recurring events expanded."""
    import recurring_ical_events
    from icalendar import Calendar
    cal = Calendar.from_ical(ics_bytes)
    now = datetime.now().astimezone()
    raw = recurring_ical_events.of(cal).between(
        now - timedelta(hours=12), now + timedelta(days=60))
    out = []
    for e in raw:
        start = e.get("DTSTART").dt
        summary = str(e.get("SUMMARY", "")).strip()
        cats = e.get("CATEGORIES")
        course = ", ".join(str(c) for c in cats.cats) if cats else ""
        out.append({"when": start, "summary": summary or "(untitled)", "course": course})
    out.sort(key=lambda e: _sort_key(e["when"]))
    return out


def _fetch_events():
    global _cal_cache
    if _cal_cache and datetime.now() - _cal_cache[0] < CACHE_TTL:
        return _cal_cache[1]
    import requests
    r = requests.get(ICAL_URL, timeout=20)
    r.raise_for_status()
    events = _parse(r.content)
    _cal_cache = (datetime.now(), events)
    return events


def _fmt_event(e):
    w = e["when"]
    if isinstance(w, datetime):
        w = w.astimezone()
        stamp = w.strftime("%a %d %b %I:%M %p").lstrip("0").replace(" 0", " ")
    elif isinstance(w, date):
        stamp = w.strftime("%a %d %b") + " (all day)"
    else:
        stamp = "time unknown"
    prefix = f"[{e['course']}] " if e["course"] else ""
    return f"{stamp} - {prefix}{e['summary']}"


@register({"name": "check_moodle_calendar",
    "description": "Check the user's Monash Moodle calendar for upcoming assignment "
                    "and quiz deadlines, read straight from Moodle itself. Use for "
                    "'what's due on Moodle', 'when do my quizzes open or close', or "
                    "as a cross-check against check_calendar's Google-synced dates.",
    "parameters": {"type": "object", "properties": {
        "days_ahead": {"type": "integer",
                        "description": "how many days forward to look, default 14"}},
        "required": []}})
def check_moodle_calendar(days_ahead: int = 14):
    if not ICAL_URL:
        return ("Moodle's calendar isn't connected yet - no MOODLE_ICAL_URL is set "
                "in .env. " + _fmt_ical_hint() + " Tell the user this plainly and "
                "don't guess at any Moodle deadlines.")
    days = max(1, min(int(days_ahead or 14), 60))
    try:
        events = _fetch_events()
    except Exception as e:
        return f"Couldn't read the Moodle calendar feed: {e}"
    cutoff = _sort_key(datetime.now().astimezone() + timedelta(days=days))
    upcoming = [e for e in events if _sort_key(e["when"]) <= cutoff]
    if not upcoming:
        return f"Nothing on the Moodle calendar in the next {days} days."
    lines = [_fmt_event(e) for e in upcoming[:30]]
    return f"Moodle calendar, next {days} days:\n" + "\n".join(lines)


# pywinauto is imported lazily inside the function below, not at module level -
# see the note in ui_automation.py: importing it at import time initialises COM
# on whatever thread loads skills, which can deadlock against pywebview's own
# COM setup on the main thread during startup.

@register({"name": "read_moodle_dashboard",
    "description": "Read whatever is currently visible in the user's Moodle "
                    "(learning.monash.edu) tab in Vivaldi - course announcements, "
                    "the dashboard timeline, or anything else on the page right now. "
                    "This is the only way Ghost can see Moodle announcements, since "
                    "they're forum posts, not calendar events, and Ghost has no "
                    "Moodle login of its own. Requires a Moodle tab to already be "
                    "open and signed in.",
    "parameters": {"type": "object", "properties": {}, "required": []}})
def read_moodle_dashboard():
    from pywinauto import Desktop
    win = None
    try:
        for w in Desktop(backend="uia").windows():
            text = (w.window_text() or "").lower()
            if any(k in text for k in MOODLE_TITLE_HINTS):
                win = w
                break
    except Exception as e:
        return f"Couldn't list open windows: {e}"
    if win is None:
        return ("No open Moodle tab found. Ask the user to open learning.monash.edu "
                "in Vivaldi and sign in if needed - the Dashboard page has "
                "announcements and the timeline, the Calendar page has deadlines - "
                "then try again. Ghost can't fetch Moodle pages on its own; "
                "monash.edu needs the user's own logged-in browser session.")
    try:
        win.set_focus()
        time.sleep(2.5)   # Chromium repopulates its tree lazily after focus
    except Exception:
        pass  # some windows refuse focus; reading may still partly work

    # NOTE: the text below includes Vivaldi's own furniture - tab bar, toolbars
    # and the titles of other open tabs - before the page content proper.
    # Scoping to a Document subtree was tried and does not separate them:
    # Vivaldi's interface is itself a web document and the page is nested inside
    # it. Roughly the first 600 characters are chrome; the Moodle content
    # follows. Left as-is rather than filtered by guesswork, since a fragile
    # filter would silently drop real page text.
    chunks, seen = [], set()
    try:
        for c in win.descendants():
            text = (c.window_text() or "").strip()
            if text and text not in seen:
                seen.add(text)
                chunks.append(text)
    except Exception as e:
        return f"Found the Moodle window ('{win.window_text()}') but couldn't read it: {e}"
    body = " | ".join(chunks)[:4000]
    if not body:
        return (f"Found the Moodle window ('{win.window_text()}') but it has no "
                "readable text yet - it may still be loading.")
    return (f"Text visible right now in the Moodle window ('{win.window_text()}'):\n"
            f"{body}\n\n"
            "This is only what's currently rendered and scrolled into view on that "
            "page, not the whole dashboard. If what the user asked about isn't "
            "here, ask them to navigate to the right page (Dashboard for "
            "announcements/timeline, Calendar for deadlines) or scroll down, then "
            "try again - don't guess at content that isn't in this text.")
