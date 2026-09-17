"""Pre-execution hook (input resolution) and DAG builder.

prehook() binds each declared input of a skill from, in the skill's own
declared order: a provided artifact, an upstream node's output, the caller's
answers (the "context" source - the engine cannot see the conversation, so
the Live model relays anything the user said), or the input's default.
A required input with none of those is a gap, and gaps become the questions
sent back to the caller.

build_layers() turns the chosen skills into topological layers. Edges come
from prerequisites, chain_after/chain_before, and any input that is fed from
another chosen skill's output. A terminal `__synthesize__` node always
follows every leaf.
"""
from graphlib import CycleError, TopologicalSorter

SYNTH = "__synthesize__"
MAX_QUESTIONS = 3
_CONTENT_TYPES = {"code", "text", "table", "url"}


def _from_artifact(inp, artifacts):
    # "text" is the universal content type: notes, a CSV or a URL list all
    # count as text sources. code/table/url inputs take only their own kind.
    wanted = inp["type"] if inp["type"] in _CONTENT_TYPES - {"text"} else None
    for a in artifacts:
        if wanted and a["type"] != wanted:
            continue
        if inp["type"] == "text" and a["type"] not in _CONTENT_TYPES:
            continue
        field = inp.get("artifact_field")
        value = a.get(field) if field else a.get("content")
        if value not in (None, ""):
            return value
    return None


def prehook(skill, intent, answers=None, upstream=None):
    """Return (bindings, assumptions, gaps).

    bindings:    {input_name: value}      everything resolved, None if optional and absent
    assumptions: [str]                    inputs that fell through to their default
    gaps:        [input dicts]            required inputs with nothing to bind
    """
    answers, upstream = answers or {}, upstream or {}
    artifacts = intent["artifacts"]["provided"]
    bindings, assumptions, gaps = {}, [], []
    for inp in skill["inputs"]:
        name, value = inp["name"], None
        for source in inp["source"]:
            if source == "artifact":
                value = _from_artifact(inp, artifacts)
            elif source == "upstream":
                value = upstream.get(name)
            elif source == "context":
                value = answers.get(name)
            elif source == "default":
                value = inp.get("default")
                if value is not None:
                    assumptions.append(f"{name}={value!s} (default, not stated)")
            if value not in (None, ""):
                break
            value = None
        if value is None and inp["required"] and inp.get("default") is None:
            gaps.append(inp)
        bindings[name] = value
    return bindings, assumptions, gaps


def questions_for(gaps_by_skill):
    """Flatten gaps into at most MAX_QUESTIONS (skill, input, question) tuples,
    in skill rank order, so the caller asks the most important thing first."""
    out = []
    for sid, gaps in gaps_by_skill:
        for inp in gaps:
            out.append((sid, inp["name"], inp["clarification_prompt"]))
            if len(out) == MAX_QUESTIONS:
                return out
    return out


def _dependencies(chosen):
    ids = {s["skill_id"] for s in chosen}
    producers = {}
    for s in chosen:
        for out in s["outputs"]:
            producers.setdefault(out["name"], s["skill_id"])
    deps = {sid: set() for sid in ids}
    for s in chosen:
        sid = s["skill_id"]
        deps[sid] |= set(s.get("prerequisites", {}).get("skills", [])) & ids
        deps[sid] |= set(s["compat"].get("chain_after", [])) & ids
        for later in set(s["compat"].get("chain_before", [])) & ids:
            deps[later].add(sid)
        for inp in s["inputs"]:
            if "upstream" in inp["source"]:
                producer = producers.get(inp["name"])
                if producer and producer != sid:
                    deps[sid].add(producer)
        deps[sid].discard(sid)
    return deps


def build_layers(chosen, log=print):
    """Return (layers, dropped). layers is a list of lists of skill_ids ending
    with [SYNTH]; dropped lists any skill removed to break a cycle (lowest
    rank first, since `chosen` arrives in rank order)."""
    chosen, dropped = list(chosen), []
    while True:
        deps = _dependencies(chosen)
        ts = TopologicalSorter(deps)
        try:
            ts.prepare()
        except CycleError:
            victim = chosen.pop()  # lowest ranked
            dropped.append(victim["skill_id"])
            log(f"  [dispatch plan: cycle, dropped {victim['skill_id']}]")
            continue
        layers = []
        while ts.is_active():
            ready = sorted(ts.get_ready())
            layers.append(ready)
            ts.done(*ready)
        layers.append([SYNTH])
        return layers, dropped
