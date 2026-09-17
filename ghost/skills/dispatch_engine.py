"""Expose the dispatch engine (ghost/dispatch/) to the Live model as a tool.

The Live model is the only thing that can talk to the user, so this wrapper
turns the engine's structured Result into instructions the model can act on:
speak a summary, ask a clarifying question, or answer without the engine.
Full output never comes back through here - live_voice caps tool results at
4000 characters - it goes to a file and the path is reported instead.
"""
from . import register
from ..dispatch import engine

_ARTIFACT_TYPES = {"code", "text", "table", "url"}


def _clean_artifacts(artifacts):
    out = []
    for a in artifacts or []:
        if not isinstance(a, dict) or not str(a.get("content") or "").strip():
            continue
        kind = a.get("type") if a.get("type") in _ARTIFACT_TYPES else "text"
        out.append({"type": kind, "name": str(a.get("name") or ""), "content": str(a["content"])})
    return out


def _clean_answers(answers):
    out = {}
    for a in answers or []:
        if isinstance(a, dict) and a.get("input") and a.get("value") not in (None, ""):
            out[str(a["input"])] = a["value"]
    return out


def _render(r):
    if r.status == "done":
        text = (f"Done via {', '.join(r.skills)}. {r.summary} "
                f"Full result saved to {r.output_path}.")
        if r.notes:
            text += " Note: " + "; ".join(r.notes[:3]) + "."
        return text + " Tell the user the summary and where the file is; do not read the file aloud."
    if r.status == "clarify":
        qs = "\n".join(f"{i}. ({name}) {q}" for i, (_, name, q) in enumerate(r.questions, 1))
        return (f"CLARIFICATION NEEDED (request_id={r.request_id}). Ask the user:\n{qs}\n"
                f"Then call dispatch_task again with request_id={r.request_id} and either "
                f"the material as artifacts or the reply as answers (use the input name in "
                f"parentheses). Nothing has been produced yet.")
    if r.status == "no_match":
        return ("No specialist skill matches this request. Answer it directly with your "
                "other tools instead.")
    return (f"Dispatch failed: {r.error} Tell the user it did not work; do not claim "
            f"anything was produced.")


@register({"name": "dispatch_task",
    "description": "Hand a substantial piece of work to a specialist skill and get a "
                    "finished deliverable saved to a file. Use for: fixing, cleaning up, "
                    "refactoring or security-auditing code; turning notes, sources or "
                    "data into an academic write-up; turning a table or CSV into charts. "
                    "Gather the material FIRST with your other tools (read_file, "
                    "on-screen text) and pass it as artifacts - this tool cannot read "
                    "files itself. Takes 10 to 90 seconds; tell the user you're on it. "
                    "If the result says CLARIFICATION NEEDED, ask the user the question, "
                    "then call again with the same request_id plus the new artifacts or "
                    "answers. When done, speak the summary and say where the file was "
                    "saved; never read the whole result aloud.",
    "parameters": {"type": "object", "properties": {
        "request": {"type": "string",
                    "description": "the user's request in their own words, in full"},
        "artifacts": {"type": "array", "description": "the material to work on",
            "items": {"type": "object", "properties": {
                "type": {"type": "string", "enum": ["code", "text", "table", "url"]},
                "name": {"type": "string", "description": "file name if known, e.g. cleanup.py"},
                "content": {"type": "string", "description": "the complete content"}},
                "required": ["type", "content"]}},
        "answers": {"type": "array",
            "description": "answers to an earlier clarification, keyed by the input name given in parentheses",
            "items": {"type": "object", "properties": {
                "input": {"type": "string"}, "value": {"type": "string"}},
                "required": ["input", "value"]}},
        "request_id": {"type": "string",
                       "description": "only when continuing a request that asked for clarification"}},
        "required": ["request"]}})
def dispatch_task(request: str, artifacts=None, answers=None, request_id: str = ""):
    artifacts, answers = _clean_artifacts(artifacts), _clean_answers(answers)
    if request_id:
        r = engine.resume(request_id, artifacts, answers)
        if r.status == "failed" and r.error.startswith("Unknown or expired"):
            # The plan is gone (TTL, or Ghost restarted): start over with what we have.
            r = engine.run(request, artifacts, answers)
    else:
        r = engine.run(request, artifacts, answers)
    for line in r.trace:
        print(f"    [dispatch] {line}")
    return _render(r)


@register({"name": "list_dispatch_skills",
    "description": "List the specialist skills dispatch_task can route to. Use when the "
                    "user asks what kinds of bigger jobs Ghost can do.",
    "parameters": {"type": "object", "properties": {}, "required": []}})
def list_dispatch_skills():
    return "\n".join(f"- {s['skill_id']}: {s['description']}" for s in engine.skills())
