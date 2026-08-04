"""Read the user's university calendars through the Google Calendar API.

This replaces the old "secret address in iCal format" feeds, which 404'd within
about a day, every time, no matter how often the links were re-pasted.

The cause was NOT token resets. The calendar *IDs* themselves kept changing
(`c_efdb0f46...` -> `c_16b7ec89...` -> dead), because Monash's timetable sync
deletes and recreates the calendars rather than updating them in place. A secret
iCal link is bound to one calendar ID forever, so a fresh link buys about a day
and then dies with the calendar it pointed at.

The API fixes this properly because it resolves calendars by NAME at request
time ("Classes", "Assignments and quizzes", "Final assessments"). When Monash
recreates a calendar under a new ID, the very next call finds it again with no
reconfiguration at all.

Auth: a one-time OAuth desktop flow, read-only scope. Run `python setup_gcal.py`
once; it writes `gcal_token.json`. This module deliberately never launches the
consent flow itself - a browser popup in the middle of a voice session would
just hang with nobody watching.
"""
import os
from datetime import datetime, timedelta, date
from pathlib import Path
from dotenv import load_dotenv
from . import register

load_dotenv()

# Read-only: this token cannot create, edit or delete anything in the account.
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

_REPO_ROOT = Path(__file__).resolve().parents[2]
CREDENTIALS_PATH = Path(os.getenv("GCAL_CREDENTIALS") or _REPO_ROOT / "credentials.json")
TOKEN_PATH = Path(os.getenv("GCAL_TOKEN") or _REPO_ROOT / "gcal_token.json")

# kind -> calendar name as it appears in Google Calendar. Overridable from .env
# in case Monash ever renames one (a rename is the *only* thing that breaks the
# name lookup; new IDs are handled for free).
CALENDARS = {
    "classes": os.getenv("GCAL_CLASSES_NAME") or "Classes",
    "assignments": os.getenv("GCAL_ASSIGNMENTS_NAME") or "Assignments and quizzes",
    "exams": os.getenv("GCAL_FINALS_NAME") or "Final assessments",
}

CACHE_TTL = timedelta(minutes=15)
_service = None
_cal_cache = None   # (fetched_at, [{"id","name"}, ...])
_event_cache = {}   # cache_key -> (fetched_at, [events])


class CalendarAuthError(Exception):
    """Not authorised yet, or the saved authorisation stopped working.

    Always carries a plain-English next step - "invalid_grant" read out loud in
    a voice session is useless.
    """


def _fmt_setup_hint():
    return ("Run 'python setup_gcal.py' in the ghost-assistant folder to authorise "
            "Google Calendar. It only needs doing once.")


def _get_service():
    """Build (and cache) an authorised Calendar API client."""
    global _service
    if _service is not None:
        return _service

    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError as e:
        raise CalendarAuthError(
            "the Google Calendar libraries aren't installed. Run "
            "'pip install google-api-python-client google-auth-oauthlib'.") from e

    if not CREDENTIALS_PATH.exists():
        raise CalendarAuthError(
            f"there's no OAuth client file at {CREDENTIALS_PATH.name}. Create a "
            "Desktop app OAuth client in Google Cloud Console, download it, and "
            f"save it as {CREDENTIALS_PATH}.")
    if not TOKEN_PATH.exists():
        raise CalendarAuthError(
            "Google Calendar hasn't been authorised on this machine yet. "
            + _fmt_setup_hint())

    try:
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    except Exception as e:
        raise CalendarAuthError(
            f"the saved Google Calendar token is unreadable ({e}). "
            + _fmt_setup_hint()) from e

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                # invalid_grant here almost always means the OAuth consent screen
                # is still in "Testing", which expires refresh tokens after 7 days.
                raise CalendarAuthError(
                    "the saved Google Calendar authorisation was rejected when "
                    f"refreshing it ({e}). If this keeps happening every week, "
                    "check that the OAuth consent screen is set to 'In production' "
                    "and not 'Testing' - Testing expires refresh tokens after 7 "
                    "days. " + _fmt_setup_hint()) from e
            _save_token(creds)
        else:
            raise CalendarAuthError(
                "the saved Google Calendar authorisation is no longer valid. "
                + _fmt_setup_hint())

    _service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    return _service


def _save_token(creds):
    TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    try:
        os.chmod(TOKEN_PATH, 0o600)
    except OSError:
        pass  # best effort; Windows ACLs don't map cleanly onto POSIX modes


def _tokens(name):
    """Normalise a calendar name to a set of comparable words."""
    return {w for w in "".join(c.lower() if c.isalnum() else " " for c in name).split()}


def _matches(wanted, actual):
    """True when one name's words are a subset of the other's.

    Word-set containment rather than substring matching: it still catches
    "Classes" vs "Monash Classes" and "Assignments and quizzes" vs
    "Assignments", without a stray substring hit pairing unrelated calendars.
    """
    a, b = _tokens(wanted), _tokens(actual)
    return bool(a) and bool(b) and (a <= b or b <= a)


def _calendar_list(force=False):
    """All calendars visible to the account, as [{"id", "name"}]."""
    global _cal_cache
    if not force and _cal_cache and datetime.now() - _cal_cache[0] < CACHE_TTL:
        return _cal_cache[1]
    service = _get_service()
    cals, page = [], None
    while True:
        resp = service.calendarList().list(pageToken=page, maxResults=250).execute()
        for item in resp.get("items", []):
            cals.append({"id": item["id"],
                         "name": item.get("summaryOverride") or item.get("summary", "")})
        page = resp.get("nextPageToken")
        if not page:
            break
    _cal_cache = (datetime.now(), cals)
    return cals


