"""Offline tests for the dispatch engine. Run with: py -m unittest discover -s tests -v.

No model is called: the classifier's model pass and the node backends are
replaced with deterministic fakes, so every routing decision here is exact.
"""
import copy
import json
import os
import pathlib
import tempfile
import threading
import unittest
from unittest.mock import patch

from ghost.dispatch import engine, intent, matcher, plan, registry

SCRIPT = """import os, subprocess, sys

def run(cmd):
    try:
        out = subprocess.check_output(cmd, shell=True)
        return out
    except:
        return None

def cleanup(dir):
    files = os.listdir(dir)
    for f in files:
        if f.endswith(".tmp"):
            run("rm " + dir + "/" + f)
    print("done")

if __name__ == "__main__":
    cleanup(sys.argv[1])
"""
CODE = [{"type": "code", "name": "cleanup.py", "content": SCRIPT}]
CSV = [{"type": "table", "name": "survey.csv", "content": "group,score\nA,4\nB,7\n"}]
REG = registry.load(log=lambda *a: None)
BY_ID = {s["skill_id"]: s for s in REG}


def semantic_model(scores):
    """A fake structured-output call returning fixed semantic scores."""
    def model(prompt, schema):
        return {"domains": [], "action": "generate", "constraints_explicit": [],
                "constraints_implicit": ["preserve behavior"], "target_output": "x",
                "ambiguity_score": 0.2, "ambiguity_gaps": [],
                "semantic": [{"skill_id": k, "score": v} for k, v in scores.items()]}
    return model


def fake_backend(reply="## Out\nbody text\n\nSUMMARY: it worked.", fail_times=0, calls=None):
    """Text backend that fails `fail_times` times per skill before succeeding."""
    failures = {}

    def backend(system, prompt, cwd):
        key = prompt.split("\n", 1)[0]
        if calls is not None:
            calls.append((threading.get_ident(), key, prompt))
        if failures.get(key, 0) < fail_times:
            failures[key] = failures.get(key, 0) + 1
            raise RuntimeError("backend down")
        return reply
    return backend


class RegistryTests(unittest.TestCase):
    def test_shipped_registry_loads_three_skills(self):
        self.assertEqual(sorted(BY_ID), ["code.refactor_audit", "data.visualizer",
                                         "research.academic_synth"])

    def test_invalid_entries_are_dropped_not_fatal(self):
        bad_domain = copy.deepcopy(BY_ID["data.visualizer"])
        bad_domain["skill_id"] = "data.bad"
        bad_domain["triggers"]["domains"] = ["not.a.domain"]
        no_prompt = copy.deepcopy(BY_ID["data.visualizer"])
        no_prompt["skill_id"] = "data.noprompt"
        del no_prompt["inputs"][0]["clarification_prompt"]
        dup = copy.deepcopy(BY_ID["data.visualizer"])
        logged = []
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / "skills.json"
            path.write_text(json.dumps(REG + [bad_domain, no_prompt, dup]), encoding="utf-8")
            loaded = registry.load(path, log=logged.append)
        self.assertEqual(len(loaded), 3)
        self.assertEqual(len(logged), 3)
        self.assertTrue(any("not.a.domain" in m or "unknown domain" in m for m in logged))
        self.assertTrue(any("clarification_prompt" in m for m in logged))
        self.assertTrue(any("duplicate" in m for m in logged))

    def test_dangling_reference_fails_loudly(self):
        skill = copy.deepcopy(BY_ID["data.visualizer"])
        skill["compat"]["chain_before"] = ["research.missing"]
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / "skills.json"
            path.write_text(json.dumps([skill]), encoding="utf-8")
            with self.assertRaisesRegex(registry.RegistryError, "research.missing"):
                registry.load(path, log=lambda *a: None)


