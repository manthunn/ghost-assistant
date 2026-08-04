"""Current-time helpers.

Deliberately dependency-free so both `brain.py` and the skills can import it
without dragging in the Gemini client (brain.py builds one at import time, and
skills/briefing.py importing brain would be circular).

This exists because the Live API path had no clock at all. `brain.think()`
prefixes every message with the time, but the Live session never calls it - so
the model was left guessing, and an assistant with no clock guesses "morning".
"""
from datetime import datetime


def part_of_day(dt=None):
    h = (dt or datetime.now()).hour
    if h < 5:
        return "the middle of the night"
    if h < 12:
        return "morning"
    if h < 17:
        return "afternoon"
    if h < 21:
        return "evening"
    return "night"


def is_small_hours(dt=None):
    """Midnight to 4:59 - the window where an unprompted briefing is unwelcome.

    Same boundary part_of_day() uses for "the middle of the night", kept here so
    the two can't drift apart.
    """
    return (dt or datetime.now()).hour < 5


def is_winding_down(dt=None):
    """True in the evening/night, when 'today' is nearly over and the useful
    horizon is tomorrow rather than the next few hours."""
    h = (dt or datetime.now()).hour
    return h >= 17 or h < 5


def stamp(dt=None):
    """'Tuesday, 4 August 2026, 11:47 PM' - no zero-padded day or hour, because
    this gets read out loud."""
    now = dt or datetime.now()
    return now.strftime("%A, %d %B %Y, %I:%M %p").replace(" 0", " ")


def time_context(dt=None):
    """The block appended to the system prompt so the model knows the real time.

    Stated as fact rather than as something to look up: a model that is unsure
    what time it is will default to a morning greeting, which is exactly the
    bug this fixes.
    """
    now = dt or datetime.now()
    return (
        f"RIGHT NOW it is {stamp(now)} in Melbourne - {part_of_day(now)}. "
        "That is the real current time; trust it completely and never assume "
        "it is morning. Match any greeting to it - 'good morning' is wrong in "
        "the afternoon, evening or at night, and in the middle of the night "
        "skip the cheerful greeting entirely and just be brief and useful. "
        "If the user opens with a direct request, act on it instead of "
        "greeting at all. Use this time to resolve 'today', 'tonight', "
        "'tomorrow' and 'this weekend'. This timestamp is from when the "
        "session started, so if the conversation has run a long time, assume "
        "the clock has moved on."
    )
