# XINGESTIONV2 Target Architecture

> Status: authoritative whole-system architecture. Existing code is migrated toward this design segment-by-segment; this document is not a claim that every target component already exists.

Read together with:

- `AGENTS.md` — durable project rules and ownership;
- `protocol-integration.md` — authoritative XINGESTIONV2 ↔ X-rev-os protocol/runtime boundary;
- `plan.md` — implementation order/status;
- `implemented.md` — factual verification/update ledger.

Detailed X protocol research/runtime architecture lives in `techtiesai-png/X-rev-os` and must not be duplicated here.

---

# 1. Mission

Build a production-grade X/Twitter ingestion subsystem that:

1. exposes stable protocol-neutral data capabilities such as search, tweet lookup, replies, timelines, followers, lists, communities and monitoring;
2. has a durable, recoverable and horizontally scalable control plane;
3. uses first-party X-specific protocol behavior owned by the dedicated X-rev-os runtime rather than treating Twikit/twscrape as protocol authorities;
4. persists raw evidence before downstream canonical/analytics processing can destroy recoverability;
5. separates production session/network allocation from X-specific request construction;
6. keeps acquisition alive when downstream analytics/briefing fails;
7. feeds production protocol health evidence back to X-rev-os without making research tooling a production dependency;
8. can integrate later into a substantially larger parent/NOS ingestion system through versioned contracts;
9. measures and proves scale rather than selecting enterprise-looking infrastructure by default.

The system should use serious public X/web-data platforms only as capability/service-shape benchmarks. Do not infer their private architecture.

---

# 2. Repository topology

The project ecosystem intentionally has two repositories with one explicit boundary.

Canonical repositories and integration branches:

```text
techtiesai-png/XINGESTIONV2  -> main
techtiesai-png/X-rev-os      -> main
```

`Pruthavirajsingh/XINGESTIONV2` is only the historical upstream from which the
maintained XINGESTIONV2 repository was forked. It is not part of the active
project topology and is consulted only when an upstream comparison is
explicitly requested.

```text
┌──────────────────────────────────────────────────────────────┐
│                     techtiesai-png/X-rev-os                 │
│                                                              │
│ protocol observation / reverse engineering / evidence        │
│ X request construction / parsers / pagination / transaction │
│ acquisition recipes / validation / protocol release bundle  │
└──────────────────────────────┬───────────────────────────────┘
                               │
                     pinned ProtocolReleaseManifest
                               │
                               v
┌──────────────────────────────────────────────────────────────┐
│                    XINGESTIONV2                              │
│                                                              │
│ CapabilitySpec / task ledger / queue / workers              │
│ session & network pools / production retries                │
│ raw production evidence / canonical data                    │
│ monitoring / analytics / APIs / parent integration          │
└──────────────────────────────────────────────────────────────┘
```

`techtiesai-png/XINGESTIONV2` remains the **core entire ingestion repository and integration anchor**. The fact that protocol research/runtime is separated does not fragment the system contract: `protocol-integration.md`, pinned release manifests and synchronized documentation keep the repositories bonded.

---

# 3. Core architecture

