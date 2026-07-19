import ollama
from . import register

MODEL = "llama3.1"
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
    r = ollama.chat(model=MODEL, messages=[
        {"role": "system", "content": system},
        {"role": "user", "content": task}])
    return f"[{agent} agent report]\n" + r["message"]["content"][:2500]