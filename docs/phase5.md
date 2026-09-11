# PHASE 5 — COMPLETE PRODUCT & END-TO-END HARDENING

## Objective

Connect all phases into one reliable user-facing workflow and make the system production-quality for the project's intended scope.

## Complete Workflow

The final system should support:

Issue
→ Validate benchmark
→ Localize repository
→ Generate fix
→ Validate fix
→ Commit
→ Draft PR

The frontend should expose the workflow clearly where required.

## Frontend Integration

Only now connect the existing frontend to the completed backend workflow.

Show:
- issue selection
- validation state
- localization results
- generated patch
- test/validation results
- candidate attempts
- commit state
- draft PR state
- errors

Do not redesign the frontend unnecessarily.

## Reliability

Add:
- clear state transitions
- failure recovery
- bounded retries
- timeouts
- useful error messages
- persistent run/attempt state
- reproducible results

No infinite background jobs.

## Security Audit

Review the entire workflow for:

- sandbox escape risks
- host filesystem access
- Docker socket exposure
- secret leakage
- GitHub credential exposure
- unsafe generated patches
- unrestricted test-time network access
- command injection
- path traversal

Do not weaken existing Phase 1 security controls.

## Performance

Verify:
- repository indexing cache
- embedding cache
- localization reuse
- no unnecessary model calls
- bounded repository/test execution
- bounded candidate generation

Measure major bottlenecks rather than optimizing blindly.

## Testing

Run:
- unit tests
- integration tests
- API tests
- sandbox tests
- localization evaluation
- fix-generation tests
- Git/PR tests
- end-to-end tests

Add regression tests for every bug discovered during development.

## Final Benchmark Evaluation

Run the complete system against the selected validated benchmark set.

Report:
- localization Top-1/Top-3
- number of generated candidates
- number of validated fixes
- success rate
- failure categories
- average generation time
- average validation time
- PR creation success where applicable

Do not hide environment failures; distinguish them from model failures.

## Definition of Done

The project has a verified end-to-end workflow:

SWE-bench Issue
→ Repository Validation
→ Code Localization
→ Fix Generation
→ Sandbox Validation
→ Git Commit
→ Draft GitHub PR

with tests, security controls, persistence, error handling and benchmark results.