```text
 Parent/NOS / local clients
             |
             v
+-------------------------------------------------------------+
| 1. CAPABILITY / NORTHBOUND CONTRACT                         |
| SEARCH_TWEETS | TWEET_REPLIES | USER_TIMELINE | ...        |
| typed inputs/outputs | fidelity | freshness | pagination    |
+-----------------------------+-------------------------------+
                              |
                              v
+-------------------------------------------------------------+
| 2. DURABLE CONTROL + SCHEDULING                             |
| PostgreSQL tasks -> outbox -> Redis delivery -> workers     |
| idempotency | lease/fence | retry | DLQ/replay | priority   |
| monitors | backfill scheduling | coalescing | backpressure  |
+-----------------------------+-------------------------------+
                              |
                              v
+-------------------------------------------------------------+
| 3. PRODUCTION ACQUISITION COORDINATION                      |
| CapabilityPlanner                                           |
| Session/Identity Manager                                    |
| Network/Proxy allocation                                    |
| approved X-rev release/recipe routing                       |
+-----------------------------+-------------------------------+
                              |
                              v
+-------------------------------------------------------------+
| 4. PINNED X-REV PROTOCOL RUNTIME                            |
| ProtocolRequest + SessionContext + transport + RawSink      |
| X request construction | parser | pagination | typed errors |
| exact runtime/bundle/recipe provenance                      |
+-----------------------------+-------------------------------+
                              |
                              v
                            X
                              |
                              v
+-------------------------------------------------------------+
| 5. PRODUCTION RAW EVIDENCE PLANE                            |
| immutable raw body/object ref | request provenance          |
| task/run/session-safe refs | X-rev recipe/runtime versions  |
+-----------------------------+-------------------------------+
                              |
                              v
+-------------------------------------------------------------+
| 6. PRODUCTION NORMALIZATION / ENTITY DATA PLANE             |
| canonical tweets/users/lists/communities                    |
| relationship edges | observations | time semantics          |
| idempotent reprocessing                                     |
+-----------------------------+-------------------------------+
                              |
              +---------------+---------------+
              |                               |
              v                               v
      analytics/alerts/briefs           APIs / parent exports

Production telemetry from stages 3–6
              |
              v
+-------------------------------------------------------------+
| 7. PROTOCOL HEALTH FEEDBACK                                 |
| capability/release/recipe/session-cohort evidence           |
| investigation package / degradation signal                 |
+-----------------------------+-------------------------------+
                              |
                              v
                      X-rev-os research loop
```

These are logical boundaries. Do not split them into microservices unless there is a real scaling/failure-isolation/ownership reason.

---

# 4. Capability contract

The canonical `CapabilitySpec` belongs to XINGESTIONV2 because it is the stable product/parent-system contract.

The first machine-readable contract artifact is implemented at:

```text
xingestion/contracts/capabilities.v1.json
artifact_schema_version: 1
```

It is shipped as package data and loaded through the typed
`xingestion.capabilities.CapabilityCatalog`. `CapabilityRequest` remains
protocol-neutral, while `CapabilityPlanner` selects an approved compatible
route. Durable tasks use `CAPABILITY_REQUEST`; the former `X_KEYWORD_SEARCH`
payload is accepted through an explicit compatibility translator rather than
remaining the canonical contract.

A capability describes **what data behavior is required**, never the underlying X endpoint.

Example:

```text
CapabilitySpec: SEARCH_TWEETS
contract_version: 1
inputs:
    query
    product
    cursor?
    page_size?
required fidelity:
    real tweet IDs
    text/author fields required by contract
pagination semantics:
    opaque continuation token
provenance requirements:
    raw evidence ref + acquisition release/recipe
```

Hard rule: capability contracts contain no:

- X query/document IDs;
- feature bundles;
- Twikit/twscrape types;
- browser selectors;
- X-specific request headers;
- provider-specific public URL names.

X-rev-os publishes `ProtocolCapabilityBinding`s linking a compatible capability-contract version to validated acquisition recipe revisions.

Until the future X-rev production runtime route exists, the planner contains a
bounded legacy source-adapter route for `SEARCH_TWEETS/Latest`. That route
declares its supported input subset and rejects `Top`, `Media`, or filters it
cannot honor rather than silently returning weaker semantics.

---

# 5. Durable Control Plane

Segments 1–3 implemented the substantial foundation.

Responsibilities:

- logical task identity/idempotency;
- task state transitions;
- schedules/priorities;
- transactional outbox;
- delivery acknowledgement;
- task execution leases/fencing/heartbeats;
- durable retry/backoff;
- dead-letter archive/replay;
- crash recovery;
- future monitor/backfill scheduling/backpressure.

PostgreSQL remains the execution authority unless measured evidence justifies migration.

Redis Streams is delivery infrastructure, not the business/state authority.

Queue/task code must not import X operation/query/parser code.

### Production retry authority

XINGESTIONV2 exclusively owns production retry policy.

The X-rev runtime performs zero hidden production retries and returns typed retry guidance only.

This prevents retry multiplication and keeps attempt history durable/auditable.

### Remaining hardening before scale claims

The current verified foundation should be strengthened as measured/needed with:

- unique lease token/epoch fencing to eliminate same-worker-ID ABA ambiguity;
- Redis dataset-loss/redrive/reconciliation;
- stream trimming/retention;
- migration-history hardening;
- high-scale/chaos/soak tests.

