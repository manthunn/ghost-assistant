from . import register

@register({"name": "end_conversation",
    "description": "End the current conversation session, e.g. when the user says "
                    "goodbye, bye, or that's all for now.",
    "parameters": {"type": "object", "properties": {}, "required": []}})
def end_conversation():
    return "Ending session."
