# XINGESTIONV2 Implementation / Verification Ledger

> Read `AGENTS.md`, `architecture.md`, `protocol-integration.md`, and `plan.md` before changing runtime behavior.

This file records implementation evidence, verified segments, architecture/documentation decisions and cross-repository dependencies. A change is not called verified unless the stated verification actually ran.

## Ledger/update rules

For every meaningful implementation, architecture or integration pass, record:

- date and scope;
- files/components changed;
- important decisions;
- what was actually tested/observed;
- exact CI/run/commit/release references where available;
- uncertainty/blockers;
- X-rev-os integration impact where relevant;
- next recommended step.

Do not silently rewrite old evidence when conclusions change; append a superseding entry.

Once X-rev runtime integration exists, record the exact pinned:

```text
X-rev git commit/release
xrev-runtime version
protocol-bundle version
capability-contract version
ProtocolReleaseManifest/checksum
```

in this ledger.

---

# External / user-dependent items

None of these should block unrelated engineering work.

- **LIVE-X-01 — live protocol validation:** final live X-rev recipe and production integration certification require authorized research/test session material and approved outbound network/proxy environment.
- **SECRETS-01 — production secret backend:** final credential/session storage needs an approved Vault/KMS/cloud/parent secret backend. Provider-neutral `SecretStore` interfaces can be built before selection.
- **PARENT-01 — parent/NOS integration contract:** final parent transport/schema/auth/correlation/versioning details remain external.
- **SCALE-01 — production SLO/capacity envelope:** provider-scale certification needs target sustained/burst throughput, hardware/topology, latency/availability targets and deployment assumptions.
- **RETENTION-01 — retention policy:** final raw/canonical/task/dead-letter/audit cleanup/archival requires explicit organizational retention policy.
- **AUTH-01 — parent/API trust model:** final northbound authentication needs the parent trust model.

---

## 2026-08-08 — Repository audit and engineering-control documentation

**Type:** audit/documentation baseline.

Established production-first/no-capability-regression rules, research context, future larger-system integration target and verification discipline.

Static review covered worker, analytics, API, session refresh/seeding, replay/cleanup, schema, Compose and deployment placeholders.

Original runtime baseline recorded at the time: `8e7771a483d5ea57f440f7f410e7b0bea0176f4c`.

No runtime remediation was claimed in this entry.

---

## 2026-08-08 — Segment 1: durable control plane and outbox

### Implemented

- PostgreSQL authoritative task ledger;
- idempotency keys and delivery generations;
- transactional outbox;
- Redis Streams consumer groups instead of destructive list consumption;
- claim-based outbox dispatcher using PostgreSQL `SKIP LOCKED`;
- task `ENQUEUED` committed before Redis publication;
- duplicate-delivery safety through generation/idempotency guards;
- Redis AOF for local stack, health checks, migrations, pinned dependencies and CI.

### Verified

GitHub Actions run **`31241791142`** passed against PostgreSQL 15 + Redis 7.

Verified install, compilation, correctness lint, migrations, task -> outbox -> Redis -> DB lease -> DONE -> ACK, and safe rejection/ACK of duplicate delivery after completion.

### Remaining after Segment 1

Worker heartbeat/reclaim, retry lifecycle, literal process failure and high-scale/multi-dispatcher fault testing remained for later segments.

---

## 2026-08-08 — Segment 2: worker lease heartbeat and crash recovery

### Implemented

- renewable PostgreSQL execution leases;
- lease renewal fenced by task ID, delivery generation, owner, state and non-expiry;
- Redis pending-entry idle refresh with `XCLAIM`;
- heartbeat/lease/reclaim timing validation;
- guarded collection/persistence cancellation after durable lease loss;
- graceful cancellation returns work to `ENQUEUED`; hard crash recovers via lease expiry + Redis reclaim.

### Verified

GitHub Actions run **`31243415471`** passed with PostgreSQL 15 + Redis 7.

Verified healthy heartbeat fencing, deterministic hard-crash recovery, stale-owner commit rejection and cancellation after deliberately losing the DB fence.

### Remaining after Segment 2

