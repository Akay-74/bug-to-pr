# Bug-to-PR Agent

Takes a curated SWE-bench-style issue and carries it end to end:

```
Issue → benchmark validation → localization → fix generation
      → sandbox validation → git commit → draft GitHub PR
```

A coding model generates patches — a local one through Ollama by default, or
a hosted one such as Gemini — and every candidate is applied and tested inside
a locked-down Docker sandbox. A
pull request is only ever opened for a fix that passed validation, and only
when GitHub access has been configured explicitly.

| Phase | Scope | Spec |
|-------|-------|------|
| 1 | Project skeleton, Postgres + Alembic, job queue, benchmark metadata, Docker sandbox, Stage 0 validation | `docs/phase1.md` |
| 2 | Repository localization (embeddings + lexical, ranked candidates) | `docs/phase2.md` |
| 3 | Fix generation, patch application, sandbox validation, bounded candidates | `docs/phase3.md` |
| 4 | Git branch/commit and draft pull request | `docs/phase4.md` |
| 5 | Whole workflow, dashboard, hardening, benchmark evaluation | `docs/phase5.md`, `docs/phase5-audit.md` |

Latest benchmark evaluation (`qwen2.5-coder:7b`, 9 validated issues): **3 of 8
scored issues fixed and verified (37.5%)**, one environment failure reported
separately. Full numbers in `backend/evaluation/report.json` and on the
dashboard's Benchmarks page.

## Requirements

* Python 3.11+ (CI uses 3.13)
* Docker (used both to run PostgreSQL for local dev and as the sandbox for
  benchmark validation)
