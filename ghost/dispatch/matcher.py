"""Intent-to-skill matcher. Deterministic given the intent envelope.

The only model-derived input is `intent["semantic"]`, which the classifier
already produced; everything here is arithmetic on the envelope and the
registry, so every routing decision can be reproduced offline in a test.
"""
import re

WEIGHTS = {"domain": 0.30, "action": 0.20, "keyword": 0.15, "semantic": 0.25,
           "artifact": 0.10, "negative": 0.50, "prereq": 0.20}
AUTO = 0.70       # invoke
# Pulled in only as a prerequisite or chain partner of an AUTO skill. Low on
# purpose: the lead skill vouches for the partner, and a partner whose required
# inputs turn out to be missing is dropped by the planner rather than asked
# about, so the cost of a false positive here is one wasted score, not a
# question to the user.
CANDIDATE = 0.35
MAX_SKILLS = 4


def _overlap(a, b):
    """Overlap coefficient, not Jaccard. A request spanning two domains
    (write-up plus chart) must not halve the score of a skill that covers one
    of them fully, and a broad skill must not be penalised for covering more
    than the request asked for. Measured: Jaccard left the academic skill at
    0.675 on a two-domain request it clearly owned."""
    a, b = set(a), set(b)
    return len(a & b) / min(len(a), len(b)) if a and b else 0.0


def score(skill, intent, chosen_ids=()):
    t = skill["triggers"]
    raw = intent["raw"].lower()
    hits = sum(k.lower() in raw for k in t.get("keywords", []))
    hits += sum(bool(re.search(p, raw, re.I)) for p in t.get("patterns", []))
    art_types = {a["type"] for a in intent["artifacts"]["provided"]}
    prereqs = set(skill.get("prerequisites", {}).get("skills", []))
    semantic = intent.get("semantic", {}).get(skill["skill_id"], 0.0)
    s = (WEIGHTS["domain"] * _overlap(intent["domains"], t["domains"])
         + WEIGHTS["action"] * (intent["action"] in t["actions"])
         + WEIGHTS["keyword"] * min(1.0, hits / 3)
         + WEIGHTS["semantic"] * (round(semantic * 20) / 20)
         + WEIGHTS["artifact"] * bool(art_types & set(t.get("artifact_types", [])))
         - WEIGHTS["negative"] * any(n.lower() in raw for n in t.get("negative", []))
         - WEIGHTS["prereq"] * len(prereqs - set(chosen_ids)))
    return round(max(0.0, min(1.0, s)), 3)


def select(skills, intent):
    """Return (chosen skills in rank order, {skill_id: score}).

    Empty `chosen` means no skill applies and the caller should answer without
    the engine. Never falls back to a low-scoring skill: a wrong specialist
    frame is worse than a plain answer.
    """
    by_id = {s["skill_id"]: s for s in skills}
    scores = {sid: score(s, intent) for sid, s in by_id.items()}
    chosen = {sid for sid, v in scores.items() if v >= AUTO}
    cand = {sid for sid, v in scores.items() if CANDIDATE <= v < AUTO}
    # Pull in what an auto-invoked skill declares it needs or chains behind.
    for sid in list(chosen):
        s = by_id[sid]
        chosen |= set(s.get("prerequisites", {}).get("skills", [])) & (cand | chosen)
        chosen |= set(s["compat"].get("chain_after", [])) & cand
    # Re-score now that prerequisites may be satisfied (removes the penalty).
    scores.update({sid: score(by_id[sid], intent, chosen) for sid in chosen})
    for a in sorted(chosen):
        for b in by_id[a]["compat"].get("exclusive_with", []):
            if b in chosen and a in chosen:
                chosen.discard(min(a, b, key=lambda k: (scores[k], by_id[k].get("priority", 50))))
    ranked = sorted(chosen, key=lambda k: (-scores[k], -by_id[k].get("priority", 50),
                                           len(by_id[k]["triggers"]["domains"]), k))
    return [by_id[k] for k in ranked[:MAX_SKILLS]], scores
