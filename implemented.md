# XINGESTIONV2 Implementation Ledger

> Read [`AGENTS.md`](./AGENTS.md), [`architecture.md`](./architecture.md), and [`plan.md`](./plan.md) before changing runtime behavior.

This file records implementation evidence, verified segments, architecture-only decisions, and cross-segment dependencies. A change is not called verified unless the stated verification actually ran.

## External / user-dependent items

**Current status: none of these block the next engineering segment.** Ask the user only when one becomes necessary to proceed.

- **LIVE-X-01 — live collector validation:** first-party live protocol certification will eventually require authorized research test identities/session material plus the approved outbound network/proxy environment. Fixture/contract/research tooling can continue without this. **User/environment input required later.**
- **SECRETS-01 — production secret backend:** final production credential/session storage needs an approved secret backend (cloud secret manager, Vault/KMS-style system, or parent-platform secret service). A provider-neutral `SecretStore` interface/local implementation can be built before this choice. **User/platform decision required later.**
- **PARENT-01 — parent ingestion integration contract:** final merge into the larger ingestion system needs ingress/egress schema, event/API transport, authentication, correlation IDs and versioning expectations. Internal modularization can continue now. **User/parent-system details required later.**
- **SCALE-01 — deployment/SLO envelope:** production capacity certification requires target hardware/topology, region/network assumptions, PostgreSQL/Redis deployment model, expected sustained/burst throughput, latency SLOs and availability targets. **User/infrastructure details required before final load certification.**
- **RETENTION-01 — retention policy:** final raw/canonical/task/dead-letter/audit cleanup and archival needs required retention periods. **Organizational/user decision required before final retention implementation.**
- **AUTH-01 — parent/API authentication:** final API/gateway hardening needs the larger system's trust model (for example mTLS, workload identity/JWT, gateway auth). Provider-neutral API contracts can be implemented first. **User/platform decision required later.**

These are dependencies, not reasons to stop unrelated implementation.

---

## 2026-08-08 — Repository audit and engineering-control documentation

**Scope:** audit baseline and documentation controls.

**Files:** `AGENTS.md`, original `plan.md`, `implemented.md`.

Established production-first/no-capability-regression rules, government-related research context, future larger-system integration target, verification discipline, and implementation ledger. Static review covered worker, analytics, API, session refresh/seeding, replay/cleanup, schema, Compose and deployment placeholders. Original runtime baseline: `8e7771a483d5ea57f440f7f410e7b0bea0176f4c`.

No runtime remediation was claimed in this entry.

---

## 2026-08-08 — Segment 1: durable control plane and outbox

**Related plan area:** durable task delivery / reproducibility / integration-test foundation.

### Implemented

- PostgreSQL authoritative task ledger;
- idempotency keys and delivery generations;
- transactional outbox;
- Redis Streams consumer groups instead of destructive list consumption;
- claim-based outbox dispatcher using PostgreSQL `SKIP LOCKED`;
- task `ENQUEUED` state committed before Redis publication;
- duplicate-delivery safety through generation/idempotency guards;
- Redis AOF for local stack, health checks, migrations, pinned dependencies and CI.

### Verified

GitHub Actions run **`31241791142`** passed against PostgreSQL 15 + Redis 7. Verified install, compilation, correctness lint, migrations, task -> outbox -> Redis -> DB lease -> DONE -> ACK, and safe rejection/ACK of duplicate delivery after completion.

### Remaining after Segment 1

Worker heartbeat/reclaim, retry-generation lifecycle, literal process failure and high-scale/multi-dispatcher fault testing remained for later segments.

---

## 2026-08-08 — Segment 2: worker lease heartbeat and crash recovery

### Implemented

- renewable PostgreSQL execution leases;
- lease renewal fenced by task ID, delivery generation, owner, state and non-expiry;
- Redis pending-entry idle refresh with `XCLAIM`;
- validated heartbeat/lease/reclaim timing relationships;
- guarded collection/persistence cancellation after durable lease loss;
- graceful cancellation returns work to `ENQUEUED`; hard crash recovers via lease expiry + Redis reclaim.

### Verified

GitHub Actions run **`31243415471`** passed with PostgreSQL 15 + Redis 7. Verified healthy heartbeat fencing, deterministic hard-crash state recovery, stale-owner commit rejection and cancellation after deliberately losing the DB fence.

### Remaining after Segment 2

Literal OS `SIGKILL`, cross-region/clock-skew behavior, reclaim storms/high-scale chaos, and session lease lifecycle remained separate work.

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
2. deterministic advancement of durable due time makes it eligible without sleep;
3. delivery generation `0 -> 1` rollover and stale generation rejection;
4. successful retry completion with attempt count preserved;
5. retry exhaustion creates exactly one dead-letter archive with correct failure class/generation;
6. selective replay filtering;
7. replay lineage/audit;
8. replay idempotency through normal operator path;
9. replacement task can execute through the same outbox/Redis/lease path;
10. all prior control-plane/crash-recovery tests remain passing.

