# XINGESTIONV2 Implementation Plan

> Read `AGENTS.md`, `architecture.md`, `protocol-integration.md`, and `implemented.md` before implementation. X-specific protocol research/runtime implementation belongs in `techtiesai-png/X-rev-os`; this roadmap owns production integration and the whole ingestion system.

## Documentation roles

- `AGENTS.md` — durable rules/ownership/cadence.
- `architecture.md` — whole-system target architecture.
- `protocol-integration.md` — XINGESTIONV2 ↔ X-rev-os integration contract.
- `plan.md` — implementation roadmap/status/acceptance gates.
- `implemented.md` — factual implementation/verification/update ledger.

If a stronger documentation system is adopted later, preserve these jobs, historical evidence and cross-repository links through an explicit migration rather than creating parallel stale docs.

---

## Status legend

- `[ ]` not started
- `[~]` partially implemented / migration in progress
- `[x]` implemented and verified to the evidence recorded in `implemented.md`
- `[D]` architecture/design decided but runtime work not complete
- `[XREV]` work is primarily implemented/validated in X-rev-os and consumed here through a versioned integration contract
- `BLOCKED-USER` requires external/user/platform information before finalization

---

# Mission

Build an independently owned, production-grade X/Twitter ingestion subsystem with:

- stable protocol-neutral capability contracts;
- durable horizontally scalable task/control plane;
- first-party X protocol behavior supplied by the canonical X-rev runtime;
- production account/session/network management;
- immutable raw production evidence;
- canonical entities/relationships/time-varying observations;
- monitoring/incremental ingestion/gap recovery;
- protocol health feedback and controlled approved-release rollout;
- downstream analytics/alerts/briefs isolated from acquisition;
- clean parent/NOS integration.

Third-party X libraries remain reference/comparison inputs, not production protocol authorities.

## [x] Canonical repository and branch

All active XINGESTIONV2 work targets:

```text
repository: techtiesai-png/XINGESTIONV2
integration branch: main
```

The historical `Pruthavirajsingh/XINGESTIONV2` upstream is not a project source
of truth and is used only for an explicitly requested comparison. The former
`hardening/control-plane-v1` line is integration history, not a permanent
authoritative branch after its verified fast-forward into `main`.

---

# Completed foundation

## [x] Segment 1 — durable task control plane

Preserve:

- PostgreSQL authoritative task ledger;
- idempotency keys/delivery generations;
- transactional outbox;
- Redis Streams consumer groups;
- claim-based outbox dispatch;
- durable task state transitions;
- CI/integration-test baseline.

Verification evidence: `implemented.md`.

## [x] Segment 2 — worker lease/crash recovery

Preserve:

- durable task leases;
- heartbeats;
- Redis pending-entry refresh/reclaim;
- stale-owner fencing;
- deterministic crash-recovery integration tests.

Verification evidence: `implemented.md`.

## [x] Segment 3 — retry/dead-letter/replay lifecycle

Preserve:

- scheduled durable retries;
- delivery-generation rollover;
- stale-generation rejection;
- retry exhaustion/dead letters;
- selective audited replay/lineage.

Verification evidence: `implemented.md`.

### Known future Control Plane hardening

Before provider-scale claims, add/verify as appropriate:

- unique per-acquisition task lease token/epoch to eliminate same-worker-ID ABA stale-owner ambiguity;
- Redis dataset-loss/redrive/reconciliation path;
- Redis stream retention/trim policy;
- high-scale/chaos/soak testing;
- migration-history hardening.

These are improvements to the verified foundation, not reasons to discard Segments 1–3.

---

# Cross-repository architecture decision

## [D] X-rev protocol split

Canonical specialist repository:

```text
techtiesai-png/X-rev-os
```

X-rev-os owns X-specific protocol observation, requests, parsers, pagination, transaction/auth attachment semantics, validation fixtures and runtime/bundle exports.