class IntentTests(unittest.TestCase):
    def test_rules_classify_the_code_sample(self):
        it = intent.classify("fix this script and make it clean", CODE, REG, model=None)
        self.assertEqual(it["domains"], ["software.code"])
        self.assertEqual(it["action"], "transform")
        self.assertEqual(it["artifacts"]["provided"][0]["lang"], "python")
        self.assertEqual(it["artifacts"]["provided"][0]["loc"], 18)
        self.assertEqual(it["semantic"], {})
        self.assertEqual(it["complexity"]["level"], 1)

    def test_code_words_without_code_context_do_not_route_to_software(self):
        it = intent.classify("fix my essay and make it clean", [], REG, model=None)
        self.assertNotIn("software.code", it["domains"])
        self.assertEqual(it["action"], "transform")

    def test_model_pass_merges_and_filters(self):
        def model(prompt, schema):
            self.assertIn("code.refactor_audit", prompt)
            return {"domains": ["software.code", "writing.technical", "ops.planning"],
                    "action": "explain", "constraints_explicit": ["keep it short"],
                    "constraints_implicit": [], "target_output": "code",
                    "ambiguity_score": 1.7, "ambiguity_gaps": ["tests?"],
                    "semantic": [{"skill_id": "code.refactor_audit", "score": 0.9},
                                 {"skill_id": "not.registered", "score": 1.0}]}
        it = intent.classify("fix this script", CODE, REG, model=model)
        self.assertEqual(it["domains"], ["software.code", "writing.technical"])  # capped at 2
        self.assertEqual(it["action"], "transform")  # rules win when they fired
        self.assertEqual(it["semantic"], {"code.refactor_audit": 0.9})
        self.assertEqual(it["ambiguity"]["score"], 1.0)  # clamped
        self.assertEqual(it["constraints"]["explicit"], ["keep it short"])
        self.assertEqual(it["confidence"], 0.9)

    def test_model_failure_falls_back_to_rules(self):
        def broken(prompt, schema):
            raise ConnectionError("offline")
        logged = []
        it = intent.classify("fix this script", CODE, REG, model=broken, log=logged.append)
        self.assertEqual(it["domains"], ["software.code"])
        self.assertEqual(it["confidence"], 0.5)
        self.assertTrue(logged and "model pass failed" in logged[0])


class MatcherTests(unittest.TestCase):
    def test_code_sample_scores_reproduce_the_walkthrough(self):
        it = intent.classify("fix this script and make it clean", CODE, REG, model=None)
        self.assertAlmostEqual(matcher.score(BY_ID["code.refactor_audit"], it), 0.75)
        self.assertEqual(matcher.score(BY_ID["research.academic_synth"], it), 0.0)
        self.assertEqual(matcher.score(BY_ID["data.visualizer"], it), 0.0)
        it["semantic"] = {"code.refactor_audit": 0.95}
        self.assertAlmostEqual(matcher.score(BY_ID["code.refactor_audit"], it), 0.9875, places=2)
        chosen, _ = matcher.select(REG, it)
        self.assertEqual([s["skill_id"] for s in chosen], ["code.refactor_audit"])

    def test_negative_trigger_vetoes(self):
        it = intent.classify("just explain this script, don't change it", CODE, REG, model=None)
        it["semantic"] = {"code.refactor_audit": 0.9}
        self.assertLess(matcher.score(BY_ID["code.refactor_audit"], it), matcher.CANDIDATE)
        self.assertEqual(matcher.select(REG, it)[0], [])

    def test_candidate_is_pulled_in_by_chain_after(self):
        it = intent.classify("here's survey.csv, write it up for my findings section",
                             CSV, REG, model=None)
        it["semantic"] = {"research.academic_synth": 0.9, "data.visualizer": 0.8}
        chosen, scores = matcher.select(REG, it)
        self.assertGreaterEqual(scores["research.academic_synth"], matcher.AUTO)
        self.assertTrue(matcher.CANDIDATE <= scores["data.visualizer"] < matcher.AUTO)
        self.assertEqual([s["skill_id"] for s in chosen],
                         ["research.academic_synth", "data.visualizer"])

    def test_candidate_alone_is_not_invoked(self):
        it = intent.classify("here's survey.csv, write it up", CSV, REG, model=None)
        it["semantic"] = {"data.visualizer": 0.8}
        chosen, scores = matcher.select(REG, it)
        self.assertLess(scores["research.academic_synth"], matcher.AUTO)
        self.assertEqual(chosen, [])

    def test_exclusive_skills_keep_the_higher_score(self):
        a, b = copy.deepcopy(BY_ID["code.refactor_audit"]), copy.deepcopy(BY_ID["code.refactor_audit"])
        b["skill_id"], b["priority"] = "code.other", 10
        a["compat"]["exclusive_with"] = ["code.other"]
        it = intent.classify("fix this script and make it clean", CODE, REG, model=None)
        it["semantic"] = {"code.refactor_audit": 0.9, "code.other": 0.9}
        chosen, _ = matcher.select([a, b], it)
        self.assertEqual([s["skill_id"] for s in chosen], ["code.refactor_audit"])


