import json
import pathlib
from datetime import datetime
from . import register

MEM_FILE = pathlib.Path(__file__).resolve().parent.parent / "memory.json"

def _load():
    if MEM_FILE.exists():
        return json.loads(MEM_FILE.read_text(encoding="utf-8"))
    return []

@register({"name": "remember",
    "description": "Save a fact about the user permanently, e.g. preferences, names, important dates.",
    "parameters": {"type": "object", "properties": {
        "fact": {"type": "string"}}, "required": ["fact"]}})
def remember(fact: str):
    mem = _load()
    mem.append({"fact": fact, "on": datetime.now().strftime("%Y-%m-%d")})
    MEM_FILE.write_text(json.dumps(mem, indent=2), encoding="utf-8")
    return "Remembered."

@register({"name": "recall",
    "description": "Recall saved facts about the user. Use when asked what you remember, or when context would help.",
    "parameters": {"type": "object", "properties": {}}})
def recall():
    mem = _load()
    if not mem:
        return "No memories saved yet."
    return "\n".join(f"- {m['fact']} (saved {m['on']})" for m in mem[-30:])