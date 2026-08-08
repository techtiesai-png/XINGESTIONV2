# XINGESTIONV2 Implementation Plan

> Architecture was redesigned on 2026-08-08. Read [`architecture.md`](./architecture.md) and [`AGENTS.md`](./AGENTS.md) before implementation. Record only actually completed/verified work in [`implemented.md`](./implemented.md).

---

# 0. Mission after the architecture reset

The goal is no longer merely to harden a Twikit-based X scraper.

The target is an independently owned X data-ingestion subsystem with:

- stable capability contracts;
- a durable, horizontally scalable control plane;
- first-party ownership of the primary X protocol implementation;
- reference libraries used as research inputs rather than protocol authorities;
- raw immutable provenance;
- versioned protocol operations/parsers;
- protocol health/drift detection;
- staged candidate discovery, canary validation, promotion and rollback;
- bounded self-healing with explicit escalation when automatic repair is unsafe;
- clean integration into a larger ingestion system later.

Public platforms such as TwitterAPI.io are used as a **capability completeness benchmark**, not as a private architecture specification and not as a list of routes to clone one-for-one.

---

# 1. Status legend

- `[ ]` not started
- `[~]` partially implemented / migration in progress
- `[x]` implemented and verified to the level recorded in `implemented.md`
- `[D]` architecture/design decided but runtime implementation not complete
- `BLOCKED-USER` requires user/platform information before that specific item can be finalized

---

# 2. Work already completed and preserved

The architecture reset **does not discard Segments 1–3**.

## [x] Segment 1 — durable task control plane

Verified implementation includes:

- PostgreSQL authoritative task ledger;
- idempotency keys;
- transactional outbox;
- Redis Streams consumer groups;
- outbox claim leases;
- durable task state transitions;
- ACK only after durable state transitions;
- persistent local Redis configuration;
- migrations/CI baseline.

## [x] Segment 2 — worker lease/crash recovery

Verified implementation includes:

- task execution leases;
- lease heartbeats;
- Redis pending-entry idle refresh;
- stale-owner fencing;
- deterministic crash recovery tests;
- cancellation after durable lease loss.

## [x] Segment 3 — retry/dead-letter/replay lifecycle

Verified implementation includes:

- durable scheduled retries;
- delivery-generation rollover;
- stale-generation rejection;
- retry exhaustion;
- dead-letter archive;
- selective replay;
- replay lineage/audit;
- retry due-time verification.

These components become the **Control Plane** described in `architecture.md`.

---

# 3. Architecture reset — current documentation pass

## [D] Define target architecture

Deliverables:

- `architecture.md` — authoritative architecture and hard module boundaries;
- rewritten `plan.md` — segment-by-segment migration roadmap;
- updated `AGENTS.md` — durable first-party protocol-ownership rules;
- `implemented.md` entry recording that this pass changed architecture/docs only.

No runtime behavior should change in this architecture-reset pass.

---

# 4. Revised implementation roadmap

Because the scope now includes **first-party protocol ownership and protocol intelligence**, expect approximately **16 focused implementation segments remaining (Segments 4–19)**. Some can merge if implementation proves smaller; some may split if a correctness boundary requires it.

The segmentation is intentional: each segment should normally fit one bounded implementation/research/verification pass and leave the repository in a coherent state.

---

# PHASE A — Stable contracts before replacing the collector

## [ ] Segment 4 — Capability contracts and planner boundary

### Goal

Make the rest of the system request **capabilities**, not Twikit functions or X endpoint names.

### Build

- `Capability` catalog/enum/registry;
- typed `CapabilityRequest` models;
- typed canonical capability result/page models;
- cursor/checkpoint abstraction;
- fidelity/freshness/provenance requirements;
- `AcquisitionPlan` model;
- `CapabilityPlanner` interface;
- update worker task payload contract from `X_KEYWORD_SEARCH`-specific assumptions toward generic capability requests;
- compatibility adapter for existing search tasks so current tests keep working.

### Hard boundary

The capability layer must contain **zero** Twikit types, X query IDs, browser selectors, internal endpoint paths, or provider-specific URLs.

### Acceptance

- existing search workflow can be expressed as `SEARCH_TWEETS`;
- task/control-plane tests continue to pass;
- a fake second capability can be planned without modifying TaskRepository/Redis code;
- contract versioning tests exist.

### Isolation value

After this segment, changing how X search works should not require changing queue/task models.

---

## [ ] Segment 5 — Session, identity, budget and secret boundary

