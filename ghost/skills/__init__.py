import importlib
import pkgutil

TOOLS = []      # tool schemas given to the LLM
FUNCTIONS = {}  # name -> python function

def register(schema):
    """Decorator that turns any function into a Ghost skill."""
    def wrap(fn):
        TOOLS.append({"type": "function", "function": schema})
        FUNCTIONS[schema["name"]] = fn
        return fn
    return wrap

def load_all():
    for m in pkgutil.iter_modules(__path__):
        if m.name.startswith("_"):
            continue
        try:
            importlib.import_module(f"{__name__}.{m.name}")
            print(f"  [skill loaded] {m.name}")
        except Exception as e:
            print(f"  [skill DISABLED] {m.name} ({e})")