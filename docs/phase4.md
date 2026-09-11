# PHASE 4 — GIT & DRAFT PULL REQUEST

## Objective

Turn a successfully validated fix into a reproducible Git commit and draft GitHub PR.

## Preconditions

Only allow PR creation when:

- fix generation succeeded
- patch applied successfully
- required tests passed
- validation succeeded

Never create a PR for an unvalidated candidate.

## Git Workflow

Create an isolated branch from the exact benchmark base commit.

Apply the validated patch.

Run final verification.

Create a Git commit containing:
- appropriate commit message
- issue reference where appropriate

Do not modify the user's main working branch.

## GitHub

Use GitHub CLI/API through authenticated credentials.

Create a draft PR.

PR should contain:
- meaningful title
- issue description/context
- summary of changes
- tests run
- validation result
- benchmark issue reference
- base repository/commit information where useful

Do not merge automatically.

Do not push anything unless the user has explicitly configured/authenticated GitHub access for the workflow.

## Security

- Never store GitHub tokens in source code.
- Never log secrets.
- Never expose credentials to the sandbox.
- Generated repository code must not receive GitHub credentials.
- Keep sandbox network restrictions unchanged.

## API

Add endpoints necessary to:

- create branch/commit
- create draft PR
- retrieve PR status

Return useful errors for:
- missing GitHub authentication
- permission failures
- branch conflicts
- commit failures
- PR creation failures

## Tests

Mock GitHub operations.

Test:
- successful commit
- validation gating
- branch isolation
- authentication failure
- Git failure
- PR creation failure
- successful draft PR response
- secret handling

## End-to-End Verification

With a safe test repository or explicitly configured GitHub test target, verify:

validated fix
→ branch
→ commit
→ draft PR

Do not automatically merge.

## Definition of Done

A successfully validated benchmark fix can be converted into a reproducible commit and draft PR without compromising credentials or sandbox security.