### Goal

Turn current token leasing into a proper Session Manager independent of protocol implementation.

### Build

- separate account identity metadata, session state and credential secret references;
- explicit session health state vs lease state;
- `SessionLease` contract;
- per-session `max_concurrency` enforcement tests;
- expired lease recovery;
- per-capability/operation usage budget/cooldown records;
- session affinity support for request chains;
- `SecretStore` interface;
- safe local-development SecretStore implementation;
- remove ambiguous "cookies OR credentials in token_value" semantics from new code paths;
- migration path from current `service_tokens` representation.

### User dependency

`SECRETS-01`: production secret backend choice is **not required** to build the interface/local implementation. Final production backend remains `BLOCKED-USER` until infrastructure choice exists.

### Acceptance

- multiple workers cannot exceed one session's configured concurrency;
- dead worker cannot strand a session;
- cooldown/refresh/quarantine are deterministic;
- capability planner can request an auth class without knowing credentials;
- no real secret appears in logs/tasks/tests.

### Isolation value

A session-management change does not change queue logic, protocol operation definitions, or normalized schemas.

---

## [ ] Segment 6 — First-party protocol foundation and raw envelope

### Goal

Create the first runtime layer that **we own** for speaking to known X protocol operations.

### Build

- `OperationDefinition` model;
- operation version/status registry (`CANDIDATE/CANARY/STABLE/DEGRADED/QUARANTINED/RETIRED`);
- first-party reusable HTTP/protocol client;
- supported timeout/connection pooling;
- session/cookie attachment via Session Manager;
- sanitized request fingerprinting;
- response schema fingerprinting;
- protocol error taxonomy;
- `RawAcquisitionEnvelope`;
- raw payload hash/reference abstraction;
- versioned parser interface;
- `XInternalWebAdapter` skeleton;
- retain `TwikitSearchAdapter` only as a transitional legacy/reference adapter.

### Important

This segment creates **protocol infrastructure**, not broad endpoint coverage.

### Acceptance

- fixture operation can execute through first-party transport;
- raw envelope includes operation/parser/session/task provenance;
- protocol transport is testable without Twikit;
- operation versions are immutable after stable promotion in tests;
- control-plane tests remain unchanged/passing.

### Isolation value

Protocol changes remain below CapabilityPlanner; data consumers receive a standardized raw envelope.

---

# PHASE B — Prove the architecture with one complete first-party capability

## [ ] Segment 7 — First-party `SEARCH_TWEETS` vertical slice

### Goal

Replace Twikit as the primary implementation for one important capability before expanding breadth.

### Research

- analyze current X search behavior using authorized observable interfaces;
- study relevant current open-source implementations as references;
- map request variables/features/auth requirements/pagination;
- create sanitized fixtures and provenance notes;
- distinguish independently observed protocol facts from incorporated code.

### Build

- stable `SEARCH_TWEETS` operation definition(s);
- first-party request builder;
- parser;
- cursor pagination;
- normalized tweet page output;
- explicit error mapping;
- legacy Twikit shadow/comparison mode during migration, not primary authority.

### Acceptance

- fixture/contract tests pass without Twikit;
- gated live validation path exists;
- pagination works across multiple pages in fixtures/live-gated tests;
- first-party result normalization satisfies Capability contract;
- Twikit can be disabled and non-live test suite remains functional.

### User dependency

`LIVE-X-01`: final live certification requires authorized research session/network environment. Implementation and fixture tests can proceed before that.

### Isolation value

When search protocol changes later, only search operation/parser/health state should need modification.

---

## [ ] Segment 8 — Raw data plane and normalization decoupling

### Goal

Stop making successful collection dependent on immediate canonical/analytics parsing.

### Build

- immutable raw acquisition record/storage abstraction;
- raw payload content addressing/hash;
- normalized object/event pipeline;
- explicit parser/normalizer versions;
- canonical entity + observation provenance;
- relationship/edge model foundation;
- reparsing/reprocessing job from raw payload;
- move analytics rollup side effects out of acquisition transaction where appropriate;
- preserve idempotency across reprocessing.

### Acceptance

- intentionally broken parser does not lose raw payload;
- repaired parser can regenerate normalized output from the same raw acquisition;
- acquisition task success semantics are explicitly defined relative to raw persistence/normalization;
- analytics failure cannot cause recollection of otherwise safely stored raw data.

### Isolation value

Parser/data-model bugs become replayable data-plane failures rather than collector failures.

