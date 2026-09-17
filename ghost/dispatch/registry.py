"""Load and validate the dispatch skill registry (skills.json).

Validation is hand-rolled rather than pulling in `jsonschema`: the shape is
small and fixed, and a dependency for a 60-line check is not worth the extra
install step on the laptop Ghost runs from. Every entry is checked on load;
an invalid entry is reported and dropped, so one bad skill can never stop the
engine (or Ghost) from importing.
"""
import json
import pathlib

SKILLS_FILE = pathlib.Path(__file__).resolve().parent / "skills.json"

DOMAINS = {
    "software.code", "software.infra", "software.data",
    "research.academic", "research.market",
    "data.analysis", "data.visualization",
    "writing.technical", "writing.business", "writing.creative",
    "ops.planning", "ops.decision",
    "general.qa",
}
ACTIONS = {"generate", "transform", "analyze", "explain", "retrieve",
           "visualize", "plan", "validate"}
INPUT_TYPES = {"code", "text", "table", "url", "enum", "bool", "number", "json"}
INPUT_SOURCES = {"artifact", "upstream", "context", "default"}
BACKENDS = {"gemini", "claude"}

REQUIRED = ("skill_id", "version", "description", "triggers", "semantic_anchor",
            "inputs", "outputs", "persona", "procedure", "output_contract", "compat")


class RegistryError(ValueError):
    pass


def _expect(cond, msg):
    if not cond:
        raise RegistryError(msg)


def _list_of_str(value, name):
    _expect(isinstance(value, list) and all(isinstance(v, str) for v in value),
            f"{name} must be a list of strings")


def validate(skill):
    """Raise RegistryError describing the first problem, else return the skill."""
    _expect(isinstance(skill, dict), "skill must be an object")
    sid = skill.get("skill_id", "<no id>")
    for key in REQUIRED:
        _expect(key in skill, f"{sid}: missing '{key}'")
    _expect(isinstance(sid, str) and "." in sid and sid == sid.lower(),
            f"{sid}: skill_id must be lowercase dotted, e.g. code.refactor_audit")

    t = skill["triggers"]
    _expect(isinstance(t, dict), f"{sid}: triggers must be an object")
    for key in ("domains", "actions"):
        _expect(key in t, f"{sid}: triggers.{key} is required")
    _list_of_str(t["domains"], f"{sid}: triggers.domains")
    _list_of_str(t["actions"], f"{sid}: triggers.actions")
    _expect(set(t["domains"]) <= DOMAINS, f"{sid}: unknown domain in triggers.domains")
    _expect(set(t["actions"]) <= ACTIONS, f"{sid}: unknown action in triggers.actions")
    for key in ("keywords", "patterns", "artifact_types", "negative"):
        _list_of_str(t.get(key, []), f"{sid}: triggers.{key}")

    _expect(isinstance(skill["inputs"], list), f"{sid}: inputs must be a list")
    for inp in skill["inputs"]:
        for key in ("name", "type", "required", "source"):
            _expect(key in inp, f"{sid}: input missing '{key}'")
        _expect(inp["type"] in INPUT_TYPES, f"{sid}: input {inp['name']} has unknown type")
        _list_of_str(inp["source"], f"{sid}: input {inp['name']}.source")
        _expect(set(inp["source"]) <= INPUT_SOURCES,
                f"{sid}: input {inp['name']} has unknown source")
        _expect(isinstance(inp["required"], bool),
                f"{sid}: input {inp['name']}.required must be boolean")
        _expect(isinstance(inp.get("artifact_field", ""), str),
                f"{sid}: input {inp['name']}.artifact_field must be a string")
        if inp["required"] and inp.get("default") is None:
            _expect(bool(inp.get("clarification_prompt")),
                    f"{sid}: required input {inp['name']} needs a clarification_prompt")

    _expect(isinstance(skill["outputs"], list) and skill["outputs"],
            f"{sid}: outputs must be a non-empty list")
    for out in skill["outputs"]:
        _expect("name" in out and "type" in out, f"{sid}: output missing name/type")

    _expect(isinstance(skill["procedure"], list) and skill["procedure"],
            f"{sid}: procedure must be a non-empty list")
    _list_of_str(skill["procedure"], f"{sid}: procedure")
    _list_of_str(skill.get("constraints", []), f"{sid}: constraints")

    c = skill["compat"]
    _expect(isinstance(c, dict) and isinstance(c.get("parallel_safe"), bool),
            f"{sid}: compat.parallel_safe must be boolean")
    for key in ("chain_after", "chain_before", "exclusive_with"):
        _list_of_str(c.get(key, []), f"{sid}: compat.{key}")

    p = skill.get("prerequisites", {})
    _list_of_str(p.get("skills", []), f"{sid}: prerequisites.skills")
    _expect(skill.get("backend", "gemini") in BACKENDS, f"{sid}: unknown backend")
    prio = skill.get("priority", 50)
    _expect(isinstance(prio, int) and 0 <= prio <= 100, f"{sid}: priority must be 0..100")
    return skill


def _cross_check(skills):
    """Every skill referenced by another skill must exist. Dangling references
    would silently never fire (a prerequisite that is never selected) or, worse,
    create an edge to a node the DAG builder cannot place."""
    ids = {s["skill_id"] for s in skills}
    for s in skills:
        refs = (s.get("prerequisites", {}).get("skills", [])
                + s["compat"].get("chain_after", [])
                + s["compat"].get("chain_before", [])
                + s["compat"].get("exclusive_with", []))
        for r in refs:
            _expect(r in ids, f"{s['skill_id']}: references unknown skill '{r}'")


def load(path=SKILLS_FILE, log=print):
    """Return the list of valid skills. Invalid entries are logged and skipped."""
    raw = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    _expect(isinstance(raw, list), "skills.json must be a JSON array")
    valid, seen = [], set()
    for entry in raw:
        try:
            skill = validate(entry)
            _expect(skill["skill_id"] not in seen, f"duplicate skill_id {skill['skill_id']}")
            seen.add(skill["skill_id"])
            valid.append(skill)
        except RegistryError as e:
            log(f"  [dispatch skill DISABLED] {e}")
    # A dangling reference is a registry bug, not a runtime condition: fail
    # loudly at load rather than misroute later.
    _cross_check(valid)
    return valid