* Git
* [ripgrep](https://github.com/BurntSushi/ripgrep) (`rg`), used by code localization —
  `sudo apt install ripgrep` / `brew install ripgrep`
* Node.js 20.9+ for the dashboard
* A coding model: [Ollama](https://ollama.com) with `qwen2.5-coder:7b`
  pulled, **or** an API key for a hosted model (see below)

## Setup

```bash
git clone <this repo> && cd <repo>
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # adjust if needed; never commit .env
```

## Choosing the coding model

Local (default, no key, nothing leaves your machine):

```bash
ollama pull qwen2.5-coder:7b
# .env
GENERATION_BACKEND=ollama
GENERATION_MODEL=qwen2.5-coder:7b
```

Hosted, through any provider with an OpenAI-compatible API. Gemini has a free
tier; get a key at [Google AI Studio](https://aistudio.google.com/apikey):

```bash
# .env
GENERATION_BACKEND=openai_compatible
GENERATION_API_BASE=https://generativelanguage.googleapis.com/v1beta/openai
GENERATION_MODEL=gemini-3.8-flash
GENERATION_API_KEY=your-key-here
```

Groq, OpenRouter, Cerebras and Mistral work the same way with their own
`GENERATION_API_BASE`. The key is read only from `.env`, is sent only in the
request's `Authorization` header, is redacted from every error message and
log, and never reaches the sandbox. A hosted model receives the issue text and
the relevant source files, so keep that in mind before pointing it at private
code.

## PostgreSQL

Run Postgres locally with Docker:

```bash
docker run -d --name bug2pr-postgres \
  -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=bug2pr \
  -p 5432:5432 postgres:16-alpine
```

`DATABASE_URL` in `.env` defaults to
`postgresql+psycopg2://postgres:postgres@localhost:5432/bug2pr`, matching the
container above.

## Migrations

```bash
alembic upgrade head          # apply migrations
alembic revision --autogenerate -m "message"   # create a new migration after model changes
```

## Environment variables

See `.env.example`:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | SQLAlchemy connection string |
| `BENCHMARK_PATH` | Directory containing `benchmark/issues/<issue_id>/` |
| `DOCKER_CPU_LIMIT` | CPU limit (cores) for sandbox containers |
| `DOCKER_MEMORY_LIMIT` | Memory limit (e.g. `2g`) for sandbox containers |
| `DOCKER_TIMEOUT` | Default per-command timeout, in seconds |
| `REPOSITORY_ALLOWLIST` | Path to the allowlist JSON file |
| `LOCALIZATION_CACHE_DIR` | Embedding + git-mirror cache root |
| `LOCALIZATION_EMBEDDING_MODEL` | Embedding backend (`jina`) |
| `GENERATION_BACKEND` / `GENERATION_MODEL` | `ollama` or `openai_compatible`, and the model name |
| `GENERATION_OLLAMA_HOST` | Ollama server, localhost only |
| `GENERATION_API_BASE` / `GENERATION_API_KEY` | Hosted model endpoint and key (`openai_compatible` only) |
| `GENERATION_TIMEOUT_SECONDS` | Per-generation timeout |
| `GENERATION_CANDIDATE_LIMIT` | Max candidates per issue |
| `GENERATION_MAX_PATCH_LINES` | Max lines in an accepted patch |
| `GITHUB_ENABLED` | Explicit opt-in before anything is pushed |
| `GITHUB_TARGET_REPO` | Where branches/PRs go (never upstream) |
| `GITHUB_PR_BASE_BRANCH` / `GITHUB_BRANCH_PREFIX` | PR base and branch naming |
| `CORS_ORIGINS` | Browser origins allowed to call the API |

There is deliberately **no** GitHub token setting: pushing and PR creation go
through the `gh` CLI's own credential store. `GENERATION_API_KEY` is the only
secret this project reads, and only from `.env`, which is gitignored.

## Benchmark structure

```
backend/benchmark/
├── allowlist.json          # explicit "org/repo" allowlist, kept as data
└── issues/
    └── <issue_id>/
        ├── metadata.json    # see backend/benchmark/schema.py for the schema
        ├── problem.md       # the original problem statement
        └── test_patch.diff  # optional: applied before the regression test
                              # runs, when the failing test doesn't exist yet
                              # at base_commit (the normal case for real
                              # historical bugs)
```

The 12 initial candidates (real instances sourced from the public SWE-bench
dataset, cross-checked against their actual merged GitHub pull requests) live
under `backend/benchmark/issues/`. Their `install_command`/test commands are
transcribed from the SWE-bench harness's own per-repo/version specs — real
data, not guesses. Being listed there makes them *candidates*, not
automatically valid; run the validator to find out.

## Benchmark validation

```bash
python -m backend.benchmark validate <issue_id>
python -m backend.benchmark validate-all
python -m backend.benchmark status
```

`validate` clones the repo at `base_commit` into an isolated temp directory,
applies `test_patch.diff` if present, builds a virtualenv inside a Docker
sandbox, runs `install_command`, requires the regression test to **fail**,
then runs the relevant/full suite once to capture baseline failures into
`metadata.json`. Historical environment breakage is recorded as
`environment_error`, never conflated with a genuine `invalid` benchmark.

Validating the heavier candidates (matplotlib, astropy, scikit-learn) pulls
and compiles real historical scientific-Python dependency trees and can take
a long time or require system packages (e.g. LaTeX for matplotlib); this
phase does not attempt to make that fast, only correct and honestly
classified.

## Worker

```bash
python -m backend.worker
```

Polls `runs` for a queued job (safe concurrent claiming via
`SELECT ... FOR UPDATE SKIP LOCKED`) and drives it through the real workflow.

The loop is bounded on request, so a run can finish and exit rather than only
ever being killed:

```bash
python -m backend.worker --once        # one job, then exit
python -m backend.worker --max-jobs 5
python -m backend.worker --idle-exit   # drain the queue, then exit
```

## API

```bash
uvicorn backend.api.main:app --reload
```

* `POST /api/runs` — `{"issue_url": "..."}`, must match a benchmark issue on
  the allowlist; creates a `queued` run.
* `GET /api/runs/{run_id}` — persisted run state.
* `GET /api/benchmark/issues` — every benchmark candidate and its
  `validation_status`.
* `GET /api/benchmark/evaluation` — the end-to-end evaluation report.
* `POST /api/localize/{issue_id}` · `GET /api/localize/{issue_id}` — Phase 2.
* `POST /api/fix/{issue_id}` · `GET /api/fix/{fix_id}` — Phase 3.
* `POST /api/pr/{fix_id}/commit` — branch, apply, verify, commit.
* `POST /api/pr/{fix_id}` — the above, then push and open a draft PR.
* `GET /api/pr/{fix_id}` — commit/PR state (`?refresh=true` re-reads GitHub).
* `POST /api/workflow/{issue_id}` — the complete workflow, synchronously.
* `GET /api/workflow` — every issue with the state of its latest run.
* `GET /api/workflow/{issue_id}` · `GET /api/workflow/run/{run_id}` —
  everything about one run: localization, attempts, patch, test results,
  commit and PR state, errors.

No authentication. Browser origins are an explicit allowlist
(`CORS_ORIGINS`), never `*`.

## Dashboard

```bash
cd frontend
npm install
npm run dev          # http://localhost:3000
```

Reads the API at `NEXT_PUBLIC_API_BASE` (default `http://localhost:8000`) and
shows issue selection, benchmark validation state, localization, the
generated patch, every candidate attempt with its checks, commit and draft-PR
state, and errors. It holds no data of its own.

## Draft pull requests

Delivery is off by default. To enable it:

```bash
gh auth login                       # the CLI holds the credential, not this project
# in .env:
GITHUB_ENABLED=true
GITHUB_TARGET_REPO=you/your-test-repo
```

With it off, the workflow still branches, commits and verifies locally, and
records why no PR exists. No token is ever read, stored or logged by this
project.

## Benchmark evaluation

```bash
python -m backend.evaluation run          # every validated issue, resumable
python -m backend.evaluation run --limit 1
python -m backend.evaluation report       # summary of an existing report
```

Writes `backend/evaluation/report.json` after every issue, so a run that
takes hours can be interrupted and resumed. Environment failures are reported
separately from model failures and excluded from the success rate.

## Tests

Tests need the same Postgres container as above (they use a separate
`bug2pr_test` database, created automatically) and Docker for the
sandbox/validation tests.

```bash
pytest
```

`tests/fixtures/fixture_repo/` is a tiny git repo with a deliberately buggy
`add()` function and a failing test, used so the sandbox/validation/
protected-test tests don't depend on live GitHub repos. Being a git
repository of its own, it isn't committed: `tests/conftest.py` builds it from
`tests/fixtures/build_fixture_repo.py` on the first run, with fixed commit
SHAs.

The tests need no model, API key or network access to GitHub: model backends
and `gh` are faked. Some suites start real sandbox containers, so a full run
takes a while; the fast unit suites are the `test_delivery_*`,
`test_evaluation_harness`, `test_security_audit` and `test_git_mirror` files.

GitHub Actions (`.github/workflows/ci.yml`) runs the backend suite against a
Postgres service, and lints, type-checks and builds the dashboard.

## Security

`docs/phase5-audit.md` records the audit: sandbox isolation, host filesystem
exposure, Docker socket, secret handling, GitHub credentials, generated
patches, test-time network, command injection and path traversal — plus the
two vulnerabilities found and fixed, and the residual risks that were
accepted. The invariants are pinned by `tests/test_security_audit.py`.

## Known limitations (by design)

* One model backend at a time (Ollama or an OpenAI-compatible API); no model
  routing or cost accounting.
* No authentication or multi-tenancy.
* No Redis/Celery — Postgres is the queue.
* PRs are opened against a configured test repository, never upstream, and
  never merged.

## License

MIT — see [LICENSE](LICENSE).