XINGESTIONV2 remains the core system and owns canonical capabilities, queues/tasks, production retries, production session/network pools, raw storage, canonical normalization, monitors, analytics, APIs and parent integration.

The detailed contract is `protocol-integration.md`.

The older roadmap items that proposed building a separate operation registry/browser research lab/parser authority directly inside XINGESTIONV2 are superseded by this boundary.

---

# Phase A — Correct stable production contracts

## [x] Segment 4 — Capability contracts and planner boundary

### Goal

Make the production system request stable capabilities rather than `X_KEYWORD_SEARCH`, Twikit methods or X protocol operations.

### Build

- canonical `CapabilitySpec` catalog/IDs/versioning in XINGESTIONV2;
- typed `CapabilityRequest` and protocol-neutral result/page expectations;
- fidelity/freshness/provenance requirements;
- opaque cursor/checkpoint contract;
- `CapabilityPlanner`/routing boundary;
- compatibility migration from current search task payloads;
- machine-readable capability-contract artifact/schema suitable for X-rev compatibility binding.

### Acceptance

- existing keyword search is representable as `SEARCH_TWEETS`;
- a second fake capability does not require TaskRepository/Redis changes;
- capability contract contains no operation/query IDs, Twikit types or browser selectors;
- capability contract version can be referenced by an X-rev `ProtocolCapabilityBinding`;
- control-plane tests continue passing.

Implemented and verified on 2026-08-10. The supported Python 3.11
PostgreSQL/Redis GitHub Actions evidence is recorded in `implemented.md`.

---

## [ ] Segment 5 — Session, identity, network, budget and secret boundary

### Goal

Replace ambiguous `service_tokens` semantics with production session/account/network abstractions independent of X protocol internals.

### Build

- `Account` identity metadata;
- long-lived credential `SecretRef`/`SecretStore` boundary;
- `SessionArtifact` metadata + secret ref;
- `SessionLease` with unique fencing token/expiry;
- session health separate from lease state;
- max concurrency/cooldown/revocation/refresh-required state;
- session affinity where required;
- operation/capability budget observations;
- `NetworkContext`/proxy allocation abstraction;
- adapter to the X-rev `SessionContext`/runtime interface without exposing passwords/TOTP.
- ephemeral resolution of cookies and optional authorization header material
  from `SessionArtifact`/`SecretRef`, with no secret values in durable tasks,
  logs or safe provenance.

### Acceptance

- dead worker cannot strand or later reuse a stale session lease;
- configured session concurrency cannot be exceeded;
- session/network selection is controlled here, while X-specific attachment remains in X-rev;
- no secret values appear in tasks/logs/tests.

Production secret backend choice remains user/platform-dependent; provider-neutral interfaces can be built first.

---

# Phase B — Evidence-first X-rev runtime integration

## [ ] Segment 6 — Production raw evidence plane + X-rev runtime adapter

### Goal

Create the production boundary required to consume a pinned X-rev runtime safely **before** making first-party search the production path.

### Build

- production `RawEvidenceSink` interface/implementation boundary;
- raw evidence metadata/ref model;
- object-storage-ready raw payload abstraction;
- X-rev `ProtocolRequest`/`SessionContext`/`NetworkContext` adapter;
- typed X-rev `ProtocolError` -> XINGESTION failure-class mapping;
- zero-hidden-retry verification;
- `ProtocolReleaseManifest` loader/checksum/compatibility validation;
- execution provenance storing runtime/bundle/recipe revisions;
- normalized protocol-output boundary distinct from production canonical data model.

### Acceptance

- fixture/mock X-rev runtime can execute through the integration adapter;
- production raw sink receives evidence before acquisition is considered safely captured;
- raw-sink failure fails acquisition rather than silently bypassing evidence;
- XINGESTION owns all retries;
- release-manifest incompatibility is rejected explicitly;
- no X-rev research path/browser/database is required.

---

## [XREV] X-rev Stages 0–3 — protocol research and validated SEARCH_TWEETS recipe

