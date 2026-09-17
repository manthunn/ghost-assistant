"""Pipeline orchestrator: classify, match, plan, execute, synthesize, save.

State between a clarifying question and its answer lives in `_state`, an
in-process dict keyed by request_id with a 15-minute TTL. Ghost sessions end
after five idle minutes, so anything older is a stale plan from a
conversation that already moved on.

Full results go to a file under ~/Documents/Ghost/dispatch/ and only a short
summary travels back through the tool result: live_voice caps tool results
at 4000 characters and Ghost speaks one to three sentences, so a refactored
file could never make it to the user through the voice channel anyway.
"""
import pathlib
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime

from . import backends, registry
from .intent import classify, describe_artifacts
from .matcher import AUTO, select
from .plan import SYNTH, build_layers, prehook, questions_for

OUT_DIR = pathlib.Path.home() / "Documents" / "Ghost" / "dispatch"
STATE_TTL = 15 * 60
PENDING = "<produced upstream>"
SUMMARY_RE = re.compile(r"^\s*\**SUMMARY:?\**\s*(.+?)\s*$", re.M)

_state = {}
_skills = None
_counter = 0


@dataclass
class Result:
    status: str                      # done | clarify | no_match | failed
    request_id: str
    summary: str = ""
    output_path: str = ""
    skills: list = field(default_factory=list)
    assumptions: list = field(default_factory=list)  # defaults taken; file only
    notes: list = field(default_factory=list)        # worth saying aloud: gaps, skips, drops
    questions: list = field(default_factory=list)   # [(skill_id, input_name, question)]
    trace: list = field(default_factory=list)
    error: str = ""


def skills(reload=False):
    global _skills
    if _skills is None or reload:
        _skills = registry.load()
    return _skills


def _new_id():
    global _counter
    _counter += 1
    return f"d-{datetime.now():%Y%m%d-%H%M%S}-{_counter}"


def _purge():
    cutoff = time.monotonic() - STATE_TTL
    for rid in [r for r, s in _state.items() if s["touched"] < cutoff]:
        del _state[rid]


def run(request, artifacts=None, answers=None, json_model=None, text_backends=None, log=print):
    """Plan and, if nothing is missing, execute a fresh request.

    `json_model` and `text_backends` default to the real Gemini/Claude calls,
    resolved here rather than in the signature so tests can patch `backends`.
    """
    json_model = backends.gemini_json if json_model is None else json_model
    text_backends = backends.TEXT if text_backends is None else text_backends
    _purge()
    reg = skills()
    rid = _new_id()
    trace = []
    intent = classify(request, artifacts, reg, model=json_model, log=log)
    trace.append(f"classify domains={intent['domains']} action={intent['action']} "
                 f"confidence={intent['confidence']} ambiguity={intent['ambiguity']['score']}")
    chosen, scores = select(reg, intent)
    trace.append("scores " + ", ".join(f"{k}={v}" for k, v in sorted(scores.items(), key=lambda kv: -kv[1])))
    if not chosen:
        trace.append("no skill above threshold")
        return Result("no_match", rid, trace=trace)
    trace.append("chosen " + ", ".join(s["skill_id"] for s in chosen))
    state = {
        "request_id": rid, "request": intent["raw"], "intent": intent,
        "artifacts": list(artifacts or []), "answers": dict(answers or {}),
        "chosen": chosen, "scores": scores, "trace": trace,
        "json_model": json_model, "text_backends": text_backends, "log": log,
        "touched": time.monotonic(),
    }
    _state[rid] = state
    return _plan_and_execute(state)


def resume(request_id, artifacts=None, answers=None):
    """Continue a request that returned `clarify`, with the new material."""
    _purge()
    state = _state.get(request_id)
    if state is None:
        return Result("failed", request_id or "",
                      error="Unknown or expired request_id. Start the request again.")
    state["artifacts"].extend(artifacts or [])
    state["answers"].update(answers or {})
    state["intent"]["artifacts"]["provided"] = describe_artifacts(state["artifacts"])
    state["touched"] = time.monotonic()
    state["trace"].append(f"resume +{len(artifacts or [])} artifacts, "
                          f"+{len(answers or {})} answers")
    return _plan_and_execute(state)