---

# PHASE C — Protocol intelligence / staged self-healing

## [ ] Segment 9 — Protocol observation and research lab

### Goal

Create tooling that discovers/records protocol facts without being required for normal production collection.

### Build

- authorized browser network-capture workflow;
- capture sanitization/redaction;
- operation/capability correlation tooling;
- response schema fingerprint generation;
- fixture builder;
- historical operation diff tooling;
- reference-library analyzer/reporting workflow for Twikit/twscrape/other selected implementations;
- source/version/license provenance records;
- research artifact storage separate from stable registry.

### Acceptance

- research tooling can produce a candidate operation artifact from a controlled capture;
- secrets/cookies are redacted before persistence/reporting;
- research tooling failure does not affect stable protocol workers.

### Isolation value

The "reverse-engineering lab" can change aggressively without destabilizing production acquisition.

---

## [ ] Segment 10 — Capability/operation health and drift detection

### Goal

Know *what broke* before trying to heal it.

### Build

- scheduled protocol/capability probes;
- operation-health records;
- last-good/first-bad timestamps;
- error-rate/latency/parser-failure metrics;
- schema-fingerprint drift detection;
- pagination anomaly detection;
- session-local vs global failure classification;
- capability health aggregation;
- degraded/quarantine state transitions;
- alert/investigation trigger.

### Acceptance

Fixtures simulate:

- auth/session failure;
- global operation failure;
- response schema change;
- parser-only breakage;
- pagination breakage;
- transient transport outage.

Health engine must classify them differently.

### Isolation value

Broken protocol versions are marked unhealthy while queue/data planes remain intact.

---

## [ ] Segment 11 — Candidate registry, canary, promotion, rollback and self-heal L1–L3

### Goal

Safely route around known failures and promote validated replacements.

### Build

- candidate operation registry;
- validation state machine;
- fixture contract gate;
- gated live canary gate;
- stable-vs-candidate comparison;
- promotion audit;
- rollback;
- router selection among multiple stable versions;
- automatic failover to an already validated stable alternate;
- investigation package generator for unresolved drift.

### Non-goal

No arbitrary agent/LLM code rewriting directly into production.

### Acceptance

- broken stable A automatically routes to already-valid stable B;
- bad candidate cannot receive normal production traffic;
- successful canary can be promoted and rolled back;
- unresolved drift emits a complete machine-readable investigation package.

### Isolation value

Self-healing operates on protocol registry/routing rather than modifying queue, sessions or canonical schemas.

---

# PHASE D — Expand capability coverage only after one vertical slice + intelligence works

## [ ] Segment 12 — Core tweet capability family

Implement/verify, as observable and required:

- tweets by ID(s);
- tweet replies;
- reply sorting variants where supported;
- quotations;
- retweeters;
- thread context;
- article/detail expansion;
- advanced search variants.

Each capability requires:

- Capability contract;
- one or more versioned AcquisitionPlans;
- operation/parser fixtures;
- provenance;
- health probe;
- pagination tests where applicable.

Acceptance: failure of one tweet operation does not disable other tweet capabilities.

---

## [ ] Segment 13 — User/profile/timeline capability family

Implement/verify:

- user by ID/handle;
- batch user lookup where beneficial;
- user search;
- profile/about metadata;
- user timeline;
- tweets + replies timeline where distinct;
- mentions;
- checkpoint/pagination semantics.

Acceptance: user entity normalization is shared but operation versions remain independently degradable.

---

## [ ] Segment 14 — Social graph, lists and communities

Implement/verify:

### Graph

- followers with profiles;
- follower IDs/bulk edges;
- following;
- relationship lookup;
- graph-edge provenance/snapshot semantics.

### Lists

- timeline;
- members;
- followers;
- metadata where needed.

### Communities

- info;
- timeline/search;
- membership/edges where observable and required.

Acceptance: high-volume ID/edge acquisition uses a data model optimized for edges rather than pretending every result is a tweet document.

---

## [ ] Segment 15 — Monitoring, incremental ingestion and gap recovery

### Goal

Provide provider-like monitoring without implementing it as naive repeated ad-hoc scraping tasks.

### Build

- persistent monitor/subscription definitions;
- user/query monitor capability;
- cursor/checkpoint state;
- deduplicated incremental delivery;
- reconnect/backfill/gap detection;
- catch-up jobs after outage;
- monitor lag metrics;
- bounded schedule fanout.

Acceptance:

