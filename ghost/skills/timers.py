"""Countdown timers, alarms and a stopwatch.

Ghost couldn't start a timer before because nothing here could. The Windows
Clock app was the obvious route and was rejected: its URI scheme opens the app
but won't create a timer with a duration, so it would mean driving its UI, and
"set a timer" would depend on the Clock app's layout not changing.

Instead each timer is a detached alarm_runner.py process. That matters because
Ghost exits after five minutes of silence - a timer living inside Ghost would
die with it, which is precisely the case a timer is for. A separate process
survives Ghost closing, a Ghost crash, and Ghost being restarted.

State lives in timers.json so Ghost can still list and cancel them across
restarts. The process is the source of truth; the file is a record, and entries
whose process has gone are pruned on read.
"""
import json
import os
import pathlib
import subprocess
import sys
import time
from datetime import datetime, timedelta

from . import register

STATE = pathlib.Path(__file__).resolve().parent.parent / "timers.json"
RUNNER = pathlib.Path(__file__).resolve().parent.parent / "alarm_runner.py"


def _pythonw():
    """pythonw so the helper doesn't flash a console window."""
    exe = pathlib.Path(sys.executable)
    cand = exe.with_name("pythonw.exe")
    return str(cand) if cand.exists() else str(exe)


def _alive(pid):
    if not pid:
        return False
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {int(pid)}", "/NH"],
                             capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return True   # can't tell; assume it's there rather than lose the entry
    return str(pid) in out


def _load():
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    data.setdefault("pending", [])
    data.setdefault("stopwatch", None)
    # Drop anything whose process has exited - fired, cancelled or killed.
    data["pending"] = [t for t in data["pending"] if _alive(t.get("pid"))]
    return data


def _save(data):
    STATE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _spawn(target_epoch, label):
    """Detached so it outlives Ghost. Returns the pid."""
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    p = subprocess.Popen(
        [_pythonw(), str(RUNNER), str(target_epoch), label],
        creationflags=flags, close_fds=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL)
    return p.pid


def _speak_delta(seconds):
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds} second{'s' if seconds != 1 else ''}"
    mins, secs = divmod(seconds, 60)
    hrs, mins = divmod(mins, 60)
    bits = []
    if hrs:
        bits.append(f"{hrs} hour{'s' if hrs != 1 else ''}")
    if mins:
        bits.append(f"{mins} minute{'s' if mins != 1 else ''}")
    if secs and not hrs:
        bits.append(f"{secs} second{'s' if secs != 1 else ''}")
    return " ".join(bits) or "0 seconds"


def _clock(dt):
    return dt.strftime("%I:%M %p").lstrip("0")


@register({"name": "set_timer",
    "description": "Start a countdown timer. Use for 'set a timer for 10 minutes', "
                    "'remind me in half an hour', 'timer for 90 seconds'. An alert "
                    "box and a chime fire when it finishes, and it keeps running even "
                    "if Ghost closes.",
    "parameters": {"type": "object", "properties": {
        "hours": {"type": "integer", "description": "hours, default 0"},
        "minutes": {"type": "integer", "description": "minutes, default 0"},
        "seconds": {"type": "integer", "description": "seconds, default 0"},
        "label": {"type": "string",
                   "description": "what the timer is for, e.g. 'pasta'. Optional."}},
        "required": []}})
def set_timer(hours: int = 0, minutes: int = 0, seconds: int = 0, label: str = ""):
    total = int(hours or 0) * 3600 + int(minutes or 0) * 60 + int(seconds or 0)
    if total <= 0:
        return "No duration given - ask the user how long the timer should be."
    if total > 24 * 3600:
        return "That's over 24 hours; use an alarm for something that far out."

    label = " ".join((label or "").split())
    target = time.time() + total
    text = f"Timer finished: {label}" if label else "Timer finished."
    try:
        pid = _spawn(target, text)
    except Exception as e:
        return f"Couldn't start the timer: {e}"

    data = _load()
    data["pending"].append({
        "kind": "timer", "label": label, "target": target, "pid": pid,
        "set_at": time.time(),
    })
    _save(data)
    when = datetime.fromtimestamp(target)
    return (f"Timer set for {_speak_delta(total)}"
            + (f" ({label})" if label else "")
            + f", finishing at {_clock(when)}. It will keep running even if Ghost "
              f"closes.")


@register({"name": "set_alarm",
    "description": "Set an alarm for a specific clock time. Use for 'wake me at 7am', "
                    "'alarm for 6:30 tomorrow'. If the time has already passed today "
                    "it is set for tomorrow.",
    "parameters": {"type": "object", "properties": {
        "hour": {"type": "integer", "description": "hour in 24-hour form, 0-23"},
        "minute": {"type": "integer", "description": "minute, 0-59, default 0"},
        "label": {"type": "string", "description": "what the alarm is for. Optional."}},
        "required": ["hour"]}})