These do not require discarding Segments 1–3.

---

# 6. Capability Planner / approved route selection

The planner receives a canonical `CapabilityRequest` and selects an **approved compatible acquisition route**, not an arbitrary X endpoint.

Inputs may include:

```text
capability ID/version
fidelity
freshness
priority/traffic class
session/auth availability
network availability
approved X-rev release catalog
recipe health/compatibility
```

Output conceptually:

```text
AcquisitionPlan
    capability_request
    protocol_release_manifest
    acquisition_recipe_revision
    required auth class
    page/checkpoint policy
    production traffic class
```

The planner must not inspect cookies/passwords or contain query IDs.

A routing change among already approved compatible releases must not require queue schema rewrites.

---

# 7. Production Session / Identity / Network Plane

Session state is independent from the task queue and independent from X protocol definitions.

Model at least:

```text
Account
CredentialRef
SessionArtifact
SessionHealth
SessionLease
NetworkContext / NetworkRoute
Budget/Cooldown observations
```

A session lease is not the same thing as session health.

Responsibilities:

- max concurrency;
- lease fencing/reclaim;
- cooldown/revocation/refresh-required state;
- safe secret references;
- session affinity where validated protocol chains require it;
- selection of production network/proxy/region;
- operation/capability budget observations;
- supplying ephemeral `SessionContext` to X-rev without exposing long-lived credentials.

The adapter may resolve cookies and optional authorization header material from
the leased session's secret reference when required by the pinned X-rev auth
profile. Those values are ephemeral secret fields, not task payload, safe
metadata, revision content or protocol-bundle data.

X-rev owns X-specific attachment semantics (which cookies/header values are derived/attached). XINGESTION owns which production session/network route is used.

Losing one session should not disable a capability when equivalent approved sessions remain.

---

# 8. X-rev protocol runtime boundary

Detailed protocol design lives in `techtiesai-png/X-rev-os`.

The production-facing contract is defined in `protocol-integration.md` and X-rev `architecture.md`.

Conceptually:

```text
ProtocolRequest
SessionContext
NetworkContext / HttpTransport
RawEvidenceSink
ProtocolReleaseManifest
        |
        v
X-rev runtime
        |
        +--> AcquisitionResult
        +--> typed ProtocolError
```

X-rev owns:

- request/operation construction;
- parser implementations;
- pagination interpretation;
- transaction/client-request metadata;
- auth/session attachment semantics;
- protocol-specific error classification;
- exact recipe dependency composition.

XINGESTION must not duplicate this behavior after migration.

### Release pinning

Version independently:

```text
xrev-runtime
protocol-bundle
capability-contract
```

A `ProtocolReleaseManifest` binds the exact tested versions/checksums/validated recipes.

Production uses an exact approved manifest, never research `latest`.

---

# 9. Production Raw Evidence Plane

Raw evidence is a durability boundary, not merely debug logging.

XINGESTIONV2 supplies the runtime's `RawEvidenceSink`.

Every relevant protocol response should become a durable production raw reference before parser success is treated as safe acquisition success.

Recommended physical architecture:

```text
object storage / content-addressed compressed blobs
        +
PostgreSQL metadata/provenance index
```

rather than storing all large raw bodies directly in PostgreSQL.

Metadata should include enough safe provenance to reprocess/investigate:

```text
raw_capture_id / object hash/ref
execution attempt/task
capability
X-rev runtime/bundle/recipe
operation/parser/pagination revisions
safe session/network pseudonymous refs
captured_at
HTTP status
schema fingerprint
cursor/page context
```

Never persist secret cookie/auth values as ordinary request provenance.

If raw storage is a required invariant and the sink is unavailable, acquisition should backpressure/fail rather than silently bypass evidence.

---

# 10. Production Normalization / Entity Data Plane

X-rev returns protocol-normalized capability records; XINGESTIONV2 owns the larger canonical production data model.

Separate:

```text
raw acquisition evidence
protocol-normalized page/records
canonical entities
relationship edges
observations over time
derived analytics
```

### Identity

Platform object IDs are primary identity when available. Never merge distinct X objects merely because their text hashes match.

### Observation semantics

Engagement/profile counts are observations over time, not additive events.

### Time semantics

Keep distinct:

```text
source_created_at
captured_at
first_seen_at
last_seen_at
source_updated_at where observable
normalized_at
```

### Reprocessing

A parser/normalizer/data-model bug must be repairable from retained evidence without recollecting where retention permits.

Production normalization should be asynchronous/idempotent relative to acquisition so analytics/model bugs do not cause repeated X calls.

---

# 11. Monitoring / scheduler architecture

Provider-like monitoring must not be implemented as naive thousands of independent scrape timers.

Model persistent subscriptions:

```text
subscription_id
canonical target/query
cadence/freshness objective
priority
watermark/checkpoint
next_due_at
max catch-up window
consumer/fanout targets
```

Scheduler responsibilities:

- coalesce identical due acquisitions where safe;
- maintain cursor/watermark state;
- deduplicate incremental results;
- detect gaps;
- schedule bounded backfills;
- jitter catch-up after outages;
- enforce priority/traffic classes;
- protect sessions from thundering herds;
- expose monitor lag.

Example:

```text
100 downstream subscribers monitoring the same public user
            |
            v
1 due acquisition where semantics/session visibility permit
            |
            v
100 downstream fanout deliveries
```

Cache/coalescing keys must include auth/visibility/fidelity/freshness dimensions where results can differ.

---

# 12. Protocol health and research feedback

XINGESTIONV2 owns **production telemetry**; X-rev-os owns **deep protocol diagnosis and candidate validation**.

Production should measure by:

```text
capability
X-rev release/recipe
operation/parser/pagination provenance
session/account cohort
network/region class
error code/scope hint
schema fingerprint
latency/yield
```

A production degradation can produce:

```text
last known success
first known failure
failure distributions
session/network cohort evidence
raw evidence refs
schema fingerprints
parser/pagination warnings
pinned release/recipe
```

for an X-rev investigation package.

### One-session evidence rule

Do not label a failure conclusively `session-local` vs `global` when evidence only comes from one context. Preserve unknown scope.

### Release lifecycle

X-rev validates exact recipe compositions and produces approved releases.

XINGESTIONV2 controls production rollout/rollback among approved compatible releases.

Future automatic failover is limited to already approved compatible alternatives. New protocol discovery stays out of the production worker path.

---

# 13. Analytics / alerts / briefs

These are downstream consumers.

Acquisition does not invoke external LLMs or rebuild analytics rollups inside its critical completion transaction.

Derived systems should consume canonical/protocol evidence asynchronously and be rebuildable.

Failures here must not force recollection from X.

---

# 14. Parent/NOS integration boundary

Parent clients should see stable versioned capability/job/data contracts, not:

- Redis stream names;
- internal task tables;
- X query IDs;
- parser versions unless explicitly requested as provenance;
- session cookies/secrets;
- research database concepts.

Use an anti-corruption boundary that keeps X protocol internals inside X-rev/XINGESTION integration.

Final transport/auth (mTLS, workload identity/JWT, gateway auth, etc.) depends on parent/NOS trust-model decisions.

---

# 15. Failure-isolation matrix

| Failure | Intended blast radius / response |
|---|---|
| One X operation/recipe changes | Dependent approved route/capabilities degrade; control plane remains healthy; X-rev investigation begins. |
| Shared X auth/transaction mechanism changes | All actual dependent recipes may degrade together; diagnose shared dependency rather than pretending per-operation isolation. |
| X response schema/parser changes | Raw evidence persists; affected recipe/parser release degrades; production data can be reprocessed after repair. |
| Pagination changes | Affected recipe/page progression degrades; task/queue and unrelated capabilities continue. |
| One session invalidated | Session quarantined/cooldown; other valid compatible sessions continue. |
| Twikit breaks | Approved first-party X-rev routes continue after cutover. |
| X-rev research browser breaks | Discovery/research pauses; approved production releases continue. |
| X-rev production runtime release bad | Controlled rollback to prior approved compatible release. |
| Redis temporarily unavailable | Delivery pauses; durable task/outbox truth remains in PostgreSQL. |
| Redis dataset lost | Redrive/reconciliation reconstructs eligible deliveries from durable state; no logical task should depend solely on Redis. |
| PostgreSQL unavailable | New authoritative task transitions stop; workers must not guess ownership. Use HA rather than pretending this dependency is localizable. |
| Raw object store unavailable | If raw-before-success is required, acquisition backpressures/fails rather than discarding evidence. |
| Production normalizer broken | Raw/protocol evidence remains; normalization backlog/reprocessing handles repair. |
| Analytics/brief system broken | Acquisition/normalization continue. |
| Health detector false positive | Require hysteresis/evidence/operator controls before destructive quarantine; approved stable release remains recoverable. |
| Bad candidate from research | Candidate remains outside normal production until approved release gate. |
| 10,000 tasks arrive | Durable admission/backpressure; do not translate directly to 10,000 simultaneous X requests. |
| Monitor catch-up storm | Coalescing/jitter/priority/catch-up ceilings protect sessions/network/database. |