- stopping workers and restarting them does not create an unobservable collection gap;
- gap/backfill status is explicit;
- monitoring uses the same stable capability/protocol contracts as request/response ingestion.

---

# PHASE E — Platformization and integration

## [ ] Segment 16 — Northbound API and parent-system integration contract

### Goal

Expose the ingestion subsystem without leaking its internal PostgreSQL/Redis/protocol layout.

### Build

- versioned Capability API/request contract;
- asynchronous job submission/status where appropriate;
- synchronous bounded read paths where appropriate;
- cursor/page contracts;
- parent-ingestion event/export contract;
- correlation/run IDs;
- stable error taxonomy;
- compatibility/versioning rules;
- liveness/readiness.

### User dependency

`PARENT-01` and `AUTH-01` become blocking only for the final parent-specific transport/auth implementation. Provider-neutral contracts can be built first.

Acceptance: parent client does not know Redis stream names, task tables, X operation IDs, or parser versions unless provenance is explicitly requested.

---

## [ ] Segment 17 — Decouple analytics, alerts and briefs from acquisition

### Goal

Turn existing analytics features into downstream consumers rather than ingestion side effects.

### Build

- normalized event/object consumption boundary;
- idempotent trend/rollup rebuild;
- correct window semantics;
- alert dedupe/state/resolution;
- provider-neutral brief generator;
- evidence/source references;
- prompt/data boundary;
- external model calls outside DB leases;
- correct API semantics over downstream models.

Acceptance:

- analytics or brief outage cannot stop acquisition;
- analytics can be rebuilt from normalized/raw data;
- alerts and briefs are reproducible from evidence windows.

---

## [ ] Segment 18 — Operability, security, deployment and retention

### Build

- structured metrics/tracing;
- capability/operation/session dashboards;
- admin/operator health endpoints;
- operation promotion/quarantine audit UI/API where useful;
- application Docker image(s);
- production Compose/Kubernetes/systemd decision based on target environment;
- non-placeholder service definitions;
- private networking;
- dependency/security CI;
- migration validation;
- secret backend integration when decided;
- retention/archival implementation when policy is provided;
- removal/classification of legacy binary/placeholders;
- `.env.example` / runbooks.

### User dependencies

- `SECRETS-01` production secret backend;
- `RETENTION-01` retention periods;
- deployment environment details.

Acceptance: clean deployment from repository, dependency-aware readiness, no undocumented manual state, auditable operator actions.

---

## [ ] Segment 19 — Scale, chaos and provider-class readiness certification

### Goal

Prove rather than assume scalability.

### Test dimensions

- multiple dispatcher processes;
- large worker pools;
- per-capability partitions/priorities;
- session scarcity under high worker count;
- retry/dead-letter storms;
- Redis interruption/restart;
- PostgreSQL interruption/failover environment where available;
- literal worker process termination;
- browser-worker isolation;
- protocol version mass degradation;
- raw-store backpressure;
- sustained/soak throughput;
- queue-age SLOs;
- monitor gap/backfill load;
- operation canary rollout at load;
- resource saturation and graceful degradation.

### Infrastructure decision gate

Only after measurements decide whether PostgreSQL + Redis Streams remains adequate or whether a different DeliveryBus/control-plane topology is justified.

### User dependency

`SCALE-01`: final capacity certification requires production-like hardware/topology and target SLOs.

### Acceptance

A written benchmark/fault report states:

- tested sustained/burst throughput;
- latency distributions;
- failure/recovery behavior;
- bottlenecks;
- safe worker/session scaling limits;
- storage growth;
- operational limits;
- whether architecture meets the requested production envelope.

---

# 5. Capability completeness target

The catalog should eventually cover the major **read/data** capabilities visible in serious X-data APIs:

### Tweet/search

- advanced search;
- latest/relevance variants;
- lookup by IDs;
- replies;
- quotes;
- retweeters;
- thread context;
- articles/details.

### User

- lookup/batch lookup;
- search;
- timelines;
- mentions;
- followers/follower IDs;
- following;
- relationship;
- profile/about.

### Collections

- lists;
- communities.

### Incremental

- user monitoring;
- query/filter monitoring;
- cursors/checkpoints;
- gap recovery.

Mutating/account actions belong in a separately authorized **Action Plane** if ever required. They are not prerequisites for the ingestion platform.

---

# 6. Hard isolation requirements

Every segment must preserve these invariants from `architecture.md`:

