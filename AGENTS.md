# AGENTS.md — XINGESTIONV2 Standing Contract

## Purpose

XINGESTIONV2 is the **core production X/Twitter ingestion repository**. It owns the complete ingestion system around X acquisition: stable capabilities, durable control plane, production session/network allocation, raw evidence storage, canonical data, monitoring, analytics, APIs and eventual parent/NOS integration.

In this project, `XINGESTIONV2` means the maintained repository at
`techtiesai-png/XINGESTIONV2`. Its `main` branch is the canonical integration
branch. The repository was historically forked from
`Pruthavirajsingh/XINGESTIONV2`, but that original/upstream repository is not an
active project repository and must not be searched, modified, pushed to or
treated as a source of project truth unless the user explicitly requests an
upstream comparison.

A separate repository, `techtiesai-png/X-rev-os`, owns specialist X protocol research/reverse engineering and the canonical X-specific executable protocol runtime. The two repositories must remain versioned, documented and cross-referenced rather than drifting into duplicate protocol implementations.

Engineering decisions must favor clear interfaces, reproducibility, auditability, recoverability, security, failure isolation and measurable production behavior rather than demo-only shortcuts.

The subsystem must remain adaptable, horizontally scalable and embeddable in a larger ingestion platform. Public serious X/web-data providers may be used as capability/service-shape benchmarks; never claim knowledge of their private backend architecture.

---

## Required companion documents

Before changing architecture or runtime behavior, read these together:

1. `AGENTS.md` — durable rules, ownership and engineering discipline.
2. `architecture.md` — target whole-system architecture and failure-isolation boundaries.
3. `protocol-integration.md` — authoritative XINGESTIONV2 ↔ X-rev-os runtime/ownership contract.
4. `plan.md` — current implementation roadmap and acceptance gates.
5. `implemented.md` — factual implementation/verification/update ledger.

When protocol-specific implementation/research is involved, also read the corresponding standing docs in `techtiesai-png/X-rev-os`:

```text
AGENTS.md
architecture.md
plan.md
implemented.md
```

Do not let durable decisions exist only in ChatGPT/Codex history. Material answers that change architecture, contracts, implementation order, integration, validation or unresolved dependencies must be written into repository documentation.

---

## Core repository relationship

### XINGESTIONV2 is the integration anchor

This repository owns the whole production ingestion system and keeps cross-system architecture bonded together.

It owns:

- canonical product/public `CapabilitySpec` and capability-contract version;
- durable tasks, PostgreSQL task ledger and transactional outbox;
- delivery bus/Redis integration;
- worker execution leases/fencing/crash recovery;
- **all production retry/backoff policy**;
- production account/session pools and session leasing;
- production proxy/network allocation;
- production raw-evidence storage and retention;
- canonical entities, relationship edges and time-varying observations;
- production normalization/reprocessing;
- monitor/subscription scheduling, coalescing, gap recovery and backfills;
- analytics/alerts/briefs as downstream consumers;
- northbound APIs and parent/NOS integration;
- production capacity/deployment/multi-region decisions;
- the exact X-rev protocol release pinned in production.

### X-rev-os is the canonical X-specific protocol authority

`techtiesai-png/X-rev-os` owns:

- authorized browser/network protocol observation;
- protocol research evidence;
- `ProtocolCapabilityBinding` from XINGESTION capability IDs to protocol recipes;
- X acquisition recipes and operation definitions;
- X request construction;
- X-specific parser implementations;
- X pagination interpretation;
- X auth/session attachment semantics;
- X client-transaction/shared request-metadata algorithms;
- X feature/config/client-profile bundles;
- protocol-specific typed errors;
- fixtures/corpus and recipe-level validation;
- protocol runtime + bundle/release exports;
- deep protocol drift/candidate research and future bounded protocol-healing logic.

There must be **one canonical implementation of X-specific protocol behavior: X-rev-os**.

Do not independently rebuild the same parser/request/pagination/transaction implementation inside XINGESTIONV2 after the integration boundary is established.

Production must never require the X-rev research browser, local capture folders, research database or Workbench UI to be online.

---

## Primary architecture mission

The system is capability-driven.

Stable contracts describe **what data is required**; X operation IDs, request shapes, feature flags, parser versions and browser mechanics stay below that boundary.

The canonical machine-readable capability artifact is
`xingestion/contracts/capabilities.v1.json`. Version it deliberately, ship it
with the package, and keep it protocol-neutral. Durable capability tasks use
`CAPABILITY_REQUEST`; legacy task shapes may only enter through explicit,
tested compatibility translators.

The long-term primary acquisition path must be first-party owned by the project ecosystem through the X-rev runtime, not fundamentally depend on Twikit/twscrape.

Third-party X libraries may be used as:

- research/reference implementations;
- migration comparison paths;
- fixture/behavior references;
- temporary compatibility/fallback paths when equivalence is actually validated.

They must not be the single protocol authority or unavoidable long-term dependency for required capabilities.

