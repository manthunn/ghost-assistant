import os
from dotenv import load_dotenv
from google import genai
from google.genai import types
from .skills import TOOLS, FUNCTIONS

load_dotenv()

MODEL = "gemini-3.6-flash"

SYSTEM = (
    "You are Ghost, a personal AI assistant running on the user's PC. "
    "You have real tools: use them whenever the user asks for an action or live "
    "information instead of saying you can't. Chain multiple tools if needed. "
    "Spoken replies must be short - 1 to 3 sentences - summarizing what you did "
    "or found. Never read out long lists or URLs; summarize them."
)

client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
_chat = None

def _get_chat():
    global _chat
    if _chat is None:
        gemini_tools = [types.Tool(function_declarations=[t["function"] for t in TOOLS])]
        config = types.GenerateContentConfig(system_instruction=SYSTEM, tools=gemini_tools)
        _chat = client.chats.create(model=MODEL, config=config)
    return _chat

def think(user_input, status=None):
    chat = _get_chat()
    resp = chat.send_message(user_input)
    for _ in range(6):  # allows chaining up to 6 tool calls
        calls = resp.function_calls
        if not calls:
            return resp.text or "Done."
        parts = []
        for c in calls:
            if status:
                status(c.name)
            print(f"  ⚙️ {c.name}({c.args})")
            try:
                result = str(FUNCTIONS[c.name](**c.args))
            except Exception as e:
                result = f"Tool error: {e}"
            parts.append(types.Part.from_function_response(
                name=c.name, response={"result": result[:4000]}))
        resp = chat.send_message(parts)
    return "That task needed too many steps - try breaking it down."
