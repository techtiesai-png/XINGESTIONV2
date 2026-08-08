# AGENTS.md

## Purpose

This file is the standing operating contract for work on this repository.

XINGESTIONV2 is an independently developed research subsystem for a government-related research context. It is being developed separately now and is expected to be integrated later into a substantially larger ingestion system. Engineering decisions must favor clear interfaces, reproducibility, auditability, recoverability, security, and measurable production behavior rather than demo-only shortcuts.

The subsystem must remain adaptable, malleable, horizontally scalable, and failure-isolated. Its architectural ambition can use the public service characteristics of serious X/web-data providers as a benchmark, but never claim knowledge of a provider's private backend design.

## Primary architectural mission

The long-term primary X acquisition implementation must be **first-party owned by this project**.

Third-party X libraries such as Twikit/twscrape may be used as:

- research/reference implementations;
- migration comparison paths;
- fixture/behavior references;
- temporary compatibility/fallback adapters while first-party capabilities are being built.

They must not remain the single protocol authority or an unavoidable long-term runtime dependency for the required capability set.

The system is capability-driven. Stable contracts describe **what data is required**; X operation IDs, request shapes, parsers, third-party library calls and browser mechanics stay below that boundary.

Protocol self-healing must be staged and evidence-based: observe -> classify -> route to already validated alternatives -> discover candidates -> validate -> canary -> promote/rollback. Do not equate self-healing with arbitrary agent/LLM code rewriting directly into production.

## Required companion documents

Before changing the repository, read these together:

- [`architecture.md`](./architecture.md) — authoritative target architecture, module boundaries, failure-isolation rules, and self-healing model.
- [`plan.md`](./plan.md) — authoritative implementation roadmap, segment boundaries, dependencies and acceptance gates.
- [`implemented.md`](./implemented.md) — factual implementation/verification ledger and external/user-dependent items.

The documents have distinct jobs:

1. `AGENTS.md` defines durable project rules and intent.
2. `architecture.md` defines what the finished system should look like and what may depend on what.
3. `plan.md` defines the migration/implementation sequence.
4. `implemented.md` records what actually exists and what was actually verified.

Whenever the user supplies a durable new project rule, constraint, integration assumption, or engineering preference, update this file if it should govern future work.

Whenever implementation changes the state of a planned item, update `plan.md` and `implemented.md` in the same coherent change set whenever practical.

## Core engineering directive

Do not dumb down, cripple, stub out, or deliberately make the system non-functional merely to make the code easier to maintain or because the collection problem is operationally difficult.

Preserving an existing approach is not the goal. If a better architecture, protocol representation, queue, storage model, deployment method, API contract, testing strategy, or implementation exists, prefer the stronger design and migrate deliberately.

The target is the strongest reasonably production-ready implementation for the stated research purpose, subject to actual technical, security, legal, and platform constraints. Document material constraints explicitly; do not silently replace real functionality with mock-only behavior or a no-op and describe that as a fix.

## Research and collection context

- Treat this repository as research infrastructure, not as a consumer automation toy.
- The system is intended to study public social-platform data collection, observable protocol behavior, protocol drift, and the architecture required to operate a large resilient ingestion service.
- Do not remove useful research functionality solely because a platform Terms-of-Service concern exists. Separate engineering facts from compliance questions and document the latter where they affect deployment.
- Do not assume an existing open-source implementation is the correct architecture merely because it currently works.
- Reference libraries are research inputs, not automatically code to copy. Record source/version/license and distinguish observed protocol facts, independently implemented behavior, and code whose license permits incorporation.
- Research context does not remove the need to protect credentials, respect applicable law and authorization boundaries, or avoid unsafe secret handling.

## Architecture invariants

### Capability Plane

- Stable capability contracts define data needs such as search, replies, timelines, followers and monitoring.
- Capability contracts must not contain Twikit types, browser selectors, X query IDs/operation IDs, or provider-specific route names.
- Provider APIs are completeness references, not the canonical internal specification.