class PlanTests(unittest.TestCase):
    def test_prehook_binds_from_artifact_and_defaults(self):
        it = intent.classify("fix this script", CODE, REG, model=None)
        b, assumed, gaps = plan.prehook(BY_ID["code.refactor_audit"], it)
        self.assertEqual(b["source_code"], SCRIPT)
        self.assertEqual(b["language"], "python")
        self.assertIs(b["preserve_behavior"], True)
        self.assertIsNone(b["test_suite"])
        self.assertEqual(gaps, [])
        self.assertEqual(len(assumed), 2)
        self.assertTrue(assumed[0].startswith("preserve_behavior=True"))

    def test_prehook_reports_gaps_and_context_fills_them(self):
        it = intent.classify("fix this script", [], REG, model=None)
        _, _, gaps = plan.prehook(BY_ID["code.refactor_audit"], it)
        self.assertEqual([g["name"] for g in gaps], ["source_code"])
        b, _, gaps = plan.prehook(BY_ID["code.refactor_audit"], it, answers={"source_code": "x = 1"})
        self.assertEqual((b["source_code"], gaps), ("x = 1", []))
        self.assertEqual(b["language"], "auto-detect")

    def test_text_input_accepts_a_table_artifact_but_table_input_needs_a_table(self):
        it = intent.classify("write it up", CSV, REG, model=None)
        b, _, gaps = plan.prehook(BY_ID["research.academic_synth"], it)
        self.assertEqual((b["sources"], gaps), (CSV[0]["content"], []))
        it = intent.classify("chart this", CODE, REG, model=None)
        _, _, gaps = plan.prehook(BY_ID["data.visualizer"], it)
        self.assertEqual([g["name"] for g in gaps], ["data"])

    def test_questions_are_capped_and_in_rank_order(self):
        gaps = [("a.b", [{"name": f"i{n}", "clarification_prompt": f"q{n}"} for n in range(3)]),
                ("c.d", [{"name": "x", "clarification_prompt": "qx"}])]
        qs = plan.questions_for(gaps)
        self.assertEqual([q[1] for q in qs], ["i0", "i1", "i2"])

    def test_layers_follow_chain_and_upstream_inputs(self):
        layers, dropped = plan.build_layers([BY_ID["research.academic_synth"], BY_ID["data.visualizer"]])
        self.assertEqual(layers, [["data.visualizer"], ["research.academic_synth"], [plan.SYNTH]])
        self.assertEqual(dropped, [])

    def test_cycle_drops_lowest_ranked_skill(self):
        a, b = copy.deepcopy(BY_ID["research.academic_synth"]), copy.deepcopy(BY_ID["data.visualizer"])
        b["compat"]["chain_after"] = ["research.academic_synth"]  # now both chain after each other
        layers, dropped = plan.build_layers([a, b], log=lambda *a: None)
        self.assertEqual(dropped, ["data.visualizer"])
        self.assertEqual(layers, [["research.academic_synth"], [plan.SYNTH]])

    def test_independent_skills_share_a_layer(self):
        a, b = copy.deepcopy(BY_ID["research.academic_synth"]), copy.deepcopy(BY_ID["data.visualizer"])
        a["compat"]["chain_after"], b["compat"]["chain_before"] = [], []
        a["inputs"] = [i for i in a["inputs"] if "upstream" not in i["source"]]
        layers, _ = plan.build_layers([a, b])
        self.assertEqual(layers, [["data.visualizer", "research.academic_synth"], [plan.SYNTH]])


