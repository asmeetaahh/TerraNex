"""Versioned agronomy knowledge, kept as data rather than code.

Two things live here: FAO-56 crop coefficients, and (from Phase 4 step 5) the disease
ruleset. Both are parameters the frozen `contracts/openapi.json` has nowhere to carry —
adding them to the published `Crop` schema would change the contract — and both are the
kind of knowledge an agronomist should be able to review and amend without reading
Python.

Every file in this package is hashed into `RULESET_VERSION`, which is stamped onto each
analysis run. A threshold cannot be edited without the version changing, so a stored
result always names the exact ruleset that produced it.
"""
