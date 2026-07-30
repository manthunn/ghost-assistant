"""Let Ghost see the screen.

The frame itself can't be returned through a tool response (those are text only),
so live_voice intercepts `look_at_screen`, grabs the screen, and pushes it into
the Live session as a realtime video frame. This module owns the capture and the
tool declaration; the wiring lives in live_voice._handle_tool_calls.

On-demand rather than a continuous video stream: the Live API bills per minute of
media, so streaming the screen nonstop would meter constantly. This only looks
when asked.
"""
import io
from . import register

MAX_DIM = 1024   # downscale: full 1920x1080 costs far more tokens for no real gain
JPEG_QUALITY = 80

def capture_screen_jpeg(max_dim=MAX_DIM, quality=JPEG_QUALITY):
    """Grab the primary screen as JPEG bytes, ready for send_realtime_input(video=...)."""
    from PIL import ImageGrab
    img = ImageGrab.grab()
    img.thumbnail((max_dim, max_dim))
    if img.mode != "RGB":
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()

@register({"name": "look_at_screen",
    "description": "Look at what's currently on the user's screen. Use this whenever "
                    "the user asks about what they're looking at, what's on screen, to "
                    "read or explain something visible, help with an error message, or "
                    "review code or a document they're viewing. After calling this you "
                    "will be shown the screen and can answer from it.",
    "parameters": {"type": "object", "properties": {}, "required": []}})
def look_at_screen():
    # live_voice intercepts this name and sends the real frame. Reaching this body
    # means something other than the Live session invoked it (e.g. brain.py's
    # turn-based fallback), which has no way to deliver an image.
    return ("Screen capture requires the live voice session; it isn't available on "
            "this code path.")
