"""Run one Ghost self-upgrade in the background. Not imported by Ghost.

    pythonw upgrade_runner.py <job_id> <request text...>

Detached on purpose, for the same reason the alarm runner is: Ghost closes
itself after five minutes of silence, and a Claude Code run takes minutes. An
upgrade living inside Ghost's process would be killed halfway through editing
its own source.

Works in a separate git WORKTREE, never in the checkout Ghost runs from. That
is the load-bearing decision here. Ghost is a running program editing its own
code - if a broken skill landed in the live tree, Ghost would fail to import
and the user would no longer be able to ask Ghost to fix it. A worktree means
the running assistant is untouched until a human merges.
"""
import ctypes
import json
import pathlib
import re
import subprocess
import sys
import time
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parent.parent
JOBS = ROOT / "upgrades"
WORKTREES = ROOT.parent / "ghost-upgrades"
CLAUDE = pathlib.Path.home() / ".local" / "bin" / "claude.exe"
TIMEOUT = 30 * 60

# File edits and the commands needed to test a change. Deliberately NOT
# bypassPermissions: this is triggered by voice with nobody watching, so the
# blast radius should be the smallest thing that can still do the job.
ALLOWED = ("Read,Write,Edit,Glob,Grep,"
           "Bash(git *),Bash(python *),Bash(py *),Bash(pip *)")

BRIEF = """You are upgrading Ghost, a Python voice assistant, in this worktree.

REQUEST FROM THE USER:
{request}

How this codebase works:
- Skills live in ghost/skills/*.py and are auto-discovered at boot.
- A skill exposes tools with the @register({{...}}) decorator - copy the shape
  from an existing skill such as ghost/skills/ft_news.py or todo.py.
- Read CLAUDE.md and README.md before writing anything.
- Match the existing style. Comments explain WHY, not what.

Rules:
- Make the smallest change that satisfies the request.
- If the request needs a credential, API key or a login you do not have, do NOT
  invent one or fake the data. Implement what you can and say clearly in your
  summary what the user must supply.
- Verify your work: import the module and exercise the new tool. A skill that
  has never been run is not done.
- Do not modify main.py, live_voice.py or brain.py unless the request cannot be
  satisfied any other way.
- Do not commit. The wrapper handles git.

Finish with a short plain-English summary, under 80 words, of what you changed
and anything the user still needs to do. It will be read aloud."""


def notify(text):
    MB = 0x40 | 0x40000 | 0x10000
    try:
        ctypes.windll.user32.MessageBoxW(None, text[:900], "Ghost - upgrade ready", MB)
    except Exception:
        pass


def write(job_id, **fields):
    JOBS.mkdir(parents=True, exist_ok=True)
    path = JOBS / f"{job_id}.json"
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data.update(fields)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def git(*args, cwd=ROOT):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, timeout=120)


def main():
    if len(sys.argv) < 3:
        return 1
    job_id = sys.argv[1]
    request = " ".join(sys.argv[2:]).strip()
    slug = re.sub(r"[^a-z0-9]+", "-", request.lower())[:40].strip("-") or "change"
    branch = f"ghost-upgrade/{job_id}-{slug}"
    wt = WORKTREES / job_id

    write(job_id, request=request, branch=branch, status="running",
          started=datetime.now().isoformat(timespec="seconds"))

    r = git("worktree", "add", "-b", branch, str(wt), "HEAD")
    if r.returncode != 0:
        write(job_id, status="failed", error=f"couldn't create worktree: {r.stderr[:400]}")
        return 1

    try:
        proc = subprocess.run(
            [str(CLAUDE), "-p", BRIEF.format(request=request),
             "--permission-mode", "acceptEdits",
             "--allowedTools", ALLOWED],
            cwd=str(wt), capture_output=True, text=True, timeout=TIMEOUT)
        summary = (proc.stdout or proc.stderr or "").strip()
    except subprocess.TimeoutExpired:
        write(job_id, status="failed", error=f"timed out after {TIMEOUT // 60} minutes")
        notify(f"Ghost upgrade timed out:\n{request}")
        return 1
    except Exception as e:
        write(job_id, status="failed", error=str(e)[:400])
        return 1

    changed = git("status", "--porcelain", cwd=wt).stdout.strip()
    if not changed:
        write(job_id, status="no_changes", summary=summary[-1500:],
              finished=datetime.now().isoformat(timespec="seconds"))
        notify(f"Ghost upgrade made no changes:\n{request}\n\n{summary[-400:]}")
        return 0

    git("add", "-A", cwd=wt)
    git("commit", "-m", f"Ghost self-upgrade: {request[:60]}\n\n{summary[:800]}", cwd=wt)
    files = git("show", "--stat", "--oneline", "HEAD", cwd=wt).stdout.strip()

    write(job_id, status="ready", summary=summary[-1500:], files=files[-800:],
          worktree=str(wt), finished=datetime.now().isoformat(timespec="seconds"))
    notify(f"Ghost upgraded itself:\n{request}\n\n{summary[-500:]}\n\n"
           f"Branch: {branch}\nNothing is merged - review it first.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
