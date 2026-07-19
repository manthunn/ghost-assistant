import webbrowser
import keyboard
from . import register

KEYS = {"play": "play/pause media", "pause": "play/pause media",
        "next": "next track", "previous": "previous track",
        "volume_up": "volume up", "volume_down": "volume down",
        "mute": "volume mute"}

@register({"name": "media_control",
    "description": "Control whatever music/video is playing on the PC: play, pause, next, previous, volume_up, volume_down, mute.",
    "parameters": {"type": "object", "properties": {
        "action": {"type": "string", "enum": list(KEYS)}},
        "required": ["action"]}})
def media_control(action: str):
    key = KEYS.get(action)
    if not key:
        return f"Unknown action {action}"
    if action in ("volume_up", "volume_down"):
        for _ in range(4):
            keyboard.send(key)
    else:
        keyboard.send(key)
    return f"Media: {action}."

@register({"name": "play_on_spotify",
    "description": "Open Spotify and search for a song, artist or playlist to play.",
    "parameters": {"type": "object", "properties": {
        "query": {"type": "string", "description": "song/artist/playlist name"}},
        "required": ["query"]}})
def play_on_spotify(query: str):
    webbrowser.open(f"spotify:search:{query.replace(' ', '%20')}")
    return f"Searching Spotify for {query}. Press play when it appears."