### Control Plane

- PostgreSQL remains the durable control-plane source of truth unless measured evidence justifies migration.
- Delivery infrastructure is replaceable; Redis Streams is the current implementation, not a business-logic dependency.
- Queue/task code must not import X protocol code.
- Task identity, lease, retry, dead-letter and replay invariants remain independent of acquisition implementation.

### Protocol Plane

- X protocol knowledge is represented through versioned operation definitions, first-party transport/request code, parsers and fixtures.
- Operation versions are explicit and should become immutable after stable promotion.
- Multiple validated plans/operation versions may implement one capability.
- Browser execution is a separate acquisition/observation mode, not the default high-throughput path when a validated direct protocol operation exists.

### Intelligence Plane

- Research/discovery tooling must not be required for normal stable collection.
- Stable/candidate/canary/degraded/quarantined/retired states are explicit.
- A candidate cannot silently replace stable production behavior.
- Promotion/rollback must be testable and auditable.
- If repair is not safely automatable, generate an investigation package rather than guessing.

### Data Plane

- Preserve raw acquisition evidence/provenance sufficiently to reparse/re-normalize without recollection when retention permits.
- Separate raw acquisitions, canonical entities, relationship edges, engagement/profile observations and derived analytics.
- Platform object IDs are identity. Content hashes are similarity/integrity tools, not destructive identity merging.
- Engagement counters are observations over time, not additive events.
- Time semantics must be explicit: source-created, captured, first-seen, last-seen and updated times must not be conflated.
- Every normalized object/observation should be traceable back to task/run, acquisition plan, operation/parser version and raw payload reference/hash.

### Downstream / integration boundary

- Analytics, alerts and briefs are downstream consumers; their failure must not stop acquisition.
- Parent-system integration uses versioned contracts rather than internal queue/table knowledge.
- Mutating account actions are not part of the ingestion core; if ever needed, isolate them in a separately authorized Action Plane.

## Failure-isolation rule

The target is not a system where nothing ever breaks. X will change.

The requirement is that **the thing that changed is the thing that breaks**.

Examples:

- one X operation changes -> one capability/version degrades, not the queue;
- one parser breaks -> raw payload remains reprocessable;
- Twikit breaks -> first-party stable operations continue after cutover;
- browser observation breaks -> discovery pauses, stable operations continue;
- one session fails -> other valid sessions/capabilities continue;
- analytics breaks -> acquisition continues;
- Redis fails -> durable tasks/outbox remain in PostgreSQL;
- candidate repair is wrong -> candidate/canary is quarantined, stable stays available.

Treat violations of these blast-radius expectations as architectural defects.

## Production-quality rules

### Correctness first

- Never mark work complete because it merely compiles or looks plausible.
- Trace state transitions end to end: capability request -> task -> delivery -> lease -> acquisition plan -> session -> protocol -> raw persistence -> normalization -> downstream consumers.
- Prefer explicit invariants enforced by schemas/transactions/contracts over comments describing intended behavior.
- Idempotency, deduplication, retry behavior, pagination/checkpointing and crash recovery must be designed and tested.
- Do not silently swallow malformed records or infrastructure failures without measurable accounting.

### No capability regression

Before replacing an implementation, identify what capability it currently provides, including edge cases and failure behavior. The replacement must preserve required behavior or explicitly document a deliberate change in `plan.md` and `implemented.md`.

Mock mode is for testing. It must not become the only path that works.

### Sessions and secrets

- Never commit real passwords, cookies, session tokens, API keys, TOTP seeds, proxy credentials or equivalent secrets.
- Account identity, session state, session leases and long-lived credentials are different concepts.
- Do not use one database field for incompatible representations such as both cookies and account credentials.
- Long-lived credentials must sit behind a `SecretStore`/encrypted-secret boundary with least-privilege access.
- Never emit secret values in logs, fixtures, research captures or investigation packages.

### Reliability