Protocol self-healing is staged and evidence-based. Do not equate it with arbitrary agent/LLM code rewriting directly into production.

---

## Architecture invariants

### Capability layer

- canonical capability contracts live in XINGESTIONV2;
- contracts contain no Twikit types, browser selectors, X query IDs/operation IDs or provider-specific URLs;
- contracts define typed inputs/outputs, fidelity, freshness, pagination semantics and provenance requirements;
- provider APIs are completeness references, not canonical specifications.

### Control plane

- PostgreSQL remains the durable task-control source of truth unless measured evidence justifies migration;
- Redis Streams/delivery infrastructure is replaceable and not a business-logic authority;
- queue/task code must not import X-specific protocol implementation;
- task identity, lease/fencing, retry, dead-letter and replay invariants remain independent of acquisition implementation;
- XINGESTIONV2 owns production retries; X-rev exported runtime performs zero automatic retries.

### Protocol/runtime boundary

- X-specific executable behavior comes from a pinned X-rev runtime/protocol release;
- XINGESTIONV2 maps canonical capabilities to validated X-rev `ProtocolCapabilityBinding`/recipe revisions;
- XINGESTIONV2 selects/leases sessions and network routes;
- X-rev owns how supplied session context is attached to protocol requests;
- XINGESTIONV2 injects the production `RawEvidenceSink`;
- runtime/bundle/capability-contract versions are pinned through an exact release manifest/checksum, never floating `latest` research state.

### Data plane

- preserve raw acquisition evidence/provenance sufficiently to reparse/re-normalize without recollection where retention allows;
- raw production evidence belongs to XINGESTIONV2 storage, not X-rev research paths;
- separate raw acquisitions, canonical entities, relationship edges, observations and derived analytics;
- platform object IDs are identity; content hashes are integrity/similarity tools, not destructive identity merging;
- engagement counters/profile statistics are observations over time, not additive events;
- source-created, captured, first-seen, last-seen and updated times must remain distinct;
- every normalized object/observation should trace back to task/run, X-rev recipe/runtime revisions and raw evidence refs.

### Protocol intelligence

- production health telemetry is observed in XINGESTIONV2;
- deep reverse engineering/candidate validation happens in X-rev-os;
- research/discovery tooling is not required for stable production collection;
- an unvalidated candidate cannot silently replace an approved production route;
- production may later fail over to a previously approved compatible alternate;
- unresolved drift should produce an investigation package/hand-off rather than make the queue reverse-engineer X inline.

### Downstream/integration boundary

- analytics, alerts and briefs are downstream consumers; their failure must not stop acquisition;
- parent-system integration uses versioned contracts, not internal queue/table knowledge;
- mutating account actions are outside the ingestion core and require a separately authorized plane if ever introduced.

---

## Failure-isolation rule

The target is not that nothing breaks. X will change.

The requirement is: **a failure should have the smallest blast radius permitted by its actual dependency graph.**

Examples:

- one X operation/recipe degrades -> only dependent capabilities/routes degrade, not the control plane;
- parser breaks -> raw payload remains reprocessable;
- Twikit breaks -> approved first-party X-rev routes continue;
- browser research tooling breaks -> protocol discovery pauses, stable production routes continue;
- one session fails -> other valid sessions continue where the capability permits;
- analytics breaks -> acquisition continues;
- Redis fails -> durable task/outbox truth remains in PostgreSQL and can be redriven;
- bad candidate -> current approved production release remains available;
- shared auth/transaction mechanism changes -> all actual dependents may degrade together and should be diagnosed as one shared dependency, not falsely isolated per endpoint.

Treat avoidable blast-radius expansion as an architectural defect.

---

## Correctness and reliability

- never mark work complete because it compiles or looks plausible;
- trace state transitions end-to-end: capability request -> task -> delivery -> lease -> session/network allocation -> X-rev runtime/recipe -> raw persistence -> production normalization -> downstream consumers;
- prefer explicit invariants enforced by schemas/transactions/contracts over comments;
- idempotency, deduplication, retry behavior, pagination/checkpointing and crash recovery must be designed/tested;
- queue consumption must survive worker crashes without losing acknowledged work;
- leases need unique ownership/fencing semantics and reclaim behavior;
- retries require classification, bounded backoff/jitter, durable scheduling and generation fencing;
- dead letters retain history; replay is selective/auditable and protected against poison loops;
- graceful shutdown stops new leases and safely completes/returns in-flight work;
- do not silently swallow malformed records or infrastructure failures.

---

## Sessions and secrets

- never commit passwords, cookies, session tokens, API keys, TOTP seeds, proxy credentials or equivalent secrets;
- account identity, long-lived credential reference, session artifact, session health and session lease are distinct concepts;
- do not reuse one database field for incompatible cookies/credentials representations;
- long-lived secrets belong behind a `SecretStore`/encrypted-secret boundary with least privilege;
- X-rev `SessionContext` receives only ephemeral material required for protocol execution;
- never emit secret values in logs, fixtures, research captures or investigation packages.