Implemented/validated in `techtiesai-png/X-rev-os`, not duplicated here:

```text
Stage 0  research kernel
Stage 1  browser/network observation
Stage 2  direct replay verified
Stage 3  parser + pagination + full SEARCH_TWEETS validation
```

XINGESTIONV2 must wait for/consume an approved compatible X-rev release manifest for final live first-party search certification.

Development of Segments 4–6 can proceed using mocks/contract fixtures before that release exists.

---

## [ ] Segment 7 — Production first-party SEARCH_TWEETS vertical slice

### Goal

Run `SEARCH_TWEETS` through the canonical capability contract + production control plane + session/network manager + pinned validated X-rev release + raw data plane.

### Build

- `SEARCH_TWEETS` capability planner binding;
- X-rev release selection/pinning;
- task -> session/network -> X-rev runtime flow;
- page-level raw evidence refs;
- protocol-normalized result -> production normalization queue/boundary;
- transitional Twikit shadow/comparison path only where explicitly enabled.

### Acceptance

- production task/control-plane path completes without Twikit constructing the request;
- exact X-rev runtime/bundle/recipe release is recorded in provenance and `implemented.md`;
- XINGESTION does not contain an independent copy of X-rev parser/request/pagination code;
- X-rev typed errors feed XINGESTION durable retry/dead-letter policy;
- raw evidence remains available if production normalization fails.

Final live certification requires authorized research/production session/network environment and an approved compatible X-rev recipe release.

---

## [ ] Segment 8 — Production normalization/reprocessing decoupling

### Goal

Make raw acquisition success independent of canonical/analytics parser/model failures after the X-rev protocol parser has produced its protocol-normalized record.

### Build

- asynchronous/idempotent production normalization job/event boundary;
- canonical tweet/user/edge/observation models;
- explicit production normalizer schema/version;
- reprocessing from raw/protocol-normalized evidence;
- time semantics and provenance;
- remove analytics rollup side effects from acquisition transactions;
- define acquisition-vs-normalization task success semantics.

### Acceptance

- production normalizer failure does not require recollection;
- raw/protocol evidence can be reprocessed;
- analytics outage cannot fail an otherwise safely captured acquisition.

---

# Phase C — Production health + X-rev protocol lifecycle feedback

## [ ] Segment 9 — Production capability/protocol telemetry

### Goal

Measure production behavior without duplicating the X-rev research lab.

### Build

- metrics keyed by capability, X-rev release, recipe, operation/error provenance and session/network cohort;
- last-success/first-failure production observations;
- parser/pagination warning accounting;
- schema fingerprint/raw provenance linkage;
- typed failure-scope aggregation;
- degraded capability/release alerts;
- investigation package export to X-rev-compatible evidence format where appropriate.

### Acceptance

- production can say which capability/release/recipe is degrading;
- one-session/cohort vs global conclusions are not overclaimed without evidence;
- queue/control-plane health remains independent from protocol health.

---

## [XREV] X-rev Stages 5,7,8 — protocol registry/drift/candidate lifecycle

X-rev-os owns:

- protocol knowledge registry and historical diff;
- browser/client-artifact research;
- candidate discovery;
- exact recipe validation;
- investigation packages;
- future bounded protocol candidate/canary metadata.

XINGESTIONV2 supplies production observations and consumes approved releases.

---

## [ ] Segment 10 — Approved release routing, rollback and known-alternate failover

### Goal

Safely operate multiple **already approved** compatible X-rev releases/recipes without allowing production workers to invent protocol changes.

### Build

- approved release catalog/pinning;
- compatibility checks against capability-contract version/runtime constraints;
- controlled rollout percentage/cohort selection where useful;
- rollback audit;
- quarantine/disable switch;
- failover only to a previously approved compatible alternate;
- operator audit trail.

### Acceptance

- bad production rollout can return to prior approved release;
- unapproved candidate cannot receive ordinary production traffic;
- no protocol definitions are mutated inside the production queue path.