Literal OS `SIGKILL`, same-worker-ID ABA/unique lease-token hardening, cross-region/clock-skew behavior, reclaim storms/high-scale chaos and session lease lifecycle remain separate work.

---

## 2026-08-08 — Segment 3: retry, dead-letter and replay lifecycle

### Implemented

- generation-scoped enqueue timing;
- `RUNNING -> RETRY_SCHEDULED` with attempt/generation increment and durable due time;
- stale old-generation delivery rejection;
- retry exhaustion -> `DEAD_LETTER`;
- replay lineage (`origin_task_id`, `replay_of_dead_letter_id`);
- immutable replay audit table;
- selective replay by dead-letter IDs/task type/failure class;
- transactionally coupled replacement task + outbox + replay audit + archive replay mark;
- replay CLI filters/priority/max-attempt controls.

### Verified

Final strengthened GitHub Actions run **`31245531114`** passed completely against PostgreSQL 15 + Redis 7.

Verified:

1. a retry scheduled 60 seconds ahead cannot publish early;
2. deterministic advancement of due time makes it eligible without sleep;
3. delivery generation `0 -> 1` rollover and stale generation rejection;
4. successful retry completion with attempt count preserved;
5. retry exhaustion creates exactly one dead-letter archive with correct failure class/generation;
6. selective replay filtering;
7. replay lineage/audit;
8. replay idempotency through normal operator path;
9. replacement task executes through the same outbox/Redis/lease path;
10. prior control-plane/crash-recovery tests remained passing.

### Status after Segment 3

The core single-region logical task lifecycle is integration-verified: durable creation, idempotency, outbox delivery, generation fencing, worker leasing/heartbeat, crash recovery, scheduled retry, stale-message rejection, dead-lettering, replay and durable completion/ACK.

Provider-scale certification is not claimed.

Known later hardening includes unique lease tokens/epochs, Redis dataset-loss redrive, stream retention, migration-history hardening and scale/chaos/soak testing.

---

## 2026-08-08 — Architecture reset: first-party protocol ownership

**Type:** architecture/design pass only; no runtime behavior changed.

Changed the mission from merely hardening a Twikit-based scraper to owning the required X data capability stack and keeping third-party libraries as research/reference inputs.

Established conceptual separation of capability, control, protocol, protocol-intelligence, data and downstream concerns, while retaining PostgreSQL + Redis until measurements justify migration.

This entry preceded creation of the dedicated X-rev-os repository; the later 2026-08-09 entries below supersede the **implementation location** of the protocol/research plane while preserving the underlying first-party ownership/failure-isolation goals.

---

## 2026-08-09 — Dedicated X-rev-os protocol repository created

**Type:** architecture/documentation decision only; no XINGESTIONV2 runtime code changed.

Canonical specialist repository established:

```text
techtiesai-png/X-rev-os
```

### Decision

XINGESTIONV2 remains the **core production ingestion repository/integration anchor**.

X-rev-os becomes the canonical specialist repository for:

- X protocol observation/reverse engineering;
- operation/request definitions;
- X request construction;
- X parsers/pagination;
- X auth/session attachment semantics;
- client transaction/shared request metadata;
- feature/config bundles;
- fixtures and recipe validation;
- protocol runtime/bundle/release exports;
- deep drift/candidate research.

XINGESTIONV2 continues owning:

- canonical CapabilitySpec;
- tasks/queue/workers;
- all production retries;
- production session/network pools;
- production raw evidence;
- canonical normalization/data;
- monitoring;
- analytics/APIs;
- parent/NOS integration;
- production rollout/pinning of approved X-rev releases.

No runtime integration was implemented or verified in this pass.

---

## 2026-08-09 — Cross-repository runtime/validation contract resolved

**Type:** architecture/documentation pass only.

Resolved and documented:

