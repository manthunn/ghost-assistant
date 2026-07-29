import os
from datetime import datetime
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
    "Input comes from speech-to-text and may contain mis-transcribed words - if "
    "something looks like a garbled version of a known app, place, or command "
    "(e.g. a near-miss of Vivaldi, Notepad, Spotify, Deakin Hall), silently treat "
    "it as that instead of taking the literal text at face value. "
    "For live info (weather, sports, news, prices) prefer web_search over "
    "run_command - it's faster and doesn't need confirmation. "
    "Every user message is prefixed with the current date/time in brackets - use "
    "it to resolve relative references like 'tonight' or 'this weekend', but "
    "don't read that prefix back to the user. "
    "The user lives at Deakin Hall, 56 College Way, Clayton, VIC 3168, Australia "
    "(Monash University Clayton Campus, Melbourne), timezone Australia/Melbourne. "
    "Use Clayton/Melbourne for weather, local news, and nearby-place queries without "
    "asking or looking up location first. In Australia 'football' usually means AFL "
    "or NRL, not the NFL, unless the user says otherwise. "
    "If web_search doesn't give a clear answer after 2 tries, stop searching and tell "
    "the user what you found (or that you couldn't confirm it) instead of repeating "
    "near-identical searches - never spend more than 3 tool calls on one sub-question. "
    "Be decisive and direct: no filler like 'as an AI', no apologizing, no hedging "
    "before acting. Mirror the user's pace - a quick request gets a one-sentence "
    "answer, brainstorming gets more depth. When you take an action, only report it "
    "succeeded if the tool result actually confirms that - never assume or guess. "
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
    now = datetime.now().strftime("%A, %B %d, %Y, %I:%M %p")
    resp = chat.send_message(f"[Current date/time: {now}] {user_input}")
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
