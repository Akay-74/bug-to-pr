# Phase 3 — Fix Generation & Validation

## Objective
Build the first end-to-end **issue → localization → generated patch → sandbox validation** pipeline.

Phase 3 must not implement GitHub PR creation yet.

## 1. Input
Reuse Phase 2 localization.

Given an `issue_id`:
1. Load the benchmark issue.
2. Resolve its exact repository and base commit.
3. Obtain the Phase 2 Top-5 localization results.
4. Use the best candidate locations for fix generation.

Do not execute repository code outside the existing sandbox.

## 2. Fix Generation
Generate a proposed code change using:
- issue title/problem statement
- repository/base commit
- localized files/symbols
- relevant source context
- relevant regression-test information when available

Use the project's configured local coding model/backend.

The output must be a **patch/diff**, not arbitrary filesystem mutations.

Persist:
- issue ID
- repository
- base commit
- model
- prompt/input metadata
- generated patch
- timestamp
- generation status

No GitHub write operations.

## 3. Patch Application
Apply generated patches only inside an isolated temporary workspace based on the exact benchmark base commit.

- Never modify the user's source checkout.
- Reject malformed/unapplicable patches cleanly.
- Keep the existing Docker sandbox security model unchanged.
- Do not expose Docker socket, host filesystem, or secrets.

## 4. Validation
After applying a candidate:
1. Run relevant regression tests.
2. Confirm failing regression tests now pass.
3. Confirm required previously-passing tests remain passing.
4. Record stdout/stderr, exit code, duration, and result.

Distinguish:
- generation failure
- patch-application failure
- environment failure
- test failure
- success

Reuse the existing Phase 1 sandbox/benchmark infrastructure.

## 5. Candidate Iteration
Support multiple candidates per issue.

For each:
`generate → apply → validate → record`

Stop on success or a configured candidate limit. Never retry indefinitely.

## 6. API
Add:

`POST /api/fix/{issue_id}` — start generation and return fix ID/status.

`GET /api/fix/{fix_id}` — return issue, status, localization target, model, patch, validation status, test results, and errors.

Follow existing API conventions.

## 7. Persistence
Add models/migrations only where necessary.

Persist:
- fix attempt
- generated patch
- model/configuration metadata
- validation result
- timestamps/status

Reuse existing Run/Attempt/LocalizationResult models where appropriate. No new database/vector store.

## 8. Tests
Test:
- request validation
- localization → generation handoff
- mocked model invocation
- malformed output
- patch parsing/application
- workspace isolation
- successful validation
- test failure
- environment failure
- multiple candidates
- persistence
- API schema
- security invariants

Tests must not require a real LLM or external API.

## 9. End-to-End Verification
Run the pipeline against at least one previously validated benchmark issue:

`Issue → Localization → Generated Patch → Patch Application → Sandbox Tests → Validation`

Verify whether the generated patch actually fixes the regression.

Do not claim success from unit tests alone.

## 10. Reliability
- Reuse Phase 2 caches/results.
- Limit patch size and candidate count.
- Add model-generation and validation timeouts.
- Fail cleanly when the model is unavailable.

## 11. Security
Do not:
- weaken container isolation
- enable unrestricted network during test execution
- broadly mount the host project
- expose Docker socket
- forward secrets
- execute generated code on the host
- add GitHub write credentials

## 12. Out of Scope
Do not implement:
- GitHub PR creation/pushing
- frontend redesign
- Phase 4
- new vector database
- new sandbox architecture

## Definition of Done
- Issue enters fix-generation pipeline.
- Phase 2 localization feeds generation.
- Model produces a patch.
- Patch runs only in isolated workspace.
- Existing sandbox validates it.
- Results are persisted and exposed through API.
- Multiple bounded candidates are supported.
- Tests pass.
- At least one real benchmark issue is verified end-to-end.
- No Phase 4 functionality is implemented.