class EngineTests(unittest.TestCase):
    def setUp(self):
        engine._state.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.out = pathlib.Path(self.tmp.name)
        patcher = patch.object(engine, "OUT_DIR", self.out)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def run_engine(self, request, artifacts, semantic, backend=None, answers=None):
        return engine.run(request, artifacts, answers, json_model=semantic_model(semantic),
                          text_backends={"gemini": backend or fake_backend(),
                                         "claude": backend or fake_backend()},
                          log=lambda *a: None)

    def test_code_sample_end_to_end(self):
        calls = []
        r = self.run_engine("fix this script and make it clean", CODE,
                            {"code.refactor_audit": 0.95}, fake_backend(calls=calls))
        self.assertEqual(r.status, "done")
        self.assertEqual(r.skills, ["code.refactor_audit"])
        self.assertEqual(r.summary, "it worked.")
        self.assertEqual(r.notes, [])
        self.assertTrue(any(a.startswith("code.refactor_audit: preserve_behavior") for a in r.assumptions))
        text = pathlib.Path(r.output_path).read_text(encoding="utf-8")
        self.assertIn("## Out\nbody text", text)
        self.assertNotIn("SUMMARY:", text.split("---")[0])
        self.assertIn("Trace:", text)
        self.assertEqual(engine._state, {})
        # The frame carries the whole file, the language and the procedure.
        frame = calls[0][2]
        self.assertIn("[SKILL FRAME: code.refactor_audit v1.2]", frame)
        self.assertIn(SCRIPT.strip(), frame)
        self.assertIn("language: python", frame)
        self.assertIn("(implied by the request) preserve behavior", frame)
        self.assertIn("1. Read the full source", frame)
        self.assertIn("SUMMARY:", frame)

    def test_no_match_when_nothing_scores(self):
        r = self.run_engine("what's the weather", [], {})
        self.assertEqual(r.status, "no_match")
        self.assertTrue(any("no skill above threshold" in t for t in r.trace))

    def test_missing_input_asks_then_resumes(self):
        r = self.run_engine("fix this script and make it clean", [],
                            {"code.refactor_audit": 0.95})
        self.assertEqual(r.status, "clarify")
        self.assertEqual([q[1] for q in r.questions], ["source_code"])
        self.assertIn("Which file", r.questions[0][2])
        self.assertIn(r.request_id, engine._state)
        done = engine.resume(r.request_id, artifacts=CODE)
        self.assertEqual(done.status, "done")
        self.assertTrue(any(t.startswith("resume +1 artifacts") for t in done.trace))
        self.assertNotIn(r.request_id, engine._state)

    def test_resume_unknown_id_fails_cleanly(self):
        r = engine.resume("d-nope")
        self.assertEqual(r.status, "failed")
        self.assertIn("Unknown or expired", r.error)

    def test_expired_state_is_purged(self):
        r = self.run_engine("fix this script", [], {"code.refactor_audit": 0.95})
        engine._state[r.request_id]["touched"] -= engine.STATE_TTL + 1
        self.assertEqual(engine.resume(r.request_id, artifacts=CODE).status, "failed")

    def test_lead_skill_failing_twice_fails_the_request(self):
        r = self.run_engine("fix this script and make it clean", CODE,
                            {"code.refactor_audit": 0.95}, fake_backend(fail_times=2))
        self.assertEqual(r.status, "failed")
        self.assertIn("failed twice", r.error)
        self.assertEqual(sum("attempt" in t for t in r.trace), 2)
        self.assertEqual(list(self.out.iterdir()), [])

    def test_claude_failure_falls_back_to_gemini(self):
        used = []

        def claude(system, prompt, cwd):
            used.append("claude")
            raise RuntimeError("Claude Code failed (exit 1): not logged in")

        def gemini(system, prompt, cwd):
            used.append("gemini")
            return "## Out\nx\n\nSUMMARY: gemini did it."
        r = engine.run("fix this script and make it clean", CODE,
                       json_model=semantic_model({"code.refactor_audit": 0.95}),
                       text_backends={"gemini": gemini, "claude": claude}, log=lambda *a: None)
        self.assertEqual((r.status, r.summary), ("done", "gemini did it."))
        self.assertEqual(used, ["claude", "gemini"])
        self.assertTrue(any("attempt 2 ok" in t and "backend=gemini" in t for t in r.trace))

    def test_one_failure_is_retried(self):
        r = self.run_engine("fix this script and make it clean", CODE,
                            {"code.refactor_audit": 0.95}, fake_backend(fail_times=1))
        self.assertEqual(r.status, "done")
        self.assertTrue(any("attempt 2 ok" in t for t in r.trace))

    def test_chain_feeds_upstream_and_merges(self):
        calls = []
        r = self.run_engine("here's survey.csv, write it up for my findings section", CSV,
                            {"research.academic_synth": 0.9, "data.visualizer": 0.8},
                            fake_backend(calls=calls))
        self.assertEqual(r.status, "done")
        frames = [c[1] for c in calls]
        self.assertEqual(frames[0], "[SKILL FRAME: data.visualizer v1.1]")
        self.assertEqual(frames[1], "[SKILL FRAME: research.academic_synth v1.0]")
        synth_frame = calls[1][2]
        self.assertIn("figures:\n## Out\nbody text", synth_frame)          # pass_full body
        self.assertIn("stats_summary: it worked.", synth_frame)            # summary only
        self.assertIn("=== OUTPUT OF data.visualizer ===", calls[2][2])   # merge prompt
        self.assertIn("=== OUTPUT OF research.academic_synth ===", calls[2][2])
        self.assertTrue(any("synthesize merged 2 outputs" in t for t in r.trace))

    def test_partner_without_inputs_is_dropped_not_asked(self):
        notes = [{"type": "text", "name": "notes.md", "content": "Finding one. Finding two."}]
        r = self.run_engine("write it up for my findings section and include the trend",
                            notes, {"research.academic_synth": 0.9, "data.visualizer": 0.8})
        self.assertTrue(any(t.startswith("chosen research.academic_synth, data.visualizer")
                            for t in r.trace))
        self.assertEqual(r.status, "done")
        self.assertEqual(r.skills, ["research.academic_synth"])
        self.assertTrue(any(t.startswith("drop data.visualizer: partner missing data") for t in r.trace))

    def test_non_lead_failure_is_skipped_and_noted(self):
        def backend(system, prompt, cwd):
            if prompt.startswith("[SKILL FRAME: data.visualizer"):
                raise RuntimeError("no charts today")
            return "## Section\ntext\n\nSUMMARY: wrote it."
        r = self.run_engine("here's survey.csv, write it up for my findings section", CSV,
                            {"research.academic_synth": 0.9, "data.visualizer": 0.8}, backend)
        self.assertEqual(r.status, "done")
        self.assertEqual(r.skills, ["research.academic_synth"])
        self.assertEqual(r.notes, ["skipped after two failures: data.visualizer"])
        self.assertTrue(any(t.startswith("synthesize passthrough") for t in r.trace))

    def test_parallel_layer_runs_nodes_concurrently(self):
        a, b = copy.deepcopy(BY_ID["research.academic_synth"]), copy.deepcopy(BY_ID["data.visualizer"])
        a["compat"]["chain_after"], b["compat"]["chain_before"] = [], []
        a["inputs"] = [i for i in a["inputs"] if "upstream" not in i["source"]]
        b["triggers"]["domains"] = ["research.academic", "data.analysis"]  # both auto-invoke
        b["triggers"]["actions"] = ["generate"]
        calls, started = [], threading.Barrier(2, timeout=3)

        def backend(system, prompt, cwd):
            if prompt.startswith("[SKILL FRAME"):  # the merge call runs alone afterwards
                calls.append(threading.get_ident())
                started.wait()  # both nodes must be in flight at once for this to pass
            return "x\n\nSUMMARY: ok."
        with patch.object(engine, "_skills", [a, b]):
            r = self.run_engine("here's survey.csv, write it up for my findings section", CSV,
                                {"research.academic_synth": 1.0, "data.visualizer": 1.0}, backend)
        self.assertEqual((r.status, r.error), ("done", ""))
        self.assertEqual(len(set(calls)), 2)

    def test_summary_split_variants(self):
        self.assertEqual(engine._split_summary("a\n**SUMMARY:** bold one\n"), ("a", "bold one"))
        self.assertEqual(engine._split_summary("Summary: not it\nbody\nSUMMARY: last wins"),
                         ("Summary: not it\nbody", "last wins"))
        body, summary = engine._split_summary("no marker here at all " * 30)
        self.assertEqual(len(summary), 300)
        self.assertTrue(body.startswith("no marker"))


