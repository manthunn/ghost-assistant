"""Let Ghost extend itself by driving Claude Code.

The user asks for a capability out loud; Ghost hands the request to Claude Code
running headless (`claude -p`) against this repo, and reports back when it has
written the code. It removes the step where the user has to sit down, open a
terminal and describe the change by hand.

Three constraints shape this, and none are optional:

Ghost is the program being edited. If a broken skill landed in the checkout
Ghost runs from, Ghost would fail to import and the user could no longer ask
Ghost to fix it - a loop it cannot get out of by voice. So every upgrade
happens in a separate git WORKTREE on its own branch. The running assistant is
untouched until a human merges.

Nothing is merged, and Ghost is never restarted automatically. Generated code
is reviewed first. The tools below deliberately cannot merge.

A Claude Code run takes minutes; Ghost closes after five minutes of silence.
The work is therefore a detached process that outlives Ghost, writing its
result to upgrades/<id>.json - the same pattern as the alarm runner.
"""
import json
import os
import pathlib
import re
import subprocess
import sys
import time

from . import register

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
RUNNER = ROOT / "ghost" / "upgrade_runner.py"
JOBS = ROOT / "upgrades"
CLAUDE = pathlib.Path.home() / ".local" / "bin" / "claude.exe"


def _pythonw():
    exe = pathlib.Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    return str(w if w.exists() else exe)


def _jobs():
    if not JOBS.is_dir():
        return []
    out = []
    for p in sorted(JOBS.glob("*.json"), reverse=True):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            d["id"] = p.stem
            out.append(d)
        except Exception:
            continue
    return out


@register({"name": "improve_ghost",
    "description": "Have Ghost write a new capability for itself. Use when the user "
                    "asks Ghost to gain an ability it doesn't have - 'learn to read my "
                    "Moodle dashboard', 'add a tool that does X', 'you should be able "
                    "to Y'. Hands the request to Claude Code, which writes and tests "
                    "the code on a separate branch. Takes several minutes and runs in "
                    "the background, so tell the user it's started and that they'll be "
                    "told when it's done - do NOT claim the feature exists yet. "
                    "Nothing is merged automatically; the user reviews it.",
    "parameters": {"type": "object", "properties": {
        "request": {"type": "string",
                     "description": "the capability to build, in full plain English. "
                                    "Include any detail the user gave about where the "
                                    "data lives or how it should behave."}},
        "required": ["request"]}})
def improve_ghost(request: str):
    request = " ".join((request or "").split())
    if len(request) < 8:
        return "That request is too vague to build from - ask what they want it to do."
    if not CLAUDE.exists():
        return f"Claude Code isn't installed at {CLAUDE}, so Ghost can't upgrade itself."
    if not RUNNER.exists():
        return f"The upgrade runner is missing at {RUNNER}."

    # Refuse to start a second one: two agents editing the same repo from
    # different worktrees would produce branches that conflict on merge.
    running = [j for j in _jobs() if j.get("status") == "running"]
    if running:
        return (f"An upgrade is already running ('{running[0].get('request', '')[:60]}'). "
                f"Wait for that one to finish before starting another.")

    job_id = time.strftime("%Y%m%d-%H%M%S")
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen([_pythonw(), str(RUNNER), job_id, request],
                         cwd=str(ROOT), creationflags=flags, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    except Exception as e:
        return f"Couldn't start the upgrade: {e}"

    return (f"Started building it: \"{request}\". Claude Code is writing the code on "
            f"its own branch now - it usually takes a few minutes and keeps going "
            f"even if this session ends. A box will pop up when it's done, and "
            f"check_upgrades will report it. Tell the user it has STARTED, not that "
            f"the feature works yet.")


@register({"name": "check_upgrades",
    "description": "Report on Ghost's self-upgrade jobs - what's still building, what "
                    "is finished and waiting to be reviewed. Use for 'did you finish "
                    "that', 'what did you build', 'any upgrades ready'.",
    "parameters": {"type": "object", "properties": {}, "required": []}})
def check_upgrades():
    jobs = _jobs()
    if not jobs:
        return "Ghost hasn't been asked to upgrade itself yet."
    lines = []
    for j in jobs[:6]:
        st = j.get("status", "?")
        req = j.get("request", "")[:70]
        if st == "running":
            lines.append(f"- STILL BUILDING: \"{req}\" (started {j.get('started','')})")
        elif st == "ready":
            lines.append(f"- READY TO REVIEW: \"{req}\"\n    branch: {j.get('branch','')}"
                         f"\n    what it did: {j.get('summary','')[:400]}")
        elif st == "no_changes":
            lines.append(f"- NO CHANGES MADE: \"{req}\" - {j.get('summary','')[:200]}")
        else:
            lines.append(f"- FAILED: \"{req}\" - {j.get('error','')[:200]}")
    return ("\n".join(lines) +
            "\n\nNothing is merged. Summarise conversationally; if something is ready, "
            "say it's on a branch waiting for him to look at, and don't claim Ghost "
            "can already do it.")
