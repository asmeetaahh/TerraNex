# TerraNex — Architecture

> Status: the contract is complete and published. Most behaviour is still to be
> implemented. Section 9 tracks exactly what is live today.

---

## 1. The rule that shapes everything

```
Frontend  →  TerraNex API  →  external providers  →  deterministic risk engine
                                                  →  Gemini reasoning
                                                  →  validated structured response  →  Frontend
```

**The backend is the only intelligence and integration layer.** The frontend never
calls Gemini, a weather API, a soil API, a satellite source, or any other provider.
It speaks REST to `/api/v1` and nothing else.

This is not a stylistic preference. It buys:

* **One place for secrets.** No provider key is ever shipped to a browser.
* **One place for caching.** Ten users looking at the same region hit one upstream call.
* **One place for degradation.** When a provider fails, the fallback is decided once.
* **A swappable data layer.** Adding a national soil database is a new backend file,
  not a frontend release.

---

## 2. The load-bearing decision: deterministic core, AI as interpreter

**The AI never invents numbers.**

A pure-Python risk engine computes every score, index, threshold, and count from
provider data. Gemini then receives those computed facts and produces *narrative,
prioritization, and reasoning* — validated against a Pydantic schema before it is
persisted or returned.

Concretely, in `WaterRisk`:

| Field | Produced by |
|---|---|
| `water_balance_mm`, `deficit_mm`, `days_until_stress`, `recommended_irrigation_mm` | risk engine (pure Python) |
| `explanation`, `drivers` phrasing | AI, interpreting the above |

This buys four things at once:

1. **Reproducibility** — the same inputs give the same scores, every demo, every time.
2. **Testability** — `pytest` has real arithmetic to assert against.
3. **No hallucinated agronomy** — a model cannot invent a soil pH or a rainfall total.
4. **Graceful failure** — when the AI is unavailable, deterministic template text is
   substituted and the run still returns, marked `ai_mode: "fallback"`.

---

## 3. Honesty is enforced by the schema

Two enums exist so that a consumer can always tell real from generated.

**`DataMode`** — on every externally-sourced payload, inside `meta`:

| Value | Meaning |
|---|---|
| `live` | fetched from a real provider during this request |
| `cached` | real provider data, served from cache within its TTL |
| `simulated` | **generated locally — not a real observation.** The UI must label it |
| `unavailable` | the provider failed; values are estimates or null, see `meta.note` |

**`AIMode`** — on every AI-touched payload:

| Value | Meaning |
|---|---|
| `gemini` | a real model produced this narrative |
| `mock` | deterministic fixture text (no key, no cost, no network) |
| `fallback` | the AI failed; deterministic template text was substituted |

Because these are required schema fields rather than a convention, a payload
physically cannot present generated values as real observations. Until real providers
are wired in, every environmental response reports `simulated`.

---

## 4. Layers

```
app/api/v1/routes/     HTTP only: path/query/body binding, status codes, OpenAPI docs
        ↓
app/services/          orchestration and business rules. No HTTP concepts, no raw SQL
        ↓
app/providers/         one adapter per external source; returns ProviderResult[T]
app/ai/                Gemini client, prompt registry, structured output, mock provider
app/models/ app/db/    SQLAlchemy ORM and session management
        ↓
app/schemas/           Pydantic — THE CONTRACT. Shared by routes, services, and AI output
```

Rules that keep the layers honest:

* A route never talks to a provider or the DB directly.
* A service never raises `HTTPException` — it raises an `AppError` subclass.
* A provider never raises into the analysis path; it returns a result envelope.
* `app/schemas/` imports from nothing but itself.

---

## 5. Request lifecycle

```
request
  → RequestContextMiddleware      assigns request_id, starts the timer
  → CORSMiddleware                explicit origin allowlist
  → route                         FastAPI validates path/query/body → 422 on failure
  → service                       orchestration
  → providers (concurrent)        asyncio.gather(..., return_exceptions=True)
  → risk engine                   deterministic scoring
  → AI                            structured output → validate → repair once → fallback
  → response_model                Pydantic validates on the way out too
  → X-Request-Id header + one structured log line
```

Any `AppError` raised anywhere becomes the single error envelope, with the same
`request_id` in both body and header — so a screenshot of a failed request is enough
to find its log line.

---

## 6. Analysis: one computation, many views

`POST /api/v1/farms/{farm_id}/analysis` is the **only** endpoint that performs farm
reasoning. It gathers data, scores it, makes one AI call, validates, and persists an
immutable `AnalysisRun`.

