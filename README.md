# TerraNex

**AI-powered agricultural intelligence platform.** Register a farm, and TerraNex fuses
weather, soil, and vegetation data with AI reasoning to produce a unified farm health
dashboard: weather risk, water/irrigation risk, disease risk, crop health, AI advisories,
crop recommendations, regenerative-agriculture guidance, and multimodal crop-image diagnosis.

---

## Architecture in one line

```
React (Vite + TS + Tailwind)  →  TerraNex API (FastAPI)  →  services  →  weather/soil/vegetation providers
                                                                      →  AI reasoning (Gemini)
                                                                      →  validated structured response  →  React
```

The **backend is the only intelligence layer**. The frontend never calls Gemini, weather,
soil, or any external provider directly — it speaks REST to `/api/v1` and nothing else.

A key design rule: **the AI never invents numbers.** A deterministic Python risk engine
computes every score and threshold from real data; Gemini interprets those computed facts
into narrative and prioritization, validated against a Pydantic schema.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design and
[`docs/API_CONTRACT.md`](docs/API_CONTRACT.md) for the API contract.

---

## Repository layout & ownership

| Path | Owner | Contents |
|---|---|---|
| `backend/` | **Backend dev** | FastAPI app, services, providers, AI layer, tests |
| `contracts/openapi.json` | **Backend dev** (generated) | Machine-readable API contract — frontend codegen input |
| `docs/` | **Backend dev** | Architecture, API contract, workflow |
| `frontend/` | **Frontend dev** | React + Vite + TypeScript + Tailwind app |
| root files | Backend dev (day 0, then frozen) | README, .gitignore |

We work on a **single `main` branch**. This is safe because the two owners write to
disjoint directories — git has nothing to conflict over.
**Read [`docs/WORKFLOW.md`](docs/WORKFLOW.md) before your first push.**

---

## Prerequisites

| Tool | Version | Install |
|---|---|---|
| Python | 3.12 | pinned via `uv` — see below |
| [uv](https://docs.astral.sh/uv/) | ≥ 0.7 | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Node | ≥ 20 | `brew install node` |
| Supabase project | free tier | <https://supabase.com/dashboard> |

> **Python 3.12 specifically.** Newer Pythons occasionally lack prebuilt wheels for
> `pydantic-core` / `asyncpg` / `pillow`. `uv` handles the pin automatically from
> `backend/.python-version` — you don't have to install 3.12 yourself.

---

## Backend — quick start

```bash
cd backend
cp .env.example .env          # then fill in the values (see below)
uv sync                       # creates .venv, installs deps, respects .python-version
uv run alembic upgrade head   # only if DATABASE_URL is set — see below
uv run uvicorn app.main:app --reload --port 8000
```

**`alembic upgrade head` is required whenever `DATABASE_URL` is set, and it is not run
automatically.** Migrations are not applied at boot, so a host pointed at an unmigrated
database fails on startup rather than degrading — and, once analysis runs are persisted,
an unmigrated database would also mean every analysis is lost on restart. With
`DATABASE_URL` unset the API runs entirely in memory and needs no migration at all,
which is what lets a fresh clone boot with nothing provisioned.

Verify:

```bash
curl http://localhost:8000/api/v1/health
open http://localhost:8000/docs          # interactive Swagger UI
```

Common tasks (from `backend/`):

```bash
make dev         # run the reload server
make test        # pytest
make contract    # regenerate ../contracts/openapi.json
make check       # lint + test + contract drift check
```

### Environment variables

Copy `backend/.env.example` → `backend/.env`. Every key is documented inline there.
The ones that matter on day one:

| Variable | Purpose |
|---|---|
| `ENABLE_AUTH` | `false` while building UI — injects the demo user so no login is needed |
| `AI_PROVIDER` | `mock` (no key, free, deterministic) or `gemini` |
| `GEMINI_API_KEY` | only needed when `AI_PROVIDER=gemini` |
| `DATABASE_URL` | Supabase Postgres connection string |
| `CORS_ORIGINS` | comma-separated; must include the frontend dev origin |

`.env` is gitignored. **Never commit real keys.**

---

## Frontend — quick start

> `frontend/` is currently an empty placeholder. The frontend developer owns it and
> scaffolds the app there; the backend never writes into it.

**First time only** — scaffold the app (frontend dev):

```bash
cd frontend
npm create vite@latest . -- --template react-ts
npm install
npm install -D tailwindcss @tailwindcss/vite
npm run dev                   # http://localhost:5173
```

**Thereafter:**

```bash
cd frontend
npm install
npm run dev
```

Create `frontend/.env.local`:

```
VITE_API_BASE_URL=http://localhost:8000/api/v1

# Not needed yet — the backend runs with ENABLE_AUTH=false, so every request is
# attributed to a seeded demo user. Fill these in when auth is switched on.
VITE_SUPABASE_URL=
VITE_SUPABASE_ANON_KEY=
```

### Generating typed API bindings

The backend commits a machine-readable contract. Regenerate your TypeScript types
from it any time the backend announces a contract update:

```bash
cd frontend
npx openapi-typescript ../contracts/openapi.json -o src/api/types.gen.ts
```

You now have fully-typed request and response shapes for every endpoint.
**You never need to open a file under `backend/`.**

If `types.gen.ts` produces a TypeScript error after regenerating, that is the
early-warning signal for an unintended breaking change — ping the backend dev.

---

## Running both together

```bash
# Terminal 1
cd backend && uv run uvicorn app.main:app --reload --port 8000

# Terminal 2
cd frontend && npm run dev
```

The backend allows `http://localhost:5173` via CORS out of the box.

---

## Testing

```bash
cd backend
uv run pytest -q                 # everything
uv run pytest tests/unit -q      # risk-engine math (fast, no network)
```

Tests never hit the network or the real AI: external providers are replayed from
recorded fixtures via `respx`, and the AI layer runs in `mock` mode.

---

## License

TBD — intended as an open agricultural digital public good.
