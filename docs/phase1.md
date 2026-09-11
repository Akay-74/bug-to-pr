# Phase 1 — Benchmark + Backend Foundation

Implement Phase 1 of the Bug-to-PR Agent v1.

Read `docs/architecture.md` and follow it. Do not implement functionality belonging to later phases.

Inspect the existing repository before changing anything. Preserve the existing frontend; another agent is building it.

---

## 1. Scope

Implement only:

* Backend project structure
* PostgreSQL + SQLAlchemy
* Alembic migrations
* PostgreSQL-backed job queue
* Benchmark metadata system
* Docker sandbox foundation
* Stage 0 benchmark validation
* Benchmark CLI
* FastAPI API foundation
* Tests for all of the above

Do NOT implement yet:

* LLM fix generation
* Localization
* Embeddings
* Model routing
* Retry/repair
* GitHub write operations
* Repository forks
* PR creation
* OAuth
* Public live execution
* Redis
* Celery
* Next.js
* S3
* AST/dependency graph

Keep the implementation small and directly usable by later phases.

---

## 2. Backend Structure

Create or adapt a structure similar to:

```text
backend/
├── api/
├── agent/
├── benchmark/
├── models/
├── pipeline/
├── tools/
├── worker.py
└── config.py
```

Use sensible module boundaries.

Do not create abstractions that are not needed yet.

---

## 3. Benchmark Candidates

Create initial candidate entries for:

```text
sympy__sympy-13480
scikit-learn__scikit-learn-13779
pydata__xarray-4629
django__django-11163
matplotlib__matplotlib-20676
sympy__sympy-11618
django__django-11141
django__django-11149
scikit-learn__scikit-learn-14053
astropy__astropy-14508
astropy__astropy-14539
pylint-dev__pylint-4604
```

These are candidates, NOT automatically valid benchmark entries.

Do not fabricate missing metadata.

---

## 4. Benchmark Metadata

Create:

```text
benchmark/
└── issues/
    └── <issue_id>/
        ├── metadata.json
        └── problem.md
```

Support these fields:

```json
{
  "issue_id": "",
  "issue_url": "",
  "repo": "",
  "base_commit": "",
  "python_version": "",
  "install_command": "",
  "regression_test_command": "",
  "relevant_test_command": "",
  "full_test_command": "",
  "protected_test_paths": [],
  "baseline_failures": [],
  "validation_status": "pending"
}
```

Use:

```text
validation_status:
pending
valid
invalid
environment_error
test_error
```

Do not populate unknown values with guesses.

---

## 5. Benchmark Validation

Implement:

```text
python -m backend.benchmark validate <issue_id>
python -m backend.benchmark validate-all
python -m backend.benchmark status
```

For a candidate issue:

1. Load metadata.
2. Validate repository against the curated allowlist.
3. Clone the repository into an isolated temporary workspace.
4. Checkout the exact `base_commit`.
5. Prepare the configured environment.
6. Run the regression test.
7. Require the regression test to FAIL.
8. Run the configured relevant/full test suite once.
9. Capture baseline failing tests.
10. Save the baseline failures.
11. Set the correct validation status.
12. Clean up the temporary workspace.

The benchmark is valid only when the regression test demonstrably fails at the base commit.

Never classify infrastructure/environment problems as agent failures.

---

## 6. Baseline Test Capture

Baseline failures are established during Stage 0 only.

Capture enough structured information to compare against later post-fix failures.

At minimum:

```text
test identifier
exit code
stdout
stderr
```

Store normalized failing test identifiers in:

```text
baseline_failures
```

Do not rerun the baseline suite during later verification just to rediscover these failures.

---

## 7. Environment Handling

Support benchmark-specific configuration.

Do not build a generalized dependency resolver.

At minimum support:

```text
Python version
install command
test commands
```

The validator must clearly report:

* environment setup success/failure
* installation output
* test command
* exit code
* stdout
* stderr
* duration

Historical environments may fail because of dependency or interpreter incompatibility. Record this as `environment_error` rather than hiding it.

---

## 8. Docker Sandbox

Create the initial sandbox abstraction.

Required interface should be approximately:

```python
run(
    command,
    workspace,
    timeout,
    cpu_limit,
    memory_limit
) -> ExecutionResult
```

`ExecutionResult` must contain:

```text
exit_code
stdout
stderr
duration
timed_out
```

The container must:

* run as non-root
* support CPU limits
* support memory limits
* enforce timeout
* use an ephemeral workspace
* not expose the host filesystem unnecessarily
* not expose the Docker socket
* not receive application secrets
* use restricted networking where practical

Do not over-engineer the sandbox yet.

---

## 9. Protected Test Detection

Implement reusable infrastructure for checking whether protected benchmark files were modified.

Given:

```text
base_commit
current_worktree
protected_test_paths
```

return:

```text
modified = true/false
changed_files = [...]
```

Use the benchmark's explicit `protected_test_paths`.

Do NOT rely solely on generic patterns such as:

```text
tests/
test_*.py
*_test.py
```

Later agent verification will call this function.

---

## 10. PostgreSQL

