"""The deterministic risk engine.

**Nothing in this package performs I/O.** No HTTP, no database, no clock, no random
number generator, no environment lookup. Every input arrives as an
:class:`~app.engine.context.AnalysisContext` and every output is a pure function of it.

That constraint is what buys three properties the product depends on:

* **Reproducibility** — the same context produces byte-identical results, so a stored
  run can be recomputed and checked years later.
* **Testability** — every calculation is unit-testable without a fixture server, a
  database, or a mocked clock.
* **Auditability** — an agronomist can read one function and see the whole derivation,
  which is what an advisory service has to be able to show.

The rule is enforced mechanically by `tests/unit/engine/test_engine_is_pure.py`, which
walks this package's imports and fails on any provider, database or transport module.
Importing from `app.schemas` is permitted and intended: the schemas layer is pure
Pydantic that imports nothing but itself, and the engine emits its types directly.

Adapters that build a context from live provider data live in the service layer, not
here — that is the boundary that keeps this package honest.
"""
