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



def _branch_exists(branch):
    r = subprocess.run(["git", "rev-parse", "--verify", "--quiet", branch],
                       cwd=str(ROOT), capture_output=True, text=True)
    return r.returncode == 0


def _age_days(job):
    stamp = job.get("finished") or job.get("started")
    if not stamp:
        return 999.0
    try:
        from datetime import datetime
        return (datetime.now() - datetime.fromisoformat(stamp)).total_seconds() / 86400
    except Exception:
        return 999.0



def _inside_upgrades(path):
    """True only for paths genuinely under ../ghost-upgrades/.

    The worktree path is read from a job file, so it is untrusted input to a
    recursive delete. Resolve both sides and confirm containment rather than
    string-matching a prefix.
    """
    try:
        base = (ROOT.parent / "ghost-upgrades").resolve()
        target = pathlib.Path(path).resolve()
        return target != base and base in target.parents
    except Exception:
        return False


def _prune_worktrees(max_age_days=14):
    """Remove worktrees that no longer have anything to review.

    Each upgrade leaves a full checkout behind, so without this they pile up in
    ../ghost-upgrades/ forever - one per request, each a copy of the repo.

    A worktree is kept only while it still holds something a human might want:
    a job that is running, or one that produced a branch that still exists. Once
    the branch has been merged or deleted, the worktree is just a stale copy of
    code that is now either in main or deliberately discarded.
    """
    removed = []
    for job in _jobs():
        wt, branch, status = job.get("worktree"), job.get("branch"), job.get("status")
        if status == "running" or not wt:
            continue
        if status == "ready" and branch and _branch_exists(branch):
            continue          # still awaiting review - leave it alone
        if status == "ready" and _age_days(job) < max_age_days and branch and                 _branch_exists(branch):
            continue
        if not pathlib.Path(wt).exists():
            continue
        r = subprocess.run(["git", "worktree", "remove", wt, "--force"],
                           cwd=str(ROOT), capture_output=True, text=True)
        if r.returncode == 0:
            removed.append(pathlib.Path(wt).name)
        elif _inside_upgrades(wt):
            # git refuses when it no longer tracks the path - which is the normal
            # state once the branch has been deleted, or after someone removed
            # the worktree by hand. The directory is still on disk either way.
            # Guarded by _inside_upgrades because this path comes out of a JSON
            # file: a recursive delete must never be able to point elsewhere.
            import shutil
            shutil.rmtree(wt, ignore_errors=True)
            if not pathlib.Path(wt).exists():
                removed.append(pathlib.Path(wt).name)
    # Clears git's records for worktrees whose directory was deleted by hand.
    subprocess.run(["git", "worktree", "prune"], cwd=str(ROOT), capture_output=True)
    try:
        parent = ROOT.parent / "ghost-upgrades"
        if parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
    except Exception:
        pass
    return removed


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

    # Tidy up before adding another checkout to the pile.
    _prune_worktrees()

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


@register({"name": "cleanup_upgrades",
    "description": "Tidy up leftover Ghost self-upgrade working copies. Each upgrade "
                    "leaves a full checkout of the repo behind; this removes the ones "
                    "whose branch has already been merged or deleted. Use for 'clean "
                    "up the upgrade folders' or if disk space is a concern. Never "
                    "removes an upgrade that is still building or still waiting to be "
                    "reviewed.",
    "parameters": {"type": "object", "properties": {}, "required": []}})
def cleanup_upgrades():
    removed = _prune_worktrees()
    if not removed:
        return ("Nothing to clean up - no leftover upgrade working copies. Anything "
                "still building or awaiting review was left alone.")
    return (f"Removed {len(removed)} leftover upgrade working copies: "
            f"{', '.join(removed)}. Branches still awaiting review were left alone.")