def resolve(kind, force=False):
    """Calendar IDs currently matching `kind`'s configured name.

    A list, not a single ID: when Monash recreates a calendar the old one can
    linger alongside the new one for a while, and reading both then de-duping
    beats guessing which is live.
    """
    wanted = CALENDARS[kind]
    return [c["id"] for c in _calendar_list(force) if _matches(wanted, c["name"])]


def _to_local(value):
    """Google returns RFC3339 with an offset for timed events and a bare date
    for all-day ones. Render timed events in local time or a 10am class reads
    as midnight (the old feed bug); leave all-day dates alone."""
    if value is None:
        return None
    if "T" in value:
        return datetime.fromisoformat(value).astimezone()
    return date.fromisoformat(value)


def _sort_key(w):
    """Sortable across tz-aware datetimes and all-day dates (comparing aware and
    naive datetimes directly raises TypeError)."""
    if isinstance(w, datetime):
        return w.timestamp() if w.tzinfo else w.astimezone().timestamp()
    if isinstance(w, date):
        return datetime.combine(w, datetime.min.time()).astimezone().timestamp()
    return float("inf")


def _events_between(cal_ids, start, end):
    """Events from every given calendar, expanded, merged and de-duped.

    Returns (events, gone_ids) - a calendar deleted between the name lookup and
    the read is reported rather than silently contributing nothing, because
    "no events" and "the calendar moved again" must not look the same to the
    caller. Guessing wrong there means telling the user they have no classes.

    singleEvents=True makes the API expand recurring events server-side, so a
    weekly class reports *this* week's occurrence - which is exactly what the
    old code needed recurring_ical_events for.
    """
    from googleapiclient.errors import HttpError
    service = _get_service()
    out, gone = [], []
    for cid in list(cal_ids):
        page = None
        while True:
            try:
                resp = service.events().list(
                    calendarId=cid, timeMin=start.isoformat(), timeMax=end.isoformat(),
                    singleEvents=True, orderBy="startTime", maxResults=250,
                    pageToken=page).execute()
            except HttpError as e:
                if e.resp.status in (404, 410):
                    gone.append(cid)
                    break
                raise
            for ev in resp.get("items", []):
                s = ev.get("start", {})
                out.append({
                    "summary": (ev.get("summary") or "(untitled)").strip(),
                    "location": (ev.get("location") or "").strip(),
                    "when": _to_local(s.get("dateTime") or s.get("date")),
                })
            page = resp.get("nextPageToken")
            if not page:
                break

    seen, deduped = set(), []
    for e in out:
        key = (e["summary"].lower(), _sort_key(e["when"]), e["location"].lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(e)
    deduped.sort(key=lambda e: _sort_key(e["when"]))
    return deduped, gone


def _fmt(e):
    w = e["when"]
    if isinstance(w, datetime):
        stamp = w.strftime("%a %d %b %I:%M %p").lstrip("0").replace(" 0", " ")
    elif isinstance(w, date):
        stamp = w.strftime("%a %d %b") + " (all day)"
    else:
        stamp = "time unknown"
    loc = f" @ {e['location']}" if e["location"] else ""
    return f"{stamp} - {e['summary']}{loc}"


def collect(days_ahead=7):
    """Returns ({kind: [lines]}, missing_kinds, auth_problem_or_None)."""
    now = datetime.now().astimezone()
    start, end = now - timedelta(hours=12), now + timedelta(days=days_ahead)
    result, missing = {}, []

    try:
        _get_service()
        _calendar_list()
    except CalendarAuthError as e:
        # One shared failure for every calendar - no point repeating it three times.
        return {}, list(CALENDARS), str(e)
    except Exception as e:
        return {}, list(CALENDARS), f"couldn't reach Google Calendar: {e}"

    for kind in CALENDARS:
        cache_key = (kind, days_ahead)
        hit = _event_cache.get(cache_key)
        if hit and datetime.now() - hit[0] < CACHE_TTL:
            result[kind] = hit[1]
            continue
        try:
            # Two attempts: the second forces a fresh calendar lookup by name.
            # That is the recovery path for exactly the failure that killed the
            # iCal links - the calendar being deleted and recreated under a new
            # ID while a stale ID is still cached here.
            for attempt in (0, 1):
                cal_ids = resolve(kind, force=(attempt == 1))
                if not cal_ids:
                    continue
                events, gone = _events_between(cal_ids, start, end)
                if len(gone) == len(cal_ids):
                    continue  # every calendar under this name vanished mid-read
                lines = [_fmt(e) for e in events]
                _event_cache[cache_key] = (datetime.now(), lines)
                result[kind] = lines
                break
            else:
                missing.append(kind)
        except CalendarAuthError as e:
            return {}, list(CALENDARS), str(e)
        except Exception as e:
            result[kind] = [f"(couldn't read {kind}: {e})"]
    return result, missing, None


def briefing_section(days_ahead=7):
    """Compact text block for the daily briefing."""
    data, missing, problem = collect(days_ahead)
    if problem:
        return ("The Google Calendar connection isn't working: " + problem +
                " Tell the user plainly, and don't guess at any classes or deadlines.")
    if not data and missing:
        try:
            names = ", ".join(sorted(c["name"] for c in _calendar_list() if c["name"]))
        except Exception:
            names = ""
        return ("None of the expected calendars were found in the user's Google "
                f"account (looking for: {', '.join(CALENDARS.values())})."
                + (f" Calendars that are there: {names}." if names else "")
                + " Tell the user, and don't guess at any classes or deadlines.")
    parts = []
    for kind, lines in data.items():
        if not lines:
            parts.append(f"{kind.upper()}: nothing in the next {days_ahead} days.")
        else:
            parts.append(f"{kind.upper()}:\n" + "\n".join(f"- {l}" for l in lines[:12]))
    if missing:
        parts.append(f"(calendar not found in the account: {', '.join(missing)})")
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