- Queue consumption must survive worker crashes without losing acknowledged tasks.
- Leases require owner/expiry/fencing semantics or an equivalent reclaim mechanism.
- Retries require classification, bounded exponential backoff with jitter, durable scheduling and generation fencing.
- Dead letters retain history; replay is selective, auditable and protected against poison loops.
- Graceful shutdown stops new leases and safely completes/returns in-flight work.

### Testing and verification

- Unit-test deterministic logic and integration-test PostgreSQL/Redis/session/protocol state transitions.
- Protocol operations/parsers need fixture/contract tests independent of live X availability.
- Live protocol tests are explicitly gated and require authorized research identities/environment.
- Add failure-injection tests for worker termination, Redis/DB interruption, malformed payloads, session exhaustion, rate limit/auth failures, duplicate delivery, protocol drift, parser drift, candidate failure and replay.
- Never write `verified`, `fixed` or `production-ready` in `implemented.md` unless the stated verification actually ran.
- If execution was impossible, record that and list missing verification.

### Observability

At minimum track:

- queue depth/age/outbox lag;
- tasks leased/completed/retried/dead-lettered/replayed;
- session availability/concurrency/cooldowns;
- acquisition success/failure by capability/operation/version;
- parser/schema drift;
- latency and throttling;
- raw/normalized processing lag;
- dedup/re-observation rate;
- candidate/canary/promotion/rollback state;
- API/downstream health.

Logs must be structured and include safe correlation identifiers. Never log credential material.

### Dependencies and deployment

- Runtime dependencies must be declared and pinned/locked sufficiently for reproducible builds.
- Development and production configuration must be explicit.
- Empty service definitions, placeholder deployment files and invalid health checks are incomplete work.
- Database migrations must be versioned/repeatable.
- CI should run relevant compilation/static checks/tests/migration validation/dependency-security checks.
- Do not replace PostgreSQL/Redis or split everything into microservices merely to look enterprise-grade. Measure bottlenecks and operational boundaries first.

## Segmented implementation cadence

Do not attempt the entire roadmap or a broad collection of unrelated fixes in one uninterrupted implementation run.

Work is divided into **small coherent segments**. A segment normally has one primary engineering objective and a bounded set of directly related files, state transitions, tests and documentation. The user prefers short focused implementation passes rather than long continuous runs. Treat this as a scope constraint, not permission to reduce reasoning depth.

For each segment:

1. Read `AGENTS.md`, `architecture.md`, and the exact `plan.md` segment.
2. Re-read current code/interfaces; do not implement from stale assumptions.
3. Research current external/library/protocol behavior when unstable or material.
4. Cross-check architecture, data semantics, failure blast radius, scaling, security and parent integration.
5. Implement one coherent production-quality slice; do not cut through an invariant merely to keep it artificially small.
6. Perform the strongest available verification.
7. Update `plan.md` status and `implemented.md` evidence.
8. Stop and report before beginning the next segment unless the user explicitly requests otherwise.

The report should state concisely:

- what was implemented;
- important design decisions;
- what was actually tested;
- what remains uncertain/blocking;
- the next recommended segment.

Segmenting work must not mean weaker reasoning, skipped research, reduced error handling, temporary architecture that will obviously be discarded, or superficial patches.

## External/user-dependent items

Keep non-blocking dependencies recorded in `implemented.md`. Ask the user only when a segment cannot safely proceed without one of them.

Current categories include:

- authorized live X research environment;
- production secret backend;
- parent ingestion integration contract/auth;
- retention policy;
- target production hardware/topology/SLOs;
- final multi-region strategy.

## Change discipline

- Prefer coherent changes that can be reviewed/reverted independently.
- Avoid unrelated refactors inside correctness fixes unless required to preserve a boundary.
- Do not delete audit/history as a replay/cleanup side effect without an explicit retention policy.
- Do not claim components exist when the repository contains placeholders/missing files.
- If documentation conflicts with executable behavior, executable behavior is current reality and documentation drift is a defect to record.
