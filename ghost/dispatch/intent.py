"""Raw intent classifier.

Two passes. The rule pass is deterministic (regex tables, artifact types) and
runs first. The model pass is a single structured-output call that fills in
what rules cannot see (implicit constraints, ambiguity, target output) AND
scores every registered skill's semantic_anchor against the request, so the
matcher does not need a second model round-trip.

If the model pass fails for any reason the rule-pass intent is returned with
semantic scores of zero. The matcher's weights are set so a clear request
still crosses the auto-invoke threshold on rules alone.
"""
import json
import re

from . import registry

# (pattern, domain or None, action). The first action hit wins; every domain
# hit is collected. Code-flavoured words only count as software.code when the
# request actually involves code (see _CODE_CONTEXT), so "fix my essay" does
# not route to a refactor skill.
RULES = [
    (r"\b(fix|refactor|clean(ed| up)?|lint|tidy|debug|bug(s|gy)?)\b", "software.code", "transform"),
    (r"\b(secure|security|vuln\w*|inject\w*|audit)\b", "software.code", "validate"),
    (r"\b(cite|citation|literature|lit review|paper|synthesi[sz]e|thesis|dissertation|"
     r"findings section|related work|abstract)\b", "research.academic", "generate"),
    (r"\bwrite (it |this |these |them )?up\b", "research.academic", "generate"),
    (r"\b(chart|plot|graph|visuali[sz]e|dashboard|histogram|bar chart|line chart)\b",
     "data.visualization", "visualize"),
    (r"\b(trend|breakdown|distribution)\b", "data.analysis", "analyze"),
    (r"\b(summari[sz]e|tl;?dr)\b", None, "analyze"),
    (r"\b(explain|what does|how does|why does)\b", None, "explain"),
    (r"\b(plan|roadmap|steps to)\b", "ops.planning", "plan"),
]
_CODE_CONTEXT = re.compile(
    r"\b(script|code|function|class|module|repo|python|javascript|typescript|"
    r"rust|golang|bash|powershell)\b|\.(py|js|ts|go|rs|java|cs|cpp|c|sh|ps1)\b", re.I)
ARTIFACT_DOMAIN = {"code": "software.code", "table": "data.analysis"}
EXT_LANG = {
    ".py": "python", ".js": "javascript", ".ts": "typescript", ".go": "go",
    ".rs": "rust", ".java": "java", ".cs": "csharp", ".cpp": "cpp", ".c": "c",
    ".sh": "bash", ".ps1": "powershell", ".rb": "ruby", ".php": "php",
}

# Schema for the model pass. Enums keep the model inside the closed taxonomy;
# anything outside it would score zero against every skill anyway.
MODEL_SCHEMA = {
    "type": "object",
    "properties": {
        "domains": {"type": "array", "items": {"type": "string", "enum": sorted(registry.DOMAINS)}},
        "action": {"type": "string", "enum": sorted(registry.ACTIONS)},
        "constraints_explicit": {"type": "array", "items": {"type": "string"}},
        "constraints_implicit": {"type": "array", "items": {"type": "string"}},
        "target_output": {"type": "string"},
        "ambiguity_score": {"type": "number"},
        "ambiguity_gaps": {"type": "array", "items": {"type": "string"}},
        "semantic": {"type": "array", "items": {
            "type": "object",
            "properties": {"skill_id": {"type": "string"}, "score": {"type": "number"}},
            "required": ["skill_id", "score"]}},
    },
    "required": ["domains", "action", "constraints_explicit", "constraints_implicit",
                 "target_output", "ambiguity_score", "ambiguity_gaps", "semantic"],
}

MODEL_PROMPT = """Classify this request for a skill router. Be literal about what the user asked for.

REQUEST:
{raw}

ARTIFACTS PROVIDED:
{artifacts}

Rules:
- domains: at most 2, from the allowed list, most relevant first.
- action: the single verb that best describes what the user wants done.
- constraints_explicit: constraints the user stated. constraints_implicit: ones a careful colleague would assume (preserve behavior, same language, etc).
- target_output: short label for the shape of the deliverable, e.g. "refactored code + findings", "academic section", "chart specs".
- ambiguity_score: 0 (fully specified) to 1 (cannot act). ambiguity_gaps: what is unspecified, as short labels.
- semantic: for EVERY skill below, how well its description matches this request, 0 to 1. Score by the user's situation, not by keyword overlap. A skill that does not apply gets 0.

SKILLS:
{skills}
"""


