# TerraNex API Contract — v1

The agreement between the backend and the frontend. The machine-readable form is
[`contracts/openapi.json`](../contracts/openapi.json); this document explains it.

**Base URL (local):** `http://localhost:8000/api/v1`
**Interactive playground:** <http://localhost:8000/docs>

> **Status.** All 26 paths / 33 operations below are **published and final**. Three are
> fully implemented; the rest return `501 NOT_IMPLEMENTED` while their behaviour is
> built. Request and response shapes will not change — you can build against them now.
> See [Implementation status](#implementation-status).

---

## Contents

1. [Generate your types](#1-generate-your-types)
2. [Conventions](#2-conventions)
3. [Errors](#3-errors)
4. [Data honesty: `mode` and `ai_mode`](#4-data-honesty-mode-and-ai_mode)
5. [Endpoints](#5-endpoints)
6. [Core schemas](#6-core-schemas)
7. [Authentication](#7-authentication)
8. [Implementation status](#implementation-status)
9. [Change policy](#9-change-policy)

---

## 1. Generate your types

Do not hand-write request or response interfaces. Generate them:

```bash
cd frontend
npx openapi-typescript ../contracts/openapi.json -o src/api/types.gen.ts
```

Re-run this whenever the backend says *"contract updated"*. If `npx tsc --noEmit` then
fails, that is your early warning of a breaking change — say so before working around it.

---

## 2. Conventions

| Aspect | Rule |
|---|---|
| Versioning | in the path — `/api/v1`. Nothing is served outside it |
| Casing | `snake_case` everywhere, request and response. No transform layer |
| IDs | UUID v4 as strings |
| Timestamps | ISO-8601 UTC, `Z` suffix |
| Dates | ISO-8601 `YYYY-MM-DD` |
| Success body | the bare resource — no `{data: …}` wrapper |
| Collections | always `{items, total, page, page_size, has_next}` |
| Enums | lowercase snake strings — fetch labels from `/reference/enums` |
| Nullability | documented nullable fields are always present, possibly `null` |
| Correlation | every response carries `X-Request-Id` |

**Collections use one shape**, even when an endpoint has no real pagination — it then
returns `page: 1, has_next: false`. One shape to learn, one component to write.

---

## 3. Errors

**Every** failure — 4xx and 5xx alike, including validation — returns exactly this:

```json
{
  "error": {
    "code": "FARM_NOT_FOUND",
    "message": "Farm 9f8e… does not exist or is not accessible.",
    "details": {"farm_id": "9f8e…"},
    "request_id": "req_01hzy8k3m2n4p5q6"
  }
}
```

> **Branch on `code`, never on `message`.** `code` is the stable API. `message` is
> written for humans and may be reworded at any time without notice.

For `422`, `details` carries the offending fields:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Request validation failed.",
    "details": {
      "fields": [
        {"field": "latitude", "message": "Input should be less than or equal to 90", "type": "less_than_equal"}
      ]
    },
    "request_id": "req_…"
  }
}
```

### Error codes

| Code | HTTP | When |
|---|---|---|
| `VALIDATION_ERROR` | 422 | body, query or path parameter failed validation |
| `UNAUTHORIZED` | 401 | missing/invalid/expired token (only once auth is enabled) |
| `FORBIDDEN` | 403 | the resource belongs to another user |
| `RESOURCE_NOT_FOUND` | 404 | generic not-found, including unknown routes |
| `FARM_NOT_FOUND` | 404 | no such farm |
| `CROP_NOT_FOUND` | 404 | no such crop or planting |
| `IMAGE_NOT_FOUND` | 404 | no such crop image |
| `NO_ANALYSIS_YET` | 404 | the farm exists but has never been analysed |
| `ANALYSIS_IN_PROGRESS` | 409 | an analysis is already running for this farm |
| `CONFLICT` | 409 | conflicts with current resource state |
| `IMAGE_TOO_LARGE` | 413 | upload exceeds the size limit |
| `UNSUPPORTED_MEDIA_TYPE` | 415 | file type not allowed |
| `PROVIDER_UNAVAILABLE` | 502 | every external data source failed |
| `AI_UNAVAILABLE` | 503 | AI unreachable and no fallback possible |
| `AI_INVALID_OUTPUT` | 502 | AI output failed schema validation after a retry |
| `RATE_LIMITED` | 429 | too many requests; see `details.retry_after_s` |
| `INTERNAL_ERROR` | 500 | unexpected; report the `request_id` |
| `NOT_IMPLEMENTED` | 501 | route published, behaviour not built yet |

### Two codes worth designing around

**`NO_ANALYSIS_YET`** is normal, not exceptional — it is the state of every newly
registered farm. Render a "Run analysis" call to action, not an error toast.
`GET /dashboard` deliberately does **not** return it; use `has_analysis` there instead.

**`NOT_IMPLEMENTED`** carries `details.feature` and `details.planned_step`, so you can
show a "coming soon" placeholder and see which build step will light it up.

---

## 4. Data honesty: `mode` and `ai_mode`

Every externally-sourced payload carries a `meta` object:

```json
{
  "meta": {
    "source": "simulated",
    "mode": "simulated",
    "fetched_at": "2026-08-19T09:14:22Z",
    "note": "Generated locally; not a real observation."
  }
}
```

| `mode` | Meaning | Suggested UI |
|---|---|---|
| `live` | fetched from a real provider just now | normal |
| `cached` | real provider data, from cache | normal, optionally show `fetched_at` |
| `simulated` | **generated locally — not real** | **visible "Simulated data" badge** |
| `unavailable` | provider failed; estimates or nulls | "Estimated" badge, show `note` |

Similarly `ai_mode` on analyses and image diagnoses is `gemini`, `mock`, or `fallback`
(deterministic text substituted after an AI failure).

**Please render these.** They are how the product stays truthful about what it knows,
and right now every environmental payload is `simulated`.

---

## 5. Endpoints

Legend — **Ext**: needs external provider data. **AI**: invokes Gemini.

### Health

| Method | Path | Response |
|---|---|---|
| GET | `/health` | `HealthResponse` |
| GET | `/health/ready` | `ReadinessResponse` — per-dependency status |

### Reference — dropdowns and location search

| Method | Path | Query | Response | Errors | Ext | AI |
|---|---|---|---|---|---|---|
| GET | `/reference/enums` | — | `EnumCatalogResponse` | — | no | no |
| GET | `/reference/crops` | `category`, `season`, `page`, `page_size` | `CropList` | 422 | no | no |
| GET | `/reference/locations` | `q` (≥2 chars), `limit` | `LocationList` | 422, 502 | yes | no |

`/reference/enums` is **live now**. Build every select input from it rather than
hardcoding values — new enum members then appear without a frontend change.

### Farms

| Method | Path | Request | Response | Errors |
|---|---|---|---|---|
| POST | `/farms` | `FarmCreate` | `201 Farm` | 422 |
| GET | `/farms` | `?page&page_size` | `FarmList` | 422 |
| GET | `/farms/{farm_id}` | — | `Farm` | 404 |
| PATCH | `/farms/{farm_id}` | `FarmUpdate` | `Farm` | 404, 422 |
| DELETE | `/farms/{farm_id}` | — | `204` | 404 |

`FarmCreate` requires only `name`, `latitude`, `longitude`. Coordinates drive every
downstream data lookup, so they are mandatory. `DELETE` is a soft delete; historical
analyses are retained.

### Farm crops (plantings)

| Method | Path | Request | Response | Errors |
|---|---|---|---|---|
| POST | `/farms/{farm_id}/crops` | `FarmCropCreate` | `201 FarmCrop` | 404, 422 |
| GET | `/farms/{farm_id}/crops` | — | `FarmCropList` | 404 |
| PATCH | `/farms/{farm_id}/crops/{farm_crop_id}` | `FarmCropUpdate` | `FarmCrop` | 404, 422 |
| DELETE | `/farms/{farm_id}/crops/{farm_crop_id}` | — | `204` | 404 |

`crop_id` references the `/reference/crops` catalog. `FarmCrop` embeds the full `crop`
object, so a card renders without a second request.
Setting `expected_harvest_date` before `planting_date` is a 422.

### Environmental data — raw series for charts

| Method | Path | Query | Response | Errors | Ext | AI |
|---|---|---|---|---|---|---|
| GET | `/farms/{farm_id}/weather` | `forecast_days` (1-16, def 7), `history_days` (1-90, def 30) | `WeatherBundle` | 404 | yes | no |
| GET | `/farms/{farm_id}/soil` | — | `SoilProfile` | 404 | yes | no |
| GET | `/farms/{farm_id}/vegetation` | `days` (7-365, def 90) | `VegetationSeries` | 404 | yes | no |

Independent of any analysis run — use these for charts. `WeatherBundle` returns
current conditions, hourly forecast, daily forecast, and a history summary in one
payload, so the dashboard needs one request rather than four.

### Analysis — the core

| Method | Path | Query | Response | Errors | Ext | AI |
|---|---|---|---|---|---|---|
| POST | `/farms/{farm_id}/analysis` | `force_refresh` | `AnalysisRun` | 404, 409, 502, 503 | **yes** | **yes** |
| GET | `/farms/{farm_id}/analysis/latest` | — | `AnalysisRun` | 404 | no | no |
| GET | `/farms/{farm_id}/analysis` | `page`, `page_size` | `AnalysisRunList` | 404 | no | no |
| GET | `/analysis/{run_id}` | — | `AnalysisRun` | 404 | no | no |
| GET | `/farms/{farm_id}/dashboard` | — | `FarmDashboard` | 404 | no | no |

**`POST /analysis` is the only endpoint that performs farm AI reasoning.** It is the
slow, expensive one — show a progress state. Everything else reads the stored result.

**`GET /dashboard` never returns `NO_ANALYSIS_YET`.** It returns `has_analysis: false`
and `analysis: null`, so a brand-new farm renders an empty state, not an error.

### Risk and health — projections of the latest run

No external calls, no AI, no recompute. All return `404 NO_ANALYSIS_YET` if the farm
has never been analysed.

| Method | Path | Response | Workflow |
|---|---|---|---|
| GET | `/farms/{farm_id}/risks/weather` | `WeatherRisk` | Weather risk |
| GET | `/farms/{farm_id}/risks/water` | `WaterRisk` | Water / irrigation risk |
| GET | `/farms/{farm_id}/risks/disease` | `DiseaseRisk` | Disease-risk analysis |
| GET | `/farms/{farm_id}/health` | `CropHealth` | Crop health |
| GET | `/farms/{farm_id}/advisories` | `AdvisoryList` | AI advisories |

`/advisories` accepts `category`, `priority`, `include_dismissed`, `page`, `page_size`.

### Recommendations

| Method | Path | Query | Response |
|---|---|---|---|
| GET | `/farms/{farm_id}/recommendations/crops` | `limit` (1-25, def 5) | `CropRecommendationList` |
| GET | `/farms/{farm_id}/recommendations/regenerative` | `limit` (1-25, def 5) | `RegenerativeRecommendationList` |

Both are projections of the latest run. Ranking is deterministic; only the prose is AI.

### Crop images — disease diagnosis

| Method | Path | Request | Response | Errors | AI |
|---|---|---|---|---|---|
| POST | `/farms/{farm_id}/crop-images` | multipart | `201 CropImage` | 404, 413, 415, 422 | if `analyze=true` |
| POST | `/crop-images/{image_id}/analyze` | — | `CropImage` | 404, 409, 503 | **yes** |
| GET | `/crop-images/{image_id}` | — | `CropImage` | 404 | no |
| GET | `/farms/{farm_id}/crop-images` | `page`, `page_size` | `CropImageList` | 404 | no |

Upload form fields: `file` (required), `farm_crop_id` (optional), `note` (optional).
Query: `analyze` (default `false`). Limits: **10 MB**, `image/jpeg`, `image/png`,
`image/webp`.

```ts
const form = new FormData()
form.append("file", file)
if (note) form.append("note", note)
await fetch(`${BASE}/farms/${farmId}/crop-images`, { method: "POST", body: form })
// do NOT set Content-Type — the browser sets the multipart boundary
```

**Recommended flow:** upload without `analyze` → render the thumbnail immediately with
`analysis_status: "pending"` → call `/analyze` → poll `GET /crop-images/{id}` while
status is `pending` or `analyzing`. Use `?analyze=true` for the simple one-shot path.

---

## 6. Core schemas

Full definitions are in `contracts/openapi.json`. The shapes worth understanding:

### `ScoredFactor` — why every score is explainable

```json
{"key": "soil_ph", "label": "Soil pH", "score": 72.0, "weight": 0.25,
 "band": "good", "explanation": "pH 6.2 sits inside maize's tolerated range."}
```

`AnalysisRun.factors`, and the `factors` array on each risk section, decompose the
composite. Render the breakdown — an opaque "68/100" is far less useful than the four
reasons behind it.

### `AnalysisRun` — what the dashboard is built from

```
id, farm_id, status (complete|partial|failed), created_at, duration_ms
model, prompt_version, ai_mode, degraded_sources[]
overall_health_score (0-100), overall_band, summary, factors[]
weather_risk, water_risk, disease_risk, crop_health, soil_assessment
advisories[], crop_recommendations[], regenerative_recommendations[]
sources[]                                  ← DataSourceMeta per input
```

`status: "partial"` is a success, not a failure: the run completed with some inputs
degraded. Show the result and surface `degraded_sources`.

### `FarmDashboard`

```
farm, crops[], has_analysis, analysis|null, current_weather|null,
recent_images[], data_freshness[]
```

One request for the whole dashboard. Check `has_analysis` before reading `analysis`.

### `WaterRisk` — the deterministic/AI split, concretely

Computed by the risk engine: `water_balance_mm`, `deficit_mm`, `days_until_stress`,
`recommended_irrigation_mm`, `soil_moisture_pct`.
Written by the AI: `explanation`.

### `CropImageAnalysis` — three fields that keep the model honest

* `is_plant_material` — `false` means the photo isn't a plant; every other field is
  then unreliable. **Check this first.**
* `differential_diagnoses[]` — alternatives considered, so the UI can show that a
  diagnosis is a judgement rather than a certainty.
* `disclaimer` — always present, always display it.

Also: `condition`, `condition_label`, `severity`, `confidence`, `affected_area_pct`,
`symptoms_observed[]`, `immediate_actions[]`, `treatment_options[]`, `prevention[]`.

---

## 7. Authentication

**None required today.** `ENABLE_AUTH=false` attributes every request to a seeded demo
user, so you can build every screen before login exists.

When auth is switched on, obtain a Supabase JWT client-side and send it:

```
Authorization: Bearer <supabase_access_token>
```

**No request or response shape changes when this happens** — only the identity behind
the data. Handle `401 UNAUTHORIZED` by redirecting to login, and `403 FORBIDDEN` as
"this farm isn't yours".

---

## Implementation status

| Endpoint | Status |
|---|---|
| `GET /health`, `GET /health/ready` | ✅ implemented |
| `GET /reference/enums` | ✅ implemented |
| all other operations | 🚧 `501 NOT_IMPLEMENTED` |

Each 501 names its build step in `error.details.planned_step`. Suggested handling: treat
`NOT_IMPLEMENTED` as a "coming soon" placeholder rather than an error toast — that way
panels light up automatically as the backend lands, with no frontend change.

Request validation is **already live** on every route: a bad `latitude` returns a real
`422` with `details.fields`, so form validation and error display can be built and
tested today.

---

## 9. Change policy

| Change | Allowed |
|---|---|
| New endpoint | ✅ anytime |
| New **optional** response field | ✅ anytime |
| New optional request field | ✅ anytime |
| Rename or remove a field | ⚠️ announced first; new field added alongside, old removed after migration |
| Change a field's type | ⚠️ announced first |
| Change an error `code` | ❌ never |

The backend regenerates `contracts/openapi.json` in its own commit
(`chore(contract): regenerate openapi`) and sends a *"contract updated"* message.
A CI test compares the running app's schema against the committed file, so the contract
cannot drift silently.

Full workflow: [`docs/WORKFLOW.md`](WORKFLOW.md). Design rationale:
[`docs/ARCHITECTURE.md`](ARCHITECTURE.md).