---

## Testing and verification

- unit-test deterministic logic and integration-test PostgreSQL/Redis/session/runtime transitions;
- X protocol operations/parsers need X-rev fixture/contract tests independent of live X availability;
- live protocol validation is explicitly gated and requires authorized research identities/environment;
- add failure-injection tests for worker termination, Redis/DB interruption, duplicate delivery, session exhaustion, rate limits/auth failure, raw-storage failure, malformed payloads and protocol/runtime errors;
- never write `verified`, `validated`, `fixed` or `production-ready` in `implemented.md` unless the stated verification actually ran;
- if verification was impossible, record that and list the missing evidence.

---

## Observability

At minimum track:

- queue depth/age/outbox lag;
- tasks leased/completed/retried/dead-lettered/replayed;
- session availability/concurrency/cooldowns;
- capability success/failure by pinned X-rev release/recipe;
- protocol typed error distribution and scope hints;
- parser/pagination warnings and schema fingerprints;
- latency and throttling;
- raw/normalized processing lag;
- dedup/re-observation rate;
- approved release rollout/rollback/quarantine state;
- API/downstream health.

Logs must be structured, safely correlated and secret-free.

---

## Dependencies and deployment

- runtime dependencies must be declared and sufficiently pinned/locked;
- X-rev production runtime/bundle pair must be pinned through a release manifest/checksum;
- development and production configuration must be explicit;
- empty service definitions, placeholder deployment files and invalid health checks are incomplete work;
- database migrations must be versioned and migration history auditable;
- CI should run relevant compilation/static checks/tests/migration validation/dependency-security checks;
- do not replace PostgreSQL/Redis or split everything into microservices for appearance; measure real bottlenecks first.

---

## Segmented implementation cadence

Work in small coherent implementation segments with one primary engineering objective and bounded files/state transitions/tests/docs.

For each XINGESTIONV2 segment:

1. read `AGENTS.md`, `architecture.md`, `protocol-integration.md`, the relevant `plan.md` segment and current `implemented.md`;
2. read X-rev docs if the segment touches protocol/runtime integration;
3. re-read current code/interfaces rather than implementing from stale assumptions;
4. research unstable external/library/protocol behavior when material;
5. cross-check data semantics, blast radius, scaling, security and parent integration;
6. implement one coherent production-quality slice;
7. perform the strongest available verification;
8. update `plan.md` and `implemented.md` in the same coherent change set when practical;
9. update architecture/integration docs if a durable contract changed;
10. stop/report before unrelated next-segment work unless explicitly requested.

A segment report should state what was implemented, important decisions, what actually ran, uncertainty/blockers, cross-repo impact and next recommended step.

---

## Documentation discipline

Documentation is part of completion.

### Primary roles

- `AGENTS.md` — durable project rules/ownership/cadence.
- `architecture.md` — whole-system target architecture.
- `protocol-integration.md` — authoritative X-rev cross-repository boundary.
- `plan.md` — current roadmap/status/acceptance gates.
- `implemented.md` — factual update/verification ledger.

### Required behavior

- whenever a durable rule/constraint changes, update `AGENTS.md`;
- whenever target architecture changes, update `architecture.md` or the specific authoritative integration document;
- whenever implementation order/status/dependencies change, update `plan.md`;
- whenever meaningful work/research/verification is completed, append `implemented.md` with exact evidence and limitations;
- when the X-rev integration contract changes, update both repositories' relevant docs/cross-links in the same workstream when access permits;
- once runtime integration exists, `implemented.md` must record the exact X-rev runtime/bundle/release/commit pinned by XINGESTIONV2;
- never leave the only record of an important architecture answer in chat history.

Codex may suggest ADRs, a generated docs index, machine-readable decision logs or another stronger documentation model. Do not silently replace this structure or create a parallel stale system. Explain the benefit, provide a migration path, preserve historical evidence/cross-links, and update the standing docs coherently if adopted.

---

## External/user-dependent items

Keep non-blocking dependencies recorded in `implemented.md`; ask only when one blocks safe progress.

Current categories include:

- authorized live X research environment;
- approved production secret backend;
- parent/NOS integration contract/auth;
- retention policy;
- production hardware/topology/SLOs;
- final multi-region strategy.

---

## Change discipline

- prefer coherent reviewable/revertible changes;
- avoid unrelated refactors inside correctness fixes unless required by a boundary;
- do not delete task/replay/audit/raw history without explicit retention policy;
- do not claim components exist when repository state contains placeholders/missing implementations;
- if docs conflict with executable behavior, executable behavior is current runtime reality and documentation drift is a defect to record/fix;
- if XINGESTIONV2 and X-rev docs conflict about protocol ownership/runtime integration, `protocol-integration.md` plus the current X-rev standing docs must be reconciled before implementation rather than choosing one silently.
