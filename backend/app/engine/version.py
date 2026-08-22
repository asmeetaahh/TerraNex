"""Engine version.

Stamped onto every persisted analysis run alongside the ruleset hash, so a stored
result can always be traced to the code that produced it. Two runs with the same
`inputs_hash` but different `engine_version` are not comparable, and a cache lookup
must treat them as distinct.

Bump this whenever a calculation changes in a way that would move a score. Adding a
new field or improving an explanation string does not require a bump; changing a
threshold, a weight, or a formula does.
"""

ENGINE_VERSION = "4.0.0"
"""Semantic version of the deterministic engine.

* **major** — a score for unchanged inputs can move.
* **minor** — a new risk, factor or field is emitted; existing scores are unchanged.
* **patch** — wording, typing or performance only.
"""