1. **Control Plane does not depend on X protocol details.**
2. **Capability contracts do not depend on adapters/endpoints.**
3. **Protocol operations do not depend on analytics.**
4. **Research/discovery tooling is not required for stable collection.**
5. **Candidate operations cannot become stable without validation/canary evidence.**
6. **Raw payload persistence allows parser/normalizer replay.**
7. **Session health and session leasing are separate concepts.**
8. **One capability/version failure has bounded blast radius.**
9. **Parent-system clients do not depend on internal queue/storage implementation.**
10. **Infrastructure is replaceable behind narrow boundaries, but only replaced after measured need.**

A change that violates these rules is architectural regression even if it makes one endpoint work temporarily.

---

# 7. Cross-segment verification matrix

As implementation progresses, continuously test these failure cases:

| Failure | Must remain working |
|---|---|
| X search operation breaks | queue, users, graph, stored data, unrelated capabilities |
| Twikit breaks | first-party stable operations |
| browser observation breaks | stable protocol acquisition |
| Redis restarts | durable task/outbox state |
| worker dies | task recovery |
| session dies | other sessions/capabilities |
| parser breaks | raw acquisition persistence/reprocessing |
| analytics breaks | ingestion + normalization |
| candidate operation is wrong | current stable production version |
| parent contract version changes | internal protocol/control planes |

Add automated tests for these boundaries when the corresponding modules exist.

---

# 8. Self-healing maturity target

Do not call the system self-healing until the levels below are separately demonstrated.

### SH-0 — observe

Health/probes/schema drift identify what broke.

### SH-1 — fail over

Router can automatically choose an already validated stable alternate.

### SH-2 — discover candidate

Research tooling can produce a candidate operation/parser definition from new observations.

### SH-3 — validate/promote

Candidate passes fixtures + live canary + comparison and can be promoted/rolled back with audit.

### SH-4 — bounded automatic repair

Only predefined safe classes of change can automatically promote after all gates.

### SH-5 — assisted reverse-engineering escalation

Unrepairable drift produces a complete investigation bundle for Codex/researcher.

Arbitrary autonomous code rewriting/deployment is not an acceptance requirement.

---

# 9. Open external/user dependencies

These are tracked so they are not forgotten. They do **not** block unrelated work.

- `LIVE-X-01` — authorized live research sessions/network/proxy environment for final live protocol validation.
- `SECRETS-01` — production SecretStore backend choice.
- `PARENT-01` — larger ingestion system's final API/event integration contract.
- `AUTH-01` — parent/gateway authentication model.
- `SCALE-01` — production-like hardware/topology + sustained/burst throughput/SLO targets.
- `RETENTION-01` — raw/canonical/task/dead-letter/audit retention periods.

Ask the user only when a segment cannot be safely completed without one of these decisions.

---

# 10. Final production acceptance

The overall project is not complete until all of the following are demonstrated:

## Ownership / capability

- primary required read capabilities run through first-party protocol implementations;
- third-party X libraries are not a single required runtime dependency;
- capability catalog has explicit implemented/degraded/unsupported state;
- protocol operation/parser versions are traceable and reproducible.

## Reliability

- no acknowledged task loss under worker/dispatcher failure;
- deterministic retries/dead letters/replay;
- session concurrency/budget limits;
- operation/version failure isolation;
- monitor gap detection/backfill;
- candidate promotion rollback.

## Data correctness

- raw acquisition evidence preserved according to retention policy;
- canonical identity and observations remain distinct;
- engagement/re-observation does not inflate data;
- parsers/normalizers are versioned and replayable;
- provenance reaches task -> acquisition plan -> operation -> raw payload -> normalized object.

## Self-healing

- operation health/drift monitoring works;
- validated alternate routing works;
- candidate/canary/promotion pipeline works;
- failed repair quarantines safely;
- investigation package is useful enough to hand to a researcher/Codex.

## Platform quality

- versioned northbound/parent contracts;
- analytics/briefs isolated downstream;
- reproducible build/deploy/migrations;
- secret handling appropriate to environment;
- metrics/tracing/runbooks;
- load/chaos/soak report against concrete infrastructure/SLOs.

---

# 11. Immediate next step

Do **not** resume the old Segment 4 token-leasing plan as originally written.

The next runtime implementation segment is now:

> **Segment 4 — Capability contracts and planner boundary.**

This is deliberately first because it creates the seam that lets the Control Plane stay stable while Twikit is progressively replaced by our first-party protocol implementation.