def _plan_and_execute(state):
    intent, chosen, answers = state["intent"], state["chosen"], state["answers"]
    trace, log = state["trace"], state["log"]
    # At planning time an input fed from another chosen skill is not a gap;
    # PENDING stands in until the producer has actually run.
    producible = {o["name"]: PENDING for s in chosen for o in s["outputs"]}
    gaps_by_skill, assumptions, notes, active = [], [], [], []
    for s in chosen:
        _, assumed, gaps = prehook(s, intent, answers, producible)
        if gaps and state["scores"][s["skill_id"]] < AUTO:
            # A chain partner the user never asked for: leave it out quietly
            # rather than interrogate them about material for a skill they
            # did not request. Not removed from state["chosen"]: material
            # supplied on a resume may make it viable after all.
            trace.append(f"drop {s['skill_id']}: partner missing "
                         + ", ".join(g["name"] for g in gaps))
            continue
        active.append(s)
        assumptions += [f"{s['skill_id']}: {a}" for a in assumed]
        if gaps:
            gaps_by_skill.append((s["skill_id"], gaps))
    if gaps_by_skill:
        questions = questions_for(gaps_by_skill)
        trace.append("clarify " + ", ".join(f"{sid}.{name}" for sid, name, _ in questions))
        return Result("clarify", state["request_id"], questions=questions,
                      skills=[s["skill_id"] for s in active], trace=trace)
    if intent["ambiguity"]["gaps"]:
        # File only: the classifier's gap labels ("incomplete snippet") are
        # useful on paper and confusing when read aloud.
        assumptions.append("unspecified, proceeding with defaults: "
                           + ", ".join(intent["ambiguity"]["gaps"][:4]))

    layers, dropped = build_layers(active, log=log)
    trace.append("layers " + " -> ".join("+".join(layer) for layer in layers))
    if dropped:
        notes.append("dropped to break a dependency cycle: " + ", ".join(dropped))
        active = [s for s in active if s["skill_id"] not in dropped]
    del _state[state["request_id"]]  # a plan executes once; a re-ask starts fresh

    chosen = active
    by_id = {s["skill_id"]: s for s in chosen}
    outputs, upstream, skipped = {}, {}, []
    try:
        for layer in layers:
            if layer == [SYNTH]:
                break
            results = _run_layer(layer, by_id, state, upstream, trace)
            for sid, (text, err) in results.items():
                if err is not None:
                    if sid == chosen[0]["skill_id"]:
                        # The lead skill is the deliverable; without it there
                        # is nothing worth saving.
                        raise RuntimeError(f"{sid} failed twice: {err}")
                    skipped.append(sid)
                    trace.append(f"skip {sid}: {err}")
                    continue
                body, summary = _split_summary(text)
                outputs[sid] = {"body": body, "summary": summary}
                for out in by_id[sid]["outputs"]:
                    upstream[out["name"]] = body if out.get("pass_full") else summary
        final, summary = _synthesize(chosen, outputs, state, trace)
    except Exception as e:
        trace.append(f"failed {_describe(e)}")
        log(f"  [dispatch failed] {_describe(e)}")
        return Result("failed", state["request_id"], error=_describe(e)[:400],
                      skills=[s["skill_id"] for s in chosen], trace=trace)
    if skipped:
        notes.append("skipped after two failures: " + ", ".join(skipped))
    path = _save(state, chosen, final, assumptions + notes, trace)
    trace.append(f"saved {path}")
    return Result("done", state["request_id"], summary=summary, output_path=str(path),
                  skills=[s["skill_id"] for s in chosen if s["skill_id"] not in skipped],
                  assumptions=assumptions, notes=notes, trace=trace)


def _describe(e):
    # str(e) is empty for a fair number of exceptions (BrokenBarrierError,
    # bare KeyError on ''), and an empty error is useless in a trace.
    return f"{type(e).__name__}: {e}" if str(e) else type(e).__name__


def _run_layer(layer, by_id, state, upstream, trace):
    """Run every node in a layer; nodes marked parallel_safe share a pool."""
    def one(sid):
        skill = by_id[sid]
        err = None
        backend = skill.get("backend", "gemini")
        for attempt in (1, 2):
            try:
                t0 = time.monotonic()
                text = _run_node(skill, state, upstream, backend)
                trace.append(f"exec {sid} attempt {attempt} ok {time.monotonic() - t0:.1f}s "
                             f"backend={backend}")
                return text, None
            except Exception as e:
                err = _describe(e)[:300]
                trace.append(f"exec {sid} attempt {attempt} error {err}")
                # The Claude CLI depends on a login that can lapse; Gemini is
                # the key Ghost already runs on. Retry there rather than fail.
                backend = "gemini"
        return None, err

    parallel = len(layer) > 1 and all(by_id[s]["compat"]["parallel_safe"] for s in layer)
    if not parallel:
        return {sid: one(sid) for sid in layer}
    with ThreadPoolExecutor(max_workers=len(layer)) as pool:
        return dict(zip(layer, pool.map(one, layer)))


