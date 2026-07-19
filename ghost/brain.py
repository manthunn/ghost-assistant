import json
import ollama
from .skills import TOOLS, FUNCTIONS

MODEL = "llama3.1"

SYSTEM = (
    "You are Ghost, a personal AI assistant running locally on the user's PC. "
    "You have real tools: use them whenever the user asks for an action or live "
    "information instead of saying you can't. Chain multiple tools if needed. "
    "Spoken replies must be short - 1 to 3 sentences - summarizing what you did "
    "or found. Never read out long lists or URLs; summarize them."
)

history = [{"role": "system", "content": SYSTEM}]

def _get(obj, key, default=None):
    try:
        v = obj[key]
        return v if v is not None else default
    except Exception:
        return getattr(obj, key, default)

def think(user_input, status=None):
    history.append({"role": "user", "content": user_input})
    for _ in range(6):  # allows chaining up to 6 tool calls
        resp = ollama.chat(model=MODEL, messages=history, tools=TOOLS)
        msg = resp["message"]
        calls = _get(msg, "tool_calls")
        if not calls:
            reply = _get(msg, "content", "") or "Done."
            history.append({"role": "assistant", "content": reply})
            return reply
        history.append(msg)
        for c in calls:
            fn_name = _get(_get(c, "function"), "name")
            args = _get(_get(c, "function"), "arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            if status:
                status(f"{fn_name}")
            print(f"  ⚙️ {fn_name}({args})")
            try:
                result = str(FUNCTIONS[fn_name](**args))
            except Exception as e:
                result = f"Tool error: {e}"
            history.append({"role": "tool", "name": fn_name,
                            "content": result[:4000]})
    return "That task needed too many steps - try breaking it down."