def set_alarm(hour: int, minute: int = 0, label: str = ""):
    try:
        hour, minute = int(hour), int(minute or 0)
    except (TypeError, ValueError):
        return "That time didn't parse - ask the user to repeat it."
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return f"{hour}:{minute:02d} isn't a valid time."

    label = " ".join((label or "").split())
    now = datetime.now()
    when = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if when <= now:
        when += timedelta(days=1)      # already gone today, so tomorrow

    text = f"Alarm: {label}" if label else "Alarm."
    try:
        pid = _spawn(when.timestamp(), text)
    except Exception as e:
        return f"Couldn't set the alarm: {e}"

    data = _load()
    data["pending"].append({
        "kind": "alarm", "label": label, "target": when.timestamp(), "pid": pid,
        "set_at": time.time(),
    })
    _save(data)
    day = "tomorrow" if when.date() != now.date() else "today"
    return (f"Alarm set for {_clock(when)} {day}"
            + (f" ({label})" if label else "")
            + f", {_speak_delta((when - now).total_seconds())} from now.")


@register({"name": "list_timers",
    "description": "List the user's running timers and alarms with how long is left. "
                    "Use for 'how long on my timer', 'what alarms do I have'.",
    "parameters": {"type": "object", "properties": {}, "required": []}})
def list_timers():
    data = _load()
    _save(data)   # persist the pruning of anything that already fired
    items = sorted(data["pending"], key=lambda t: t["target"])
    lines = []
    for t in items:
        left = t["target"] - time.time()
        name = t["label"] or t["kind"]
        if t["kind"] == "alarm":
            lines.append(f"- alarm '{name}' at {_clock(datetime.fromtimestamp(t['target']))}"
                         f" ({_speak_delta(left)} away)")
        else:
            lines.append(f"- timer '{name}': {_speak_delta(left)} left")

    sw = data.get("stopwatch")
    if sw:
        lines.append(f"- stopwatch running: {_speak_delta(time.time() - sw)} elapsed")

    if not lines:
        return "No timers or alarms are set, and the stopwatch isn't running."
    return f"{len(lines)} running:\n" + "\n".join(lines)


@register({"name": "cancel_timer",
    "description": "Cancel a running timer or alarm. Match it by its label, or pass "
                    "'all' to cancel everything.",
    "parameters": {"type": "object", "properties": {
        "match": {"type": "string",
                   "description": "text from the timer's label, or 'all'"}},
        "required": ["match"]}})
def cancel_timer(match: str):
    data = _load()
    q = " ".join((match or "").split()).lower()
    if not q:
        return "Which timer? Ask the user which one to cancel."

    if q == "all":
        doomed = list(data["pending"])
    else:
        doomed = [t for t in data["pending"]
                  if q in (t["label"] or "").lower() or q in t["kind"]]
    if not doomed:
        return f"No timer or alarm matching '{match}'. Use list_timers to see them."

    killed = []
    for t in doomed:
        try:
            subprocess.run(["taskkill", "/PID", str(t["pid"]), "/F"],
                           capture_output=True, timeout=10)
            killed.append(t["label"] or t["kind"])
        except Exception:
            pass
    data["pending"] = [t for t in data["pending"] if t not in doomed]
    _save(data)
    return f"Cancelled {len(killed)}: {', '.join(killed) or 'none'}."


@register({"name": "stopwatch",
    "description": "Start, check or stop a stopwatch. Use for 'start a stopwatch', "
                    "'how long has it been', 'stop the stopwatch'.",
    "parameters": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["start", "check", "stop"],
                    "description": "'start', 'check' or 'stop'"}},
        "required": ["action"]}})
def stopwatch(action: str):
    action = (action or "").strip().lower()
    data = _load()
    running = data.get("stopwatch")

    if action == "start":
        if running:
            return (f"A stopwatch is already running - "
                    f"{_speak_delta(time.time() - running)} so far. "
                    f"Ask whether to restart it.")
        data["stopwatch"] = time.time()
        _save(data)
        return "Stopwatch started."

    if action == "check":
        if not running:
            return "The stopwatch isn't running."
        return f"{_speak_delta(time.time() - running)} elapsed."

    if action == "stop":
        if not running:
            return "The stopwatch isn't running."
        elapsed = time.time() - running
        data["stopwatch"] = None
        _save(data)
        return f"Stopwatch stopped at {_speak_delta(elapsed)}."

    return "Action must be 'start', 'check' or 'stop'."