- canonical `CapabilitySpec` ownership in XINGESTIONV2;
- X-rev `ProtocolCapabilityBinding` mapping;
- typed exported runtime concepts (`ProtocolRequest`, `SessionContext`, injected transport/network context, `RawEvidenceSink`, `AcquisitionResult`, typed errors);
- zero automatic retries in exported X-rev production runtime;
- XINGESTION ownership of production raw evidence through injected sink;
- validation attached to the exact immutable `AcquisitionRecipeRevision` composition;
- independent runtime/bundle/capability-contract versioning;
- pinned `ProtocolReleaseManifest` for production;
- one-session limitation on session-local-vs-global drift classification;
- deterministic fixture transformation/provenance;
- separate evidence maturity, validation freshness and operational health.

Detailed X-rev contracts live in the X-rev repository's `architecture.md`.

No live X or production integration tests were run in this documentation pass.

---

## 2026-08-09 — XINGESTIONV2 architecture/roadmap synchronized with X-rev-os

**Type:** documentation/architecture synchronization; no runtime code changed.

### Files changed

- rewrote `AGENTS.md` around XINGESTIONV2 as the core integration repository and X-rev-os as protocol authority;
- rewrote `architecture.md` so the Protocol Plane is a pinned X-rev runtime rather than a duplicate internal research/operation-registry implementation;
- added `protocol-integration.md` as the authoritative cross-repository ownership/runtime contract;
- rewrote `plan.md` so protocol observation/reverse-engineering/candidate validation is implemented in X-rev-os and consumed here, while XINGESTION builds capability contracts, production session/network/raw-storage integration, production telemetry and rollout;
- updated this ledger.

### Important corrections

- old roadmap Segment 6/7/9/10/11 descriptions that would have duplicated the protocol registry/browser research/parser authority directly in XINGESTIONV2 are superseded;
- production retry authority is explicitly single-owned by XINGESTIONV2;
- raw evidence is single-owned by the production Data Plane and injected into X-rev runtime through `RawEvidenceSink`;
- X-rev release/runtime/bundle is pinned rather than consumed as floating research state;
- production health feeds evidence back to X-rev research, but stable production does not require the research browser/tooling to run.

### Verification

Documentation was cross-read for ownership/roadmap consistency. No runtime tests were required or claimed because runtime code did not change.

### Next recommended XINGESTIONV2 step

**Segment 4 — canonical CapabilitySpec and planner boundary**, while X-rev-os independently begins Stage 0 research-kernel work.

---

## 2026-08-09 — Documentation governance synchronized across repositories

**Type:** documentation/process decision only.

XINGESTIONV2 primary docs now have distinct authoritative jobs:

```text
AGENTS.md                durable rules/ownership/cadence
architecture.md          whole-system target architecture
protocol-integration.md  X-rev integration contract
plan.md                  roadmap/status/acceptance gates
implemented.md           factual update/verification ledger
```

X-rev-os now follows the corresponding four-doc model:

```text
AGENTS.md
architecture.md
plan.md
implemented.md
```

For meaningful work, Codex/researchers must update the relevant docs and ledger rather than leaving material answers only in chat history.

A stronger ADR/generated-index/machine-readable documentation approach may be proposed later, but must include an explicit migration preserving history, evidence, ownership and cross-repository links rather than creating a parallel stale documentation tree.

No runtime work was performed in this entry.

---

## 2026-08-09 — Canonical fork and main-branch alignment

**Type:** repository-management and documentation synchronization; no runtime
behavior changed.

### Result

- established `techtiesai-png/XINGESTIONV2` as the only configured and
  authoritative XINGESTIONV2 repository;
- documented that the historical `Pruthavirajsingh/XINGESTIONV2` upstream is
  not a project source of truth and is inspected only when explicitly
  requested;
- integrated the complete `hardening/control-plane-v1` control-plane and
  architecture history into the fork's `main` branch;
- made the clean worktree's local `main` track `origin/main`;
- preserved the old dirty checkout unchanged on `legacy-dirty-main`.

### Git safety and verification

- authenticated GitHub account: `techtiesai-png`;
- new commit identity:
  `techtiesai-png <298773167+techtiesai-png@users.noreply.github.com>`;
- confirmed before integration that `origin/main` had no commits absent from
  `origin/hardening/control-plane-v1` (`0 56` left/right divergence);
- committed the repository-identity documentation as `de96a21`;
- pushed by ordinary fast-forward, without force:
  `origin/main` `00725eb -> de96a21`;
