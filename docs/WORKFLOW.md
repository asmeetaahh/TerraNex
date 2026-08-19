# TerraNex — Single-Branch Workflow

We use **one branch: `main`**. No feature branches, no PRs.

This is safe — not because we're careful, but because of a structural property:
**the two developers write to disjoint directories.** Git cannot produce a conflict
between files that only one person ever touches.

---

## 1. Ownership map

| Path | Owner |
|---|---|
| `backend/**` | Backend dev |
| `contracts/**` | Backend dev (generated output) |
| `docs/**` | Backend dev |
| `frontend/**` | **Frontend dev** |
| `README.md`, `.gitignore`, root config | Backend dev — set up day 0, then frozen |

**Do not edit files outside your column.** If you genuinely need a change in the
other person's territory, send a message. That 10-second exchange prevents a
20-minute rebase.

---

## 2. One-time setup (both machines, day 0)

```bash
git clone <repo-url> TerraNex
cd TerraNex
git config pull.rebase true      # rebase instead of merge — keeps history linear
git config user.name  "Your Name"
git config user.email "you@example.com"
```

`pull.rebase true` is the single most important line here. Without it you get merge
commits that tangle the history; with it, `main` stays a clean straight line.

---

## 3. The loop

```bash
git pull --rebase        # ALWAYS first
# ...work...
git add <your files>
git commit -m "feat(frontend): farm registration form"
git pull --rebase        # again, in case they pushed while you worked
git push
```

### Rule 1 — Push every 30–60 minutes

This is the rule that actually matters. Conflicts are caused by **long-lived local
work**, not by the single branch. Ten small pushes a day is a calm day; one giant
push at hour 20 is a bad evening.

### Rule 2 — Never push a broken `main`

The other person pulls your commits constantly. A broken `main` blocks them.

Backend, before every push:
```bash
cd backend && uv run pytest -q && uv run python -c "from app.main import app"
```

Frontend, before every push:
```bash
cd frontend && npm run build
```

### Rule 3 — Never `git push --force` on `main`

It rewrites history the other person has already pulled. There is no situation in
this project where force-pushing is the right answer. Ask first.

---

## 4. Commit messages

Scoped conventional commits, so the log is readable per-owner:

```
feat(backend):   new capability in the API
feat(frontend):  new screen or component
fix(backend):    bug fix
chore(contract): regenerate openapi.json
docs:            documentation only
test(backend):   tests only
```

**Contract regeneration is always its own isolated commit.** That makes it trivial to
see exactly when the API surface changed and what changed with it.

---

## 5. Lockfiles have exactly one owner

| File | Owner |
|---|---|
| `backend/uv.lock` | Backend dev |
| `frontend/package-lock.json` | Frontend dev |

Never run `npm install` from the backend side, or `uv sync` from the frontend side.
Lockfiles are the one kind of file that conflicts messily, and single ownership
eliminates the possibility entirely.

---

## 6. The contract handshake

This is how the frontend stays unblocked without ever reading backend code.

**Backend dev**, after changing any endpoint or schema:

```bash
cd backend
make contract                      # writes ../contracts/openapi.json
uv run pytest tests/api/test_openapi_contract.py
git add ../contracts/openapi.json ../docs/API_CONTRACT.md
git commit -m "chore(contract): regenerate openapi"
git push
```

→ then send one message: **"contract updated — regenerate types"**

**Frontend dev**, on receiving that message:

```bash
git pull --rebase
cd frontend
npx openapi-typescript ../contracts/openapi.json -o src/api/types.gen.ts
npx tsc --noEmit                   # compile errors here = breaking change, speak up
```

### Contract change policy

| Change | Allowed? |
|---|---|
| New endpoint | ✅ anytime |
| New **optional** response field | ✅ anytime |
| New optional request field | ✅ anytime |
| Rename / remove a field | ⚠️ avoid after Phase 2 — add the new one alongside, migrate, then remove |
| Change a field's type | ⚠️ same — announce first |
| Change an error `code` | ❌ the frontend branches on these |

`test_openapi_contract_matches_committed` fails CI if the committed contract drifts
from the running app, so the contract cannot silently change out from under anyone.

---

## 7. If a conflict does happen

Because ownership is disjoint, resolution is mechanical — take whole files by owner:

```bash
git checkout --ours   backend/path/to/file     # during YOUR rebase, keep backend
git checkout --theirs frontend/path/to/file    # keep their frontend version
git add <file>
git rebase --continue
```

If a rebase goes badly wrong, nothing is lost:

```bash
git rebase --abort      # back to where you started
git reflog              # every prior state is recoverable
```

---

## 8. Daily rhythm

| When | What |
|---|---|
| Start of session | `git pull --rebase` |
| Every 30–60 min | commit + `pull --rebase` + push |
| After a contract change | push, then message the other dev |
| Before a break | push whatever works — never leave work only on your laptop |
| End of day | both pull, both verify the app runs end-to-end together |