---

# 16. Scalability expectations

The likely first real bottleneck is X-side acquisition capacity, not the task queue:

```text
usable sessions
per-operation/account limits
network/proxy reputation
challenge rates
session affinity
```

### Workers

Scale independently by work class when measurements justify it:

- control/acquisition workers;
- production normalization workers;
- browser/research workers live in X-rev, not production path;
- analytics workers.

### PostgreSQL

Keep it while it works. Watch:

- task/outbox write volume;
- execution-attempt/history growth;
- monitor scheduling load;
- observation/canonical writes;
- lock/index behavior.

Partition/archive large historical tables only when measured growth justifies it.

### Redis Streams

Keep it while measured performance is sufficient. Add stream trimming, lag/pending-age metrics and redrive.

Do not move to Kafka/Pulsar merely because the target scale sounds large; migrate when durable replay/consumer topology/throughput requirements actually exceed the current design.

### Raw storage

Use object storage for large response bodies. Monitor bytes/day, object count, compression, PUT/read cost and reprocessing throughput.

### Pagination/backfills

Persist/checkpoint by page where appropriate. Do not accumulate huge backfills entirely in worker memory.

Use separate traffic classes such as:

```text
INTERACTIVE
MONITOR
BACKFILL
RESEARCH/CANARY
```

so historical backfills cannot starve freshness-sensitive monitoring.

### Multi-region

Delay until required. Sessions/network identity may need home-region/egress affinity; do not assume stateless region hopping is safe.

---

# 17. Security and evidence rules

- long-lived credentials behind an approved secret boundary;
- no secrets in tasks/logs/raw metadata/fixtures/investigation packages;
- raw evidence retention/deletion governed by explicit policy;
- protocol release artifacts signed/checksummed/pinned as deployment policy matures;
- reference-library/code incorporation records source/version/license;
- state-changing account actions are out of scope for ingestion core.

---

# 18. Documentation and cross-repository integrity

The documentation system is itself an architecture-control mechanism.

Primary XINGESTIONV2 documents:

```text
AGENTS.md
architecture.md
protocol-integration.md
plan.md
implemented.md
```

Primary X-rev-os documents:

```text
AGENTS.md
architecture.md
plan.md
implemented.md
```

Rules:

1. update architecture docs when durable contracts change;
2. update plan/status when implementation order or acceptance gates change;
3. append factual implemented/research evidence when work occurs;
4. update both repositories when the shared integration contract changes;
5. once integrated, record the exact pinned X-rev release/runtime/bundle/commit in XINGESTIONV2 `implemented.md`;
6. do not leave important decisions only in conversations;
7. if documentation is restructured, migrate history/cross-links coherently.

Codex/researchers may propose ADRs, generated indexes, machine-readable decision logs or another stronger model. The criterion is not preserving filenames forever; it is preserving authoritative ownership, decision history, evidence, update discipline and cross-repository traceability.

---

# 19. Architectural bottom line

XINGESTIONV2 should be the durable production ingestion machine.

X-rev-os should be the fast-moving protocol microscope and canonical X-specific runtime source.

The seam between them is deliberately small and versioned:

```text
canonical CapabilitySpec
        |
ProtocolCapabilityBinding
        |
pinned ProtocolReleaseManifest
        |
typed X-rev runtime API
        |
XINGESTION production Session/Network/RawEvidenceSink
```

If this seam stays stable, X can change query IDs, client bundles, parsers, pagination or shared request mechanisms without forcing rewrites of the task ledger, monitoring scheduler, canonical data model or parent API.