Set up:

* PostgreSQL
* SQLAlchemy
* Alembic

Create these tables.

### runs

```text
id
issue_id
issue_url
repo
base_commit
mode
status
current_stage
model_policy
attempts_taken
total_cost
pr_url
started_at
completed_at
```

### attempts

```text
id
run_id
attempt_number
model
hypothesis
diff
status
failure_type
input_tokens
output_tokens
cost
started_at
completed_at
```

### verification_results

```text
id
attempt_id
check_name
status
command
exit_code
stdout
stderr
duration_ms
```

Add:

* primary keys
* foreign keys
* useful indexes
* timestamps where appropriate

Do not create an events/event-sourcing table.

---

## 11. PostgreSQL Job Queue

Use PostgreSQL itself as the queue.

Supported run states:

```text
queued
running
completed
failed
```

Supported stages:

```text
intake
baseline
localization
generation
verification
retry
pr_creation
completed
```

Implement safe claiming of queued jobs using PostgreSQL transaction/row-locking semantics.

Two workers must not be able to claim the same job.

Create:

```text
backend/worker.py
```

The worker must currently:

1. claim a queued job
2. mark it `running`
3. execute a placeholder pipeline
4. mark it `completed` or `failed`

Do not implement the actual AI pipeline yet.

---

## 12. FastAPI Foundation

Create a minimal FastAPI application.

Implement:

```text
POST /api/runs
GET  /api/runs/{run_id}
GET  /api/benchmark/issues
```

### POST /api/runs

Accept:

```json
{
  "issue_url": "..."
}
```

Validate that the issue belongs to the curated benchmark.

Create a PostgreSQL run with:

```text
status = queued
current_stage = intake
mode = public_demo
```

Do not execute the agent yet.

### GET /api/runs/{run_id}

Return actual persisted run data.

### GET /api/benchmark/issues

Return supported benchmark issues and validation status.

No authentication yet.

---

## 13. Repository Allowlist

Implement an explicit allowlist.

The system must reject:

* unsupported repositories
* unsupported issues

Do not accept arbitrary public GitHub repositories in this phase.

Keep the allowlist configuration separate from code where practical.

---

## 14. Configuration

Use environment/configuration for:

```text
DATABASE_URL
BENCHMARK_PATH
DOCKER_CPU_LIMIT
DOCKER_MEMORY_LIMIT
DOCKER_TIMEOUT
REPOSITORY_ALLOWLIST
```

Create:

```text
.env.example
```

Never commit real credentials.

---

## 15. Fixture Repository

Create a tiny local fixture repository for automated tests.

It should allow the test suite to verify:

* Docker execution
* a deliberately failing regression test
* baseline failure capture
* protected test detection
* successful benchmark validation

Do not depend on live GitHub repositories for unit tests.

---

## 16. Automated Tests

Write tests for:

### Benchmark

* metadata loading
* issue lookup
* repository allowlist
* validation states
* baseline regression failure requirement
* baseline failure parsing

### Sandbox

* command execution
* timeout
* exit code
* stdout/stderr capture

### Test protection

* protected file modification detected
* unrelated file modification allowed
* multiple protected paths handled correctly

### Database

* model creation
* foreign keys
* run creation
* attempt creation
* verification creation

### Queue

* queued job can be claimed
* claimed job becomes running
* two workers cannot claim the same job

### API

* supported issue accepted
* unsupported issue rejected
* run persisted
* run can be retrieved
* benchmark list returned

---

## 17. README

Update/create the README with:

* project setup
* PostgreSQL setup
* environment variables
* migration commands
* benchmark structure
* benchmark validation commands
* worker startup command
* API startup command
* test command
* Docker requirements

Document exactly how to validate one benchmark and all benchmarks.

---

## 18. Important Constraints

Do not:

* fabricate benchmark metadata
* silently skip failed validation
* mix environment failures with agent failures
* expose secrets to Docker
* add Redis
* add Celery
* add frontend frameworks
* add AST retrieval
* add embeddings
* add LLM calls
* add GitHub write operations
* add OAuth
* add PR creation

Do not modify the existing Stitch frontend except where absolutely necessary to keep the project runnable.

---

## 19. Definition of Done

Phase 1 is complete only when:

1. PostgreSQL is running and migrations succeed.
2. Benchmark metadata loads correctly.
3. The repository allowlist works.
4. Docker sandbox executes commands.
5. Fixture benchmark validation works.
6. A valid benchmark records a regression-test failure.
7. Invalid/environment-broken benchmarks are classified correctly.
8. Baseline failures are stored.
9. Protected test detection works.
10. PostgreSQL queue safely prevents duplicate job claims.
11. FastAPI creates and retrieves runs.
12. Automated tests pass.
13. One real candidate benchmark has been attempted and its actual status is reported.
14. README contains reproducible setup instructions.

Do not move to Phase 2 automatically.

At the end, report:

```text
Implemented:
...

Tests:
X passed
Y failed

Real benchmark validation:
<issue>: <status>

Known blockers:
...

Files added/changed:
...
```

Be honest about any benchmark candidate that could not be validated.
