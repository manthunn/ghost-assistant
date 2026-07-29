from google.genai import types
from . import register
from ..brain import client, MODEL

AGENTS = {
    "researcher": "You are a research agent. Investigate the task and return a concise, factual briefing with key points.",
    "writer": "You are a writing agent. Produce polished final text for the task. Return only the text.",
    "coder": "You are a coding agent. Return complete, working code with brief usage notes.",
    "planner": "You are a planning agent. Break the goal into a numbered step-by-step plan.",
}

@register({"name": "delegate_task",
    "description": "Delegate a sub-task to a specialist AI agent and get back its result. Agents: researcher, writer, coder, planner. Use for complex work.",
    "parameters": {"type": "object", "properties": {
        "agent": {"type": "string", "enum": list(AGENTS)},
        "task": {"type": "string", "description": "clear description of the sub-task"}},
        "required": ["agent", "task"]}})
def delegate_task(agent: str, task: str):
    system = AGENTS.get(agent, AGENTS["researcher"])
    r = client.models.generate_content(model=MODEL, contents=task,
        config=types.GenerateContentConfig(system_instruction=system))
    return f"[{agent} agent report]\n" + (r.text or "")[:2500]