- verified `origin/hardening/control-plane-v1` is an ancestor of the updated
  `origin/main` (`0 1` divergence after the documentation commit);
- verified the original dirty files remained present and unmodified:
  `seed_test.py`, `task_replay.py`, `worker.py`, `CLAUDE.md`, and
  `requirements.txt`;
- no push was made to the historical upstream repository.

### Documentation changed

- `AGENTS.md`;
- `architecture.md`;
- `protocol-integration.md`;
- `plan.md`;
- `implemented.md`.

### Cross-repository impact

The corresponding canonical repository identity is recorded in the standing
documents of `techtiesai-png/X-rev-os`.

### Next recommended step

Continue production work from fork `main`. The next planned production slice
is Segment 4 — canonical capability contracts and planner boundary, while
X-rev-os proceeds with Stage 0.

---

## 2026-08-09 — Segment 4 capability contract and planner implemented

**Type:** production contract/planner implementation with local focused
verification; remote PostgreSQL/Redis CI pending.

### Implemented

- canonical machine-readable `SEARCH_TWEETS` capability contract version `1`
  at `xingestion/contracts/capabilities.v1.json`;
- typed protocol-neutral `CapabilitySpec`, `CapabilityRequest`,
  `CapabilityCatalog`, `CapabilityRoute`, `CapabilityPlanner`, and
  `AcquisitionPlan` models;
- typed input/default/enum/range/date validation, opaque cursor and page-size
  contract enforcement;
- route-level supported product/input constraints and bounded page limits;
- fail-fast route validation, including the requirement that every future
  X-rev runtime route pin an immutable `ProtocolReleaseManifest`;
- explicit `XREV_PROTOCOL_RUNTIME`, `LEGACY_SOURCE_ADAPTER`, and `FIXTURE`
  executor kinds without embedding X operation/query IDs;
- canonical durable task type `CAPABILITY_REQUEST` and serializer;
- compatibility translation from legacy `X_KEYWORD_SEARCH` payloads;
- worker planning before collection, with capability/contract/route provenance
  included in result metadata;
- deliberate rejection of unsupported `Top`, `Media`, and filter semantics on
  the current bounded legacy `Latest` route;
- `seed_test.py` migration to canonical capability tasks;
- package version `0.3.0` and packaged contract JSON;
- `.gitignore` for local Python, environment and test state;
- CI branch triggers updated for canonical `main` and temporary segment/feature
  branches, with the new capability/worker tests included.

No X request builder, parser, pagination algorithm or reverse-engineering
machinery was added to XINGESTIONV2.

### Local verification

The workstation only provides Python 3.12 while the repository officially
pins Python 3.11. A dependency-compatible Python 3.12 smoke environment was
used; GitHub Python 3.11 remains authoritative.

- `python -m compileall -q xingestion tests worker.py seed_test.py` — passed;
- `ruff check --select E,F,B ...` — passed;
- focused `pytest` run covering capability contracts, worker capability routing
  and control-plane value objects — **12 passed**;
- wheel build — passed; inspection confirmed the wheel contains
  `xingestion/contracts/capabilities.v1.json`;
- `git diff --check` — passed.

Tests establish that legacy search maps to `SEARCH_TWEETS@1`, canonical task
payloads round-trip, unsupported semantics fail closed, malformed/unknown
inputs are rejected, and a second synthetic capability can be planned without
changes to TaskRepository or Redis delivery code.

### Verification not yet claimed

- PostgreSQL/Redis integration tests under the supported Python 3.11 CI image
  are pending the implementation push;
- no X-rev runtime release is integrated;
- no live X behavior is exercised.

### Cross-repository impact

The previously planned canonical capability-contract artifact now exists.
X-rev-os documentation is updated in the same workstream so future
`ProtocolCapabilityBinding` work references this artifact rather than a
research-only guessed contract.

### Next step

Require the full control-plane CI job to pass, then mark Segment 4 complete.
The next XINGESTIONV2 segment is Segment 5 — session, identity, network, budget
and secret boundary. X-rev-os can independently proceed to Stage 1 once the
authorized browser login is available.
