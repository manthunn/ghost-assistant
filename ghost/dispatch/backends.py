"""Model backends that execute one assembled frame.

`gemini` reuses the client `brain.py` already builds (same key, same billing
cap). `claude` shells out to Claude Code headless, the way the self-upgrade
runner does; there is deliberately no ANTHROPIC_API_KEY on this machine, so
the CLI's own login is the only Claude route, and the variable is stripped
from the child environment for the same reason upgrade_runner strips it.

The `brain` import is lazy: building the Gemini client needs GOOGLE_API_KEY,
and the engine must import (and be testable) without one.
"""
import json
import os
import pathlib
import subprocess

GEMINI_MODEL = "gemini-3.6-flash"
CLAUDE = pathlib.Path.home() / ".local" / "bin" / "claude.exe"
CLAUDE_TIMEOUT = 600  # a full refactor can legitimately take minutes


def gemini_json(prompt, schema):
    """Structured-output call; returns the parsed JSON object."""
    from ..brain import client
    from google.genai import types
    r = client.models.generate_content(
        model=GEMINI_MODEL, contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json", response_schema=schema, temperature=0))
    return json.loads(r.text)


def gemini_text(system, prompt, cwd=None):
    from ..brain import client
    from google.genai import types
    r = client.models.generate_content(
        model=GEMINI_MODEL, contents=prompt,
        config=types.GenerateContentConfig(system_instruction=system, temperature=0.2))
    return r.text or ""


def claude_text(system, prompt, cwd):
    if not CLAUDE.exists():
        raise RuntimeError(f"Claude Code is not installed at {CLAUDE}")
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    # Prompt goes over stdin, not argv: a frame carrying a whole source file
    # would blow Windows' 32k command-line limit. `--tools ""` makes the run
    # pure generation; `--setting-sources ""` keeps whatever CLAUDE.md or hooks
    # live near cwd out of the frame.
    proc = subprocess.run(
        [str(CLAUDE), "-p", "--output-format", "text", "--tools", "",
         "--setting-sources", "", "--no-session-persistence",
         "--system-prompt", system],
        input=prompt, cwd=str(cwd), capture_output=True, text=True,
        encoding="utf-8", timeout=CLAUDE_TIMEOUT, env=env)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "no diagnostic output").strip()
        raise RuntimeError(f"Claude Code failed (exit {proc.returncode}): {detail[-400:]}")
    return proc.stdout


# backend name -> callable(system, prompt, cwd) -> text
TEXT = {"gemini": gemini_text, "claude": claude_text}