def _run_node(skill, state, upstream, backend):
    intent = state["intent"]
    bindings, assumed, gaps = prehook(skill, intent, state["answers"], upstream)
    if gaps:
        # Only reachable if a producer was skipped after failing.
        raise RuntimeError("missing upstream input " + ", ".join(g["name"] for g in gaps))
    frame = assemble_frame(skill, intent, bindings, assumed, upstream)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    text = state["text_backends"][backend](skill["persona"], frame, OUT_DIR)
    if not (text or "").strip():
        raise RuntimeError("empty response")
    return text


def _fmt_input(inp, value):
    if value is None:
        return f"{inp['name']}: (not provided)"
    if inp["type"] in ("code", "text", "table", "json") and "\n" in str(value):
        return f"{inp['name']}:\n```\n{value}\n```"
    return f"{inp['name']}: {value}"


def assemble_frame(skill, intent, bindings, assumed, upstream):
    constraints = list(skill.get("constraints", [])) + [
        f"(implied by the request) {c}" for c in intent["constraints"]["implicit"]] + [
        f"(stated by the user) {c}" for c in intent["constraints"]["explicit"]]
    upstream_lines = [f"{k}:\n{v}" for k, v in upstream.items() if v and v != PENDING] or ["none"]
    lines = [
        f"[SKILL FRAME: {skill['skill_id']} v{skill['version']}]",
        f"USER REQUEST: {intent['raw']}",
        "",
        "CONSTRAINTS:",
        *[f"  - {c}" for c in constraints],
        "",
        "INPUTS:",
        *[_fmt_input(inp, bindings.get(inp["name"])) for inp in skill["inputs"]],
        "",
        "ASSUMED (state these in the output if they matter): " + ("; ".join(assumed) or "nothing"),
        "",
        "UPSTREAM RESULTS:",
        *upstream_lines,
        "",
        "PROCEDURE:",
        *[f"  {i}. {step}" for i, step in enumerate(skill["procedure"], 1)],
        "",
        "OUTPUT CONTRACT (fill this exactly):",
        skill["output_contract"],
        "",
        "Finish with one final line that starts with 'SUMMARY:' followed by one or two "
        "plain sentences a voice assistant can read aloud: what you produced and the "
        "single most important finding. No markdown on that line.",
        "[END FRAME]",
    ]
    return "\n".join(lines)


def _split_summary(text):
    matches = list(SUMMARY_RE.finditer(text))
    if not matches:
        flat = " ".join(text.split())
        return text.strip(), flat[:300]
    last = matches[-1]
    body = (text[:last.start()] + text[last.end():]).strip()
    return body, last.group(1).strip()


def _synthesize(chosen, outputs, state, trace):
    if not outputs:
        raise RuntimeError("no skill produced output")
    if len(outputs) == 1:
        (sid, out), = outputs.items()
        trace.append(f"synthesize passthrough {sid}")
        return out["body"], out["summary"]
    lead = next(s for s in chosen if s["skill_id"] in outputs)
    prompt = "\n\n".join(
        [f"USER REQUEST: {state['intent']['raw']}",
         "Merge the specialist outputs below into ONE deliverable. Keep every finding, "
         "figure and citation; remove duplication; do not invent anything not present.",
         "TARGET CONTRACT:\n" + lead["output_contract"]]
        + [f"=== OUTPUT OF {sid} ===\n{out['body']}" for sid, out in outputs.items()]
        + ["Finish with one final line starting with 'SUMMARY:' - one or two plain sentences "
           "for a voice assistant to read aloud."])
    backend = state["text_backends"][lead.get("backend", "gemini")]
    t0 = time.monotonic()
    text = backend("You are an editor merging specialist reports. Precise, no filler.",
                   prompt, OUT_DIR)
    trace.append(f"synthesize merged {len(outputs)} outputs {time.monotonic() - t0:.1f}s")
    return _split_summary(text)


def _save(state, chosen, final, assumptions, trace):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    lead = chosen[0]["skill_id"].replace(".", "-")
    path = OUT_DIR / f"{state['request_id']}-{lead}.md"
    header = [f"# {state['request']}", "",
              f"Request {state['request_id']}, {datetime.now():%Y-%m-%d %H:%M}. "
              f"Skills: {', '.join(s['skill_id'] for s in chosen)}.", ""]
    if assumptions:
        header += ["Assumed:"] + [f"- {a}" for a in assumptions] + [""]
    footer = ["", "---", "Trace:"] + [f"- {t}" for t in trace]
    path.write_text("\n".join(header) + final.strip() + "\n" + "\n".join(footer) + "\n",
                    encoding="utf-8")
    return path
