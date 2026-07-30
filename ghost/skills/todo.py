"""A local to-do list, so Ghost has a real task source with no external service."""
import json
import pathlib
from datetime import datetime
from . import register

TODO_FILE = pathlib.Path(__file__).resolve().parent.parent / "todo.json"

def _load():
    if TODO_FILE.exists():
        try:
            return json.loads(TODO_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
    return []

def _save(items):
    TODO_FILE.write_text(json.dumps(items, indent=2), encoding="utf-8")

def pending():
    """Used by the daily briefing as well as the tools below."""
    return [t for t in _load() if not t.get("done")]

@register({"name": "add_task",
    "description": "Add a task or reminder to the user's to-do list.",
    "parameters": {"type": "object", "properties": {
        "task": {"type": "string", "description": "what needs doing"},
        "due": {"type": "string",
                 "description": "optional due date/time in plain words, e.g. 'Friday' or 'Aug 3'"}},
        "required": ["task"]}})
def add_task(task: str, due: str = ""):
    items = _load()
    items.append({"task": task.strip(), "due": (due or "").strip(),
                  "added": datetime.now().strftime("%Y-%m-%d"), "done": False})
    _save(items)
    return f"Added: {task}" + (f" (due {due})" if due else "")

@register({"name": "list_tasks",
    "description": "List the user's outstanding to-do items.",
    "parameters": {"type": "object", "properties": {}, "required": []}})
def list_tasks():
    items = pending()
    if not items:
        return "Nothing on the to-do list."
    lines = []
    for i, t in enumerate(items, 1):
        due = f" (due {t['due']})" if t.get("due") else ""
        lines.append(f"{i}. {t['task']}{due}")
    return f"{len(items)} outstanding task(s):\n" + "\n".join(lines)

@register({"name": "complete_task",
    "description": "Mark a to-do item as done, matched by some of its text.",
    "parameters": {"type": "object", "properties": {
        "match": {"type": "string", "description": "text identifying the task"}},
        "required": ["match"]}})
def complete_task(match: str):
    query = (match or "").lower().strip()
    items = _load()
    for t in items:
        if not t.get("done") and query and query in t["task"].lower():
            t["done"] = True
            t["completed"] = datetime.now().strftime("%Y-%m-%d")
            _save(items)
            return f"Marked done: {t['task']}"
    return f"No outstanding task matching '{match}'."