def describe_artifacts(artifacts):
    """Normalise caller-supplied artifacts into the envelope's shape."""
    out = []
    for i, a in enumerate(artifacts or []):
        content = a.get("content") or ""
        name = a.get("name") or ""
        kind = a.get("type") or "text"
        ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
        lang = a.get("lang") or EXT_LANG.get(ext) or ("auto-detect" if kind == "code" else None)
        out.append({"id": f"a{i + 1}", "type": kind, "name": name, "lang": lang,
                    "loc": len(content.splitlines()), "content": content})
    return out


def rule_pass(raw, artifacts):
    text = raw.lower()
    code_context = bool(_CODE_CONTEXT.search(raw)) or any(a["type"] == "code" for a in artifacts)
    domains, action, signals = [], None, []
    for pattern, domain, act in RULES:
        if not re.search(pattern, text):
            continue
        signals.append(pattern)
        if action is None:
            action = act
        if domain == "software.code" and not code_context:
            continue
        if domain and domain not in domains:
            domains.append(domain)
    for a in artifacts:
        d = ARTIFACT_DOMAIN.get(a["type"])
        if d and d not in domains:
            domains.append(d)
    return {"domains": domains[:2], "action": action, "signals": signals}


def _complexity(artifacts, domains, action, explicit):
    level = 1
    level += sum(1 for a in artifacts if a["loc"] > 100)
    if len(domains) > 1:
        level += 1
    if action in ("plan", "transform") and len(explicit) > 2:
        level += 1
    return min(5, level)


def _model_pass(raw, artifacts, skills, model):
    art_lines = [f"- {a['id']}: {a['type']}{' ' + a['lang'] if a['lang'] else ''}"
                 f"{' ' + a['name'] if a['name'] else ''}, {a['loc']} lines. "
                 f"Starts: {a['content'][:200]!r}" for a in artifacts] or ["(none)"]
    skill_lines = [f"- {s['skill_id']}: {s['semantic_anchor']}" for s in skills]
    prompt = MODEL_PROMPT.format(raw=raw, artifacts="\n".join(art_lines),
                                 skills="\n".join(skill_lines))
    data = model(prompt, MODEL_SCHEMA)
    if isinstance(data, str):
        data = json.loads(data)
    ids = {s["skill_id"] for s in skills}
    semantic = {}
    for item in data.get("semantic", []):
        if item.get("skill_id") in ids:
            semantic[item["skill_id"]] = max(0.0, min(1.0, float(item.get("score", 0))))
    data["semantic"] = semantic
    data["domains"] = [d for d in data.get("domains", []) if d in registry.DOMAINS][:2]
    if data.get("action") not in registry.ACTIONS:
        data["action"] = None
    return data


def classify(raw, artifacts, skills, model=None, log=print):
    """Return an IntentEnvelope dict.

    `model(prompt, schema) -> dict` is the structured-output call; pass None
    for rule-only classification (tests, or a backend outage).
    """
    raw = " ".join((raw or "").split())
    arts = describe_artifacts(artifacts)
    rules = rule_pass(raw, arts)
    intent = {
        "raw": raw,
        "domains": list(rules["domains"]),
        "action": rules["action"],
        "constraints": {"explicit": [], "implicit": []},
        "artifacts": {"provided": arts},
        "target_output": None,
        "ambiguity": {"score": 0.5 if not rules["domains"] else 0.3, "gaps": []},
        "semantic": {},
        "confidence": 0.5,
        "signals": rules["signals"],
    }
    if model is not None:
        try:
            m = _model_pass(raw, arts, skills, model)
        except Exception as e:
            log(f"  [dispatch classify: model pass failed, using rules] {e}")
        else:
            # Rules win on domain when they fired: they are exact and the model
            # occasionally over-generalises ("software.code" for a maths essay
            # that mentions Python). The model fills everything rules cannot.
            for d in m["domains"]:
                if d not in intent["domains"] and len(intent["domains"]) < 2:
                    intent["domains"].append(d)
            intent["action"] = intent["action"] or m["action"]
            intent["constraints"] = {"explicit": m.get("constraints_explicit", []),
                                     "implicit": m.get("constraints_implicit", [])}
            intent["target_output"] = m.get("target_output")
            intent["ambiguity"] = {"score": max(0.0, min(1.0, float(m.get("ambiguity_score", 0.5)))),
                                   "gaps": m.get("ambiguity_gaps", [])}
            intent["semantic"] = m["semantic"]
            agree = bool(rules["domains"]) and rules["domains"][0] in m["domains"]
            intent["confidence"] = 0.9 if agree else 0.7
    intent["complexity"] = {
        "level": _complexity(arts, intent["domains"], intent["action"],
                             intent["constraints"]["explicit"]),
        "signals": [f"{len(arts)}_artifacts"] + [f"{a['id']}_{a['loc']}_lines" for a in arts],
    }
    return intent