Every other analysis endpoint — the dashboard, the four risk panels, crop health,
advisories, both recommendation lists — is a **cheap projection of that stored run**.
No recompute, no provider calls, no additional AI cost.

The consequence is that a dashboard with a dozen panels costs exactly one analysis,
and a user clicking between tabs costs nothing. An `inputs_hash` on each run lets a
repeat request inside the cache window return the stored result instantly;
`force_refresh=true` bypasses it.

Runs are immutable and record `model`, `prompt_version`, and `ai_mode`, so any stored
output can be traced to exactly what produced it — the property an agricultural
advisory service needs to be reviewable.

---

## 7. Failure and degradation

Nothing in a live demo should be able to hard-fail.

| Failure | Behaviour |
|---|---|
| One provider times out | `meta.mode: "unavailable"`, listed in `degraded_sources`, run continues as `partial` |
| Soil provider down | texture-class defaults substituted, `meta.note` explains it |
| AI returns invalid JSON | one repair retry with the validation errors fed back |
| AI still invalid, or unreachable | deterministic template advisories, `ai_mode: "fallback"` |
| No analysis exists yet | `/dashboard` returns `has_analysis: false`, not a 404 |
| Gemini quota exhausted mid-demo | set `AI_PROVIDER=mock` — a live kill switch |

`502 PROVIDER_UNAVAILABLE` and `503 AI_UNAVAILABLE` are declared in the contract but
should only ever fire when *every* input fails at once.

---

## 8. Cross-cutting

**Configuration** — `pydantic-settings` in `app/core/config.py`, loaded once and
validated at import. Every key is documented in `backend/.env.example`. Real secrets
live only in `.env` (gitignored) and the deployment host's dashboard.

**Authentication** — deliberately deferred. `ENABLE_AUTH=false` attributes every
request to a seeded demo user, so the frontend can build every screen before login
exists. When it is enabled, the frontend obtains a Supabase JWT and sends
`Authorization: Bearer …`; the backend verifies it and resolves ownership. **No
request or response shape changes when auth is turned on** — only the identity behind
`user_id`.

**CORS** — an explicit origin allowlist from `CORS_ORIGINS`, never `*` alongside
credentials. `X-Request-Id` is in `expose_headers` so the browser can read it.

**Errors** — a 17-member `ErrorCode` enum and an `AppError` hierarchy in
`app/core/errors.py`. FastAPI's default 422 body is overridden so the frontend needs
exactly one error parser. `code` is the stable API; `message` is for humans.

**Testing** — `pytest` with `httpx.ASGITransport` (no sockets). Tests never touch the
network: providers will be replayed from recorded fixtures via `respx`, and the AI
layer is forced to `mock`. The suite is tiered: unit (risk-engine arithmetic —
ultimately the highest-value tests), provider parsing, service orchestration, and API
contract tests.

`test_openapi_contract_matches_committed` compares the running app's schema against
the committed `contracts/openapi.json` and fails if they differ. That single test is
what makes the single-branch workflow safe — the contract cannot silently drift.

---

## 9. Implementation status

| Component | Status |
|---|---|
| App factory, CORS, request-id, structured logging | **done** |
| Error taxonomy and envelope | **done** |
| Pydantic schemas — 69 published in the contract | **done** |
| All 26 MVP paths declared with final request/response models | **done** |
| `GET /health`, `/health/ready`, `/reference/enums` | **done** |
| Remaining 30 operations | scaffolded, return `501 NOT_IMPLEMENTED` |
| Seeded fixture responses | next |
| Database, models, migrations | not started |
| External providers | not started |
| Deterministic risk engine | not started |
| Gemini reasoning and vision | not started |

Each scaffolded route names its intended step in `error.details.planned_step`.

---

## 10. Where this goes next

* **Stateless API** — horizontally scalable; move the TTL cache to Redis and scale out.
* **Swappable providers** — a country plugging in its own authoritative soil database
  writes one adapter. The service layer does not change. This is the digital-public-good
  path.
* **Auditable AI** — model, prompt version and input hash on every run make advisories
  reproducible and reviewable.
* **Explainable by construction** — `ScoredFactor` breakdowns mean no score is opaque.
* **Open contract** — a published OpenAPI document lets NGOs, cooperatives, and
  SMS/IVR gateways build clients without touching TerraNex.
* **Growth path** — background workers for long analyses, PostGIS for field polygons,
  real Sentinel-2 NDVI behind the existing `VegetationSeries` contract, i18n on
  advisory strings for smallholder reach.