---

# Phase D — Capability breadth

## [ ] Segment 11 — Core tweet capability family

Add production capability contracts/planner integration for approved X-rev implementations such as:

- tweet by ID(s);
- replies;
- thread context;
- quotes;
- retweeters;
- article/detail variants where required.

Each requires canonical XINGESTION capability contract + compatible approved X-rev binding + production raw/provenance/normalization support.

---

## [ ] Segment 12 — User/profile/timeline capability family

Add:

- user by ID/handle;
- user timelines;
- tweets + replies;
- mentions where required;
- profile observations.

Operation/request/parser knowledge remains X-rev-owned.

---

## [ ] Segment 13 — Social graph, lists and communities

Add production models/planning for:

- followers/following/relationship edges;
- lists/timelines/members;
- communities/info/timelines/membership where observable/required.

Optimize graph/edge storage semantics rather than pretending every result is a tweet document.

---

# Phase E — Monitoring and platform integration

## [ ] Segment 14 — Monitoring, incremental ingestion and gap recovery

### Build

- persistent subscriptions;
- user/query monitor definitions;
- cursors/watermarks;
- acquisition coalescing for identical due requests;
- deduplicated incremental delivery;
- outage gap detection/backfill;
- catch-up caps/jitter/priority;
- monitor lag metrics;
- bounded schedule fanout.

Acceptance: restart/outage gaps and catch-up state are explicit and do not create unbounded session stampedes.

---

## [ ] Segment 15 — Northbound API and parent/NOS integration

### Build

- versioned Capability API;
- async job submission/status;
- bounded synchronous reads where appropriate;
- stable cursor/page/error contracts;
- parent event/export contract;
- correlation IDs;
- authentication/trust boundary once parent details exist;
- liveness/readiness.

Parent clients must not know Redis stream names, task tables or X operation IDs.

---

## [ ] Segment 16 — Downstream analytics/alerts/brief decoupling

Turn legacy analytics into consumers of normalized evidence rather than acquisition side effects.

Acceptance:

- analytics/brief outage cannot stop acquisition;
- analytics is rebuildable from production evidence;
- alerts have idempotent/deduplicated lifecycle state.

---

# Phase F — Production readiness

## [ ] Segment 17 — Operability, security, deployment and retention

Build/finish:

- structured metrics/tracing/dashboards;
- admin/operator health endpoints;
- release/quarantine audit interfaces;
- production images/services;
- migration tooling/history validation;
- secret backend integration;
- retention/archival implementation;
- dependency/security/SBOM/licensing checks;
- runbooks/config examples.

Requires final retention/deployment/secret decisions for full certification.

---

## [ ] Segment 18 — Scale, chaos and provider-class readiness certification

Prove rather than assume:

- 10k+ burst task admission/backpressure;
- dispatcher/worker scale;
- PostgreSQL/Redis interruption and recovery;
- raw object-store interruption;
- monitor catch-up storms;
- session exhaustion/rate-limit behavior;
- X-rev release rollback;
- long-running soak;
- real capacity/cost model;
- SLOs.

Only after measured evidence should queue/database/multi-region topology be replaced or split further.

---

# Documentation gate

For every meaningful segment/pass:

1. update `plan.md` if status/scope/order/dependencies/acceptance changed;
2. append `implemented.md` with facts, tests/evidence, uncertainty, cross-repo impact and next step;
3. update `architecture.md` for whole-system durable changes;
4. update `protocol-integration.md` if the X-rev boundary/runtime contract changes;
5. update `AGENTS.md` only for durable project/ownership/safety/documentation rules;
6. update/cross-reference X-rev docs when the protocol integration contract changes;
7. never claim completion/validation beyond actual verification.

Codex may propose a better documentation/ADR/index model, but must provide a migration preserving these roles, history, evidence and cross-repository references rather than leaving a parallel stale documentation tree.
