# Phase 5 — Security Audit & Performance Report

Companion to `docs/phase5.md`. Everything below was checked against the code
as it stands, and every invariant named here is pinned by a test in
`tests/test_security_audit.py` unless stated otherwise.

## 1. Security audit

### Findings

**One real vulnerability was found and fixed during this audit.**

| # | Area | Finding | Status |
|---|------|---------|--------|
| 1 | Path traversal | `backend/benchmark/loader.py::_issue_dir` joined an issue id straight onto the benchmark root. Issue ids arrive from API path parameters (`/api/workflow/{issue_id}`), so an id like `../../../etc` resolved outside the benchmark tree — and `save_metadata` uses the same helper to **write**. The API returned 404 for probes only incidentally (no `metadata.json` at the target), not because traversal was rejected. | **Fixed**: an id must be a single path segment, and the resolved directory must stay under the benchmark root. Regression tests cover read, write and legitimate ids. |
| 2 | Secret leakage | HTTP error bodies were built from `str(exc)` and bypassed redaction, so a token echoed by `git`/`gh` could reach an API response. | **Fixed in Phase 4**; the API boundary now redacts every delivery error regardless of what raised it. |

### Reviewed and found sound

| Risk | Control |
|------|---------|
| **Sandbox escape** | `cap_drop=["ALL"]`, `security_opt=["no-new-privileges"]`, non-root UID `65534:65534`, CPU/memory limits, `timeout(1)` inside the container plus a client-side deadline. No `privileged`, `network_mode`, `pid_mode` or `ipc_mode` anywhere. |
| **Host filesystem access** | The only bind mount is one ephemeral workspace directory at `/workspace`, created per run and deleted after. No host project path is ever mounted. |
| **Docker socket exposure** | The socket is never mounted or referenced. Sandboxes are constructed without a pre-authenticated client. |
| **Secret leakage** | No token setting exists in `Settings`; nothing in `backend/delivery/` reads the environment. Every `git`/`gh` stream passes through `github.redact()` before reaching an exception, the database or an API body. |
| **GitHub credential exposure** | Auth is delegated to the `gh` CLI's own credential store. The push configures `credential.helper` per-invocation with `-c`, so no token enters the workspace's git config or a remote URL. Credentials never reach the sandbox, which is given no environment at all. |
| **Unsafe generated patches** | Patches are size-capped, rejected for `..`/absolute paths, rejected for protected test paths, and applied only inside a disposable workspace. The protected-test check runs against the model's changes alone, before the benchmark's own tests are added. |
| **Unrestricted test-time network** | `run_tests` runs with `network_disabled=True`; only dependency installation enables the network, because a package index is unavoidable there. `DockerSandbox` defaults to no network, so a new call site that forgets the flag gets the safe behaviour. |
| **Command injection** | No `shell=True` anywhere in the backend. Every subprocess is an argv list. The only shell strings are the benchmark's own curated `install_command`/test commands, `shlex.quote`d into the container. Model output becomes a *patch*, never a command — there is no path from a generation result to a shell. |
| **Repository code on the host** | The host only parses repository files (Python AST, `ripgrep` with `--fixed-strings --`). No `exec`, `eval`, `__import__` or `runpy` in the backend. Execution belongs to the container. |

### Residual risks (accepted, documented)

- **Symlink-creating patches.** `git apply` can create a symlink; a later
  write through it is not something path validation inspects. Impact is
  bounded: the workspace is disposable, the patch is only exercised inside
  the container, and the committed tree is inspected before delivery.
- **Argument-shaped repository names.** A repo name beginning with `-` could
  in principle be read as a git flag. The allowlist is what prevents this
  today; the mirror slug also strips leading punctuation.
- **Installation needs the network.** Dependency installation cannot be
  offline for these benchmarks. It runs before the patch is exercised, and
  the test phase — the step that runs generated code — has no network.

## 2. Performance

Measured on this machine against `pylint-dev__pylint-4604`, rather than
estimated.

| Step | Cost | Note |
|------|------|------|
| Localization, reused from the database | **25 ms** | What a re-run of an already-localized issue costs. |
| Localization, recomputed with warm embedding cache | **38.5 s** | Of which ~19 s was the clone. |
| Repository clone at base commit (network) | **~19 s** | Dominant repeated cost. |
| Dependency install (container) | **~59 s** | Per candidate workspace. |
| Model generation (qwen2.5-coder:7b) | **7.7 s** | One candidate. |
| Regression + relevant tests | **~1.6 s** | Small for this issue. |
| **Full workflow, one candidate** | **4 m 07 s** | End to end, including delivery. |

### What the measurement changed

Localization reuse was already ~1500× cheaper than recomputation, and the
embedding cache was working. The measurement showed the real bottleneck was
elsewhere: **a single run clones the same repository about four times**
(baseline measurement, source context, each candidate's validation, and
delivery verification).

So a local bare-mirror cache was added to `backend/tools/git.py`: the first
checkout fetches into `cache/git-mirrors/<repo>.git`, and later checkouts
fetch from disk. Measured effect on pylint: **~19–27 s → ~10 s per clone**,
with the identical commit checked out every time. It is an optimisation, not
a dependency — if the mirror cannot be prepared, the original network clone
path runs unchanged (`tests/test_git_mirror.py`).

### Bounds verified

- **Candidate generation** is bounded by `GENERATION_CANDIDATE_LIMIT`, and an
  identical regenerated patch is rejected without re-validating it.
- **Model calls** are one per candidate; localization is reused rather than
  recomputed; source context is read once per run, not once per candidate.
- **Repository/test execution** is bounded by `DOCKER_TIMEOUT` in the
  container and by `GENERATION_TIMEOUT_SECONDS` for the model.
- **No infinite background jobs**: the worker accepts `--once`,
  `--max-jobs` and `--idle-exit`, so a run can finish and exit.
