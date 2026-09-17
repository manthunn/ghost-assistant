"""Dispatch engine: route a raw request to registered composite skills.

A "dispatch skill" here is not one of Ghost's atomic `@register` tools. It is
a persona, a set of constraints, an ordered procedure and an output contract,
stored in `skills.json`, that turns a vague request ("fix this script and make
it clean") into a fully specified prompt and runs it.

Pipeline, one request:

    raw request + artifacts
      -> intent.classify   deterministic rule pass, then one structured
                           Gemini call for the rest plus per-skill semantic
                           scores (rule pass alone is the fallback)
      -> matcher.select    weighted score per skill, thresholds, prerequisites
      -> plan.prehook      bind every input from artifacts, upstream, answers
                           or defaults; unresolved required inputs become
                           clarification questions
      -> plan.build_layers topological layers, terminal synthesize node
      -> engine.execute    one frame per node on the skill's backend
      -> engine.synthesize merge multi-node output, split off the SUMMARY line
      -> file on disk + short spoken summary back to the caller

Entry points are `engine.run()` for a new request and `engine.resume()` for
one that previously asked a clarifying question. `skills/dispatch_engine.py`
exposes both to the Live model as the `dispatch_task` tool.
"""