class ToolTests(unittest.TestCase):
    def setUp(self):
        engine._state.clear()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = patch.object(engine, "OUT_DIR", pathlib.Path(tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_tool_is_registered_without_a_google_key(self):
        from ghost import skills
        with patch.dict(os.environ, {"GOOGLE_API_KEY": "offline-test-key"}):
            skills.load_all()
        self.assertIn("dispatch_task", skills.FUNCTIONS)
        self.assertIn("list_dispatch_skills", skills.FUNCTIONS)
        names = [t["function"]["name"] for t in skills.TOOLS]
        self.assertEqual(len(names), len(set(names)), "tool name collision")

    def test_tool_renders_each_outcome(self):
        from ghost.skills import dispatch_engine as tool
        run = patch.object(engine.backends, "gemini_json", semantic_model({"code.refactor_audit": 0.95}))
        text = patch.dict(engine.backends.TEXT, {"gemini": fake_backend(), "claude": fake_backend()})
        with run, text, patch("builtins.print"):
            asked = tool.dispatch_task("fix this script and make it clean")
            self.assertTrue(asked.startswith("CLARIFICATION NEEDED (request_id=d-"), asked)
            self.assertIn("(source_code)", asked)
            rid = asked.split("request_id=")[1].split(")")[0]
            done = tool.dispatch_task("fix this script and make it clean", artifacts=CODE,
                                      request_id=rid)
            self.assertTrue(done.startswith("Done via code.refactor_audit. it worked. Full result saved to"))
            self.assertIn("do not read the file aloud", done)
            # An expired id starts over instead of failing the user.
            again = tool.dispatch_task("fix this script and make it clean", artifacts=CODE,
                                       request_id="d-gone")
            self.assertTrue(again.startswith("Done via"))
            self.assertIn("No specialist skill", tool.dispatch_task("what's the weather"))
            listing = tool.list_dispatch_skills()
            self.assertEqual(listing.count("\n- ") + 1, 3)

    def test_tool_ignores_malformed_artifacts_and_answers(self):
        from ghost.skills import dispatch_engine as tool
        self.assertEqual(tool._clean_artifacts(["junk", {"type": "code"}, {"type": "zzz", "content": "x"}]),
                         [{"type": "text", "name": "", "content": "x"}])
        self.assertEqual(tool._clean_answers([{"input": "a", "value": "1"}, {"input": "b"}, "x"]),
                         {"a": "1"})


if __name__ == "__main__":
    unittest.main()
