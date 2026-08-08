# AGENTS.md

## Purpose

This file is the standing operating contract for work on this repository.

XINGESTIONV2 is an independently developed research subsystem for a government-related research context. It is being developed separately now and is expected to be integrated later into a substantially larger system. Engineering decisions should therefore favor clear interfaces, reproducibility, auditability, security, recoverability, and production-grade behavior rather than demo-only shortcuts.

## Required companion documents

Before changing the repository, read these files together:

- [`plan.md`](./plan.md) — authoritative engineering backlog, architecture findings, priorities, acceptance criteria, and planned remediation.
- [`implemented.md`](./implemented.md) — factual implementation ledger of changes that have actually been made and what was actually verified.

The three documents have distinct jobs:

1. `AGENTS.md` defines the durable rules and project intent.
2. `plan.md` defines what is wrong, what should change, why, priority, dependencies, and completion criteria.
3. `implemented.md` records only completed work, including verification performed and remaining limitations.

Whenever the user supplies a durable new project rule, constraint, integration assumption, or engineering preference, update this file if it should govern future work.

Whenever implementation changes the state of a planned item, update `plan.md` and `implemented.md` in the same change set whenever practical.

## Core engineering directive

Do not dumb down, cripple, stub out, or deliberately make the system non-functional merely to make the code easier to maintain or because the existing collection problem is operationally difficult.

Preserving an existing approach is also not the goal. If a better architecture, protocol, queue, storage model, deployment method, API contract, testing strategy, or implementation exists, prefer the stronger design and migrate deliberately.

The target is the strongest reasonably production-ready implementation for the stated research purpose, subject to actual technical, security, legal, and platform constraints. When such a constraint matters, document it explicitly; do not silently replace real functionality with mock-only behavior or a no-op and describe that as a fix.

## Research and collection context

- Treat this repository as research infrastructure, not as a consumer automation toy.
- The system is intended to study public social-platform data collection and the architecture used by large third-party data providers.
- Do not remove useful research functionality solely because a platform Terms-of-Service concern exists. Separate engineering facts from policy/compliance questions and document the latter where they affect deployment.
- Do not assume the existing implementation is the correct way to preserve capability. A more reliable supported interface, approved data source, adapter design, or better collection architecture may replace an existing mechanism when it improves the system.
- Research context does not remove the need to protect credentials, respect applicable law and authorization boundaries, or avoid unsafe secret-handling practices.

## Production-quality rules

### Correctness first

- Never mark work complete because it merely compiles or looks plausible.
- Trace state transitions end to end: producer -> queue -> lease -> collection -> validation -> persistence -> analytics -> API -> retry/dead-letter/recovery.
- Prefer explicit invariants enforced by schema constraints and transactions over comments describing intended behavior.
- Idempotency, deduplication, retry behavior, and crash recovery must be designed, not assumed.
- Do not silently swallow malformed records or infrastructure failures without measurable accounting.

### No capability regression

Before replacing an implementation, identify what capability it currently provides, including edge cases and failure behavior. The replacement must preserve required behavior or explicitly document a deliberate behavior change in `plan.md` and `implemented.md`.

Mock mode is for testing. It must not become the only path that works.

### Architecture

- Keep source/platform collection behind an adapter boundary so the rest of the system is not coupled to one library or transport.
- Keep queue/control-plane state authoritative and recoverable. Avoid split-brain state across PostgreSQL and Redis.
- Separate immutable/raw observations, canonical entities, engagement snapshots, analytical rollups, and generated intelligence when their semantics differ.
- Preserve provenance: source, query/task, collection time, platform object identifier, collector version, and relevant run identifiers should be recoverable.
- Design integration boundaries so this subsystem can later be embedded into a larger platform without rewriting core business logic.

### Secrets and identities

- Never commit real passwords, cookies, session tokens, API keys, TOTP seeds, proxy credentials, or equivalent secrets.
- Do not use one database column for incompatible representations such as both session cookies and account credentials.
- Prefer a secret-store reference or encrypted credential envelope with least-privilege access for long-lived secrets.
- Never emit secret values in logs, errors, fixtures, or implementation documentation.

### Data and analytics

- Platform object IDs are identity. Content hashes are useful for similarity/content grouping but must not silently merge distinct source objects unless that behavior is explicitly intended.
- Engagement counters are observations over time, not additive events. Re-observation must not inflate totals.
- Analytics must be derived idempotently from well-defined observations or rollups.
- Time semantics must be explicit: source-created time, first-seen time, captured time, last-seen time, and updated time must not be conflated.

### Reliability

- Queue consumption must survive worker crashes without losing tasks.
- Leases must have owner/expiry semantics or an equivalent reclaim mechanism.
- Retries require classification, bounded exponential backoff with jitter, and durable scheduling.
- Dead letters must retain history; replay must be selective, auditable, and protected against infinite poison-message loops.
- Graceful shutdown must stop new leases, finish or safely return in-flight work, and close all external clients.

### Testing and verification

- Add unit tests for deterministic logic and integration tests for PostgreSQL/Redis state transitions.
- Collection adapters should have fixture/contract tests independent of live platform availability. Live integration tests, when used, must be explicitly gated.
- Add failure-injection tests for worker termination, Redis interruption, database interruption, malformed payloads, rate-limit/auth failures, duplicate delivery, and replay.
- Never write "verified", "fixed", or "production-ready" in `implemented.md` unless the stated verification was actually performed.
- If execution was not possible in the current environment, record that fact and list the missing verification.

### Observability

Every important state transition should be measurable. At minimum track queue depth/age, tasks leased/completed/retried/dead-lettered, collection success/failure by class, account/session health, throttling/rate-limit responses, database latency/errors, ingestion throughput, deduplication/re-observation rate, analytics lag, and API health/latency.

Logs should be structured and include safe correlation identifiers such as task ID, run ID, worker ID, adapter, and token/account database ID. Never log credential material.

### Dependencies and deployment

- Runtime dependencies must be declared and version-pinned/locked sufficiently for reproducible builds.
- Development and production configuration must be explicit; no production service should depend on undocumented local files.
- Empty service definitions, placeholder deployment files, and invalid health checks are incomplete work, not production scaffolding.
- Database migrations must be versioned and repeatable.
- CI should run formatting/linting, type/static checks where useful, tests, migration validation, and dependency/security checks.

## Implementation workflow

For each meaningful implementation batch:

1. Read `AGENTS.md` and relevant items in `plan.md`.
2. Re-check the current code; do not implement from stale assumptions.
3. Update the plan if discovery changes the diagnosis or recommended architecture.
4. Implement the smallest coherent production-quality slice, not a superficial patch.
5. Run the strongest available verification for that slice.
6. Update `plan.md` status/notes.
7. Append a dated entry to `implemented.md` containing:
   - plan item(s),
   - files changed,
   - behavior changed,
   - migrations/config implications,
   - tests/checks actually run,
   - known limitations or follow-up work.
8. Keep documentation consistent with actual code.

## Change discipline

- Prefer coherent changes that can be reviewed and reverted independently.
- Avoid unrelated refactors inside a correctness fix unless required to make the fix safe.
- Do not delete historical/audit data as a side effect of replay or housekeeping without an explicit retention policy.
- Do not claim architectural components exist when the repository contains placeholders or missing files.
- If documentation conflicts with executable code, treat executable behavior as current reality and record the documentation drift as a defect.