### Status after Segment 3

The core single-region task lifecycle is integration-verified: durable creation, idempotency, outbox delivery, generation fencing, worker leasing/heartbeat, crash recovery, scheduled retry, stale-message rejection, dead-lettering, replay and durable completion/ACK.

Provider-scale production certification is not claimed. Load/chaos/failover/soak work remains later.

---

## 2026-08-08 — Architecture reset: first-party protocol ownership and protocol intelligence

**Type:** architecture/design pass only. **No runtime code behavior changed in this entry.**

### Why the plan changed

The earlier roadmap still treated Twikit as the primary protocol implementation to harden around. The revised mission is broader: required X data capabilities should ultimately execute through a protocol implementation owned by this project, while Twikit/twscrape/provider docs/browser observations become research/reference inputs rather than a single runtime protocol authority.

Public capability documentation from serious X-data providers was reviewed as a completeness benchmark. Public web-scraping architecture documentation was also reviewed to validate the module-separation approach. The important recurring patterns were independent request queues, session pools/state, routing/handlers, concurrency control, HTTP-vs-browser acquisition, network capture, explicit retry/error semantics and versioned extraction behavior. No private provider architecture is assumed.

### Files changed

- added `architecture.md`;
- completely rewrote `plan.md` around the new architecture;
- updated `AGENTS.md` so first-party protocol ownership and staged self-healing are durable project rules;
- updated this ledger.

### Architecture decided

The system is separated into stable planes:

1. **Capability Plane** — what data is requested;
2. **Control Plane** — durable tasks, delivery, leases, retry/replay;
3. **Protocol Plane** — versioned first-party X operations, transport and parsers;
4. **Intelligence Plane** — probes, drift detection, research captures, candidate registry, canary/promotion/rollback and investigation escalation;
5. **Data Plane** — immutable raw acquisition evidence, normalization, canonical entities/edges/observations and provenance;
6. downstream analytics/integration consume those planes but cannot destabilize acquisition.

### Important decisions

- Provider endpoints are a capability checklist, not a one-to-one internal specification.
- `CapabilityRequest` must not expose Twikit types, browser selectors or X operation IDs.
- `AcquisitionAdapter` is used only at real external/transport boundaries; the architecture deliberately avoids meaningless adapter proliferation.
- `XInternalWebAdapter` is the intended primary first-party runtime path.
- browser acquisition becomes an independent observation/recovery path.
- Twikit becomes a transitional/reference adapter rather than long-term protocol authority.
- protocol operations/parsers are versioned and health-scored.
- self-healing is staged: observe -> known-alternate failover -> candidate discovery -> validation/canary -> promotion/rollback -> bounded auto-repair -> Codex/researcher investigation package when unresolved.
- arbitrary agent/LLM code rewriting directly into production is not the self-healing model.
- raw payload/provenance must survive parser failure so data can be reparsed instead of recollected where retention permits.
- analytics/alerts/briefs move downstream from acquisition.
- state-changing account actions, if ever required, belong in a separate authorized Action Plane, not the ingestion core.
- use a modular codebase with only the process/service boundaries that have real scaling/failure-isolation value; avoid microservice theatre.
- PostgreSQL + Redis Streams stay until measurements justify replacing them.

### Revised roadmap

The new roadmap estimates approximately **16 remaining focused runtime segments, Segments 4–19**:

4. Capability contracts/planner boundary
5. Session/identity/budget/secret boundary
6. First-party protocol foundation/raw envelope
7. First-party `SEARCH_TWEETS` vertical slice
8. Raw data plane/normalization decoupling
9. Protocol observation/research lab
10. Health/drift detection
11. Candidate/canary/promotion/rollback self-healing foundation
12. Core tweet capabilities
13. User/profile/timeline capabilities
14. Graph/lists/communities
15. Monitoring/incremental ingestion/gap recovery
16. Northbound/parent integration API
17. Analytics/alerts/brief decoupling
18. Operability/security/deployment/retention
19. Scale/chaos/provider-class readiness certification

The scope is larger than the old plan because the system is no longer merely hardening a third-party library integration; it is building first-party protocol ownership plus protocol intelligence.

### Verification performed for this pass

- Re-read current `AGENTS.md`, current verified implementation ledger and existing roadmap before redesign.
- Cross-checked the design against the already implemented Control Plane so Segments 1–3 remain useful rather than being rewritten.
- Reviewed public/primary documentation for Crawlee/Apify request/session/routing/scaling separation, Zyte HTTP/browser/session/network-capture/error behavior, public TwitterAPI.io capability surface, and current open-source X-library failure patterns.
- Re-read the resulting `architecture.md` and new `plan.md` for boundary/roadmap consistency.

No runtime tests were required because no runtime code changed in this architecture-reset entry.

### Immediate next runtime segment

**Segment 4 — Capability contracts and planner boundary.**

This creates the seam that lets the verified Control Plane remain stable while the X acquisition implementation is replaced underneath it.
