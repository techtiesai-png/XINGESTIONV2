# XINGESTIONV2 Target Architecture

> Status: architecture decision document. This describes the intended system after the 2026-08-08 redesign. Existing code is migrated toward this architecture segment-by-segment; the document must not be read as a claim that every component already exists.

Read together with [`AGENTS.md`](./AGENTS.md), [`plan.md`](./plan.md), and [`implemented.md`](./implemented.md).

---

## 1. Mission

Build an independently owned, production-grade X/Twitter data-ingestion subsystem that:

1. exposes stable **data capabilities** such as tweet search, tweet lookup, replies, timelines, followers, lists, communities, and monitoring;
2. owns the primary implementation used to communicate with observable X web/API protocols rather than depending on one third-party scraping library at runtime;
3. can use open-source libraries and provider documentation as **research inputs and compatibility references**, not as the single source of protocol knowledge;
4. detects protocol drift, validates alternate operation versions, fails over to already validated alternatives, and eventually supports carefully bounded self-healing;
5. preserves raw evidence and provenance so parsers/normalizers can be repaired and replayed without recollecting everything;
6. scales horizontally and fails in isolated domains rather than turning one broken X operation into a system-wide outage;
7. remains cleanly embeddable into a larger ingestion platform later.

The architectural benchmark is the *service shape* of serious web-data and X-data platforms: broad capability coverage, stable customer-facing contracts, queues, sessions, pagination/checkpointing, independent workers, monitoring, recoverability, and versioned extraction behavior. Public provider documentation is a capability benchmark only; we do not claim knowledge of any provider's private backend design.

---

## 2. The central design change

The original prototype effectively behaved like:

```text
XINGESTIONV2
    |
    v
Twikit
    |
    v
X internal/unofficial web API
```

That makes Twikit both an implementation dependency and a large part of the protocol knowledge base.

The target is:

```text
                         STABLE CAPABILITY CONTRACT
                                  |
                                  v
                        Capability Planner / Router
                                  |
                     +------------+-------------+
                     |                          |
                     v                          v
            First-party X Protocol       Browser Observation
                 Adapter                   / Recovery Adapter
                     |                          |
                     +------------+-------------+
                                  |
                                  v
                                 X
```

Reference projects such as Twikit/twscrape move outside the primary runtime path:

```text
Twikit / twscrape / other research code
provider capability documentation
browser/network observations
historical fixtures
              |
              v
      RESEARCH / INTELLIGENCE TOOLS
              |
              v
     our versioned protocol registry
```

The primary production path should ultimately be ours.

---

## 3. Do not design around provider endpoint names

A public provider endpoint is a product contract, not proof of a one-to-one X endpoint underneath it.

For example, a provider can expose `GET_FOLLOWERS`, while internally that capability might require:

- one or more X operations;
- cursor traversal;
- cached user objects;
- normalization;
- deduplication;
- retries and session selection;
- multiple protocol versions.

Therefore the canonical specification is a **capability catalog**, not a copied list of third-party URLs.

Example:

```text
Capability: USER_FOLLOWERS
Input:
  user_id
  cursor?
  page_size?

Output:
  users[]
  next_cursor?
  has_more
  provenance

Acquisition plan v12:
  protocol operation UserFollowers/v4
  auth class AUTHENTICATED_WEB
  parser UserTimelineParser/v7
```

The provider-style API can later map onto this stable capability layer without leaking X protocol details.

---

## 4. Architecture overview

```text
                               +----------------------+
                               | Parent ingestion     |
                               | system / local API   |
                               +----------+-----------+
                                          |
                                 versioned CapabilityRequest
                                          |
                                          v
+--------------------------------------------------------------------------------+
|                           NORTHBOUND CAPABILITY LAYER                           |
| search_tweets | tweet_by_id | replies | user_timeline | followers | monitors...|
+-----------------------------------------+--------------------------------------+
                                          |
                                          v
+--------------------------------------------------------------------------------+
|                            DURABLE CONTROL PLANE                               |
| PostgreSQL task ledger -> transactional outbox -> delivery bus -> workers      |
| leases | heartbeat | retries | dead letters | replay | priorities | schedules  |
+-----------------------------------------+--------------------------------------+
                                          |
                                          v
+--------------------------------------------------------------------------------+
|                         CAPABILITY PLANNER / ROUTER                            |
| required capability + fidelity + freshness + auth class                       |
|          -> choose validated AcquisitionPlan / OperationVersion                |
+------------------+----------------------+-------------------+-------------------+
                   |                      |                   |
                   v                      v                   v
        +--------------------+  +--------------------+  +------------------------+
        | First-party X      |  | Browser observation|  | Transitional/reference |
        | protocol adapter   |  | / recovery adapter |  | adapters (non-primary) |
        +---------+----------+  +---------+----------+  +------------+-----------+
                  |                       |                          |
                  +-----------------------+--------------------------+
                                          |
                                          v
                                          X
                                          |
                                          v
+--------------------------------------------------------------------------------+
|                              RAW DATA PLANE                                    |
| immutable response/capture envelope | request provenance | schema fingerprint  |
+-----------------------------------------+--------------------------------------+
                                          |
                                          v
+--------------------------------------------------------------------------------+
|                        NORMALIZATION / ENTITY DATA PLANE                       |
| canonical tweets/users/lists/communities | relationship edges | observations   |
| parser version | first/last seen | engagement snapshots | source timestamps    |
+---------------------------+-----------------------------+-----------------------+
                            |                             |
                            v                             v
                    downstream analytics          parent-system export
                    alerts / briefs / API          events / query API

                +--------------------------------------------------+
                |             PROTOCOL INTELLIGENCE                |
                | probes | drift detector | capture analyzer       |
                | schema diff | candidate registry | canaries      |
                | promotion/rollback | investigation packages      |
                +--------------------------+-----------------------+
                                           |
                                           v
                                  Operation Registry
```

---

# 5. Hard module boundaries

These boundaries are architectural invariants. They exist specifically so one broken part does not force rewrites elsewhere.

## 5.1 Capability contract

The northbound contract describes **what data is wanted**, never which X endpoint/library should be used.

Examples:

- `SEARCH_TWEETS`
- `TWEETS_BY_IDS`
- `TWEET_REPLIES`
- `TWEET_QUOTES`
- `TWEET_RETWEETERS`
- `THREAD_CONTEXT`
- `USER_BY_ID`
- `USER_BY_HANDLE`
- `USER_TIMELINE`
- `USER_MENTIONS`
- `USER_FOLLOWERS`
- `USER_FOLLOWER_IDS`
- `USER_FOLLOWING`
- `FOLLOW_RELATIONSHIP`
- `LIST_TIMELINE`
- `LIST_MEMBERS`
- `LIST_FOLLOWERS`
- `COMMUNITY_INFO`
- `COMMUNITY_TIMELINE`
- `MONITOR_USER_TWEETS`
- `MONITOR_QUERY`

Each capability has a versioned typed input, canonical output type, pagination semantics, required fidelity, and provenance requirements.

**Rule:** no X query IDs, feature flags, Twikit types, Playwright selectors, or provider-specific URLs are allowed in the capability contract.

---

## 5.2 Durable control plane

This is the work completed substantially in Segments 1-3.

Responsibilities:

- task identity/idempotency;
- task state transitions;
- schedules/priorities;
- outbox delivery;
- delivery acknowledgement;
- task leases/heartbeats;
- retry/backoff;
- dead-letter archive/replay;
- crash recovery.

It should eventually consume generic `CapabilityRequest` payloads.

**Rule:** the control plane does not import X protocol code and does not know whether a capability is implemented through HTTP, GraphQL, browser capture, or another source.

If the X search operation breaks tomorrow, task creation/retry/replay still works.

---

## 5.3 Capability planner / router

The planner converts a capability request into a versioned **AcquisitionPlan**.

Example:

```text
CapabilityRequest:
  SEARCH_TWEETS(query="india", sort="latest")

Planner result:
  plan_id: search-latest/web-v17
  adapter: X_INTERNAL_WEB
  operation_version: SearchTimeline/v17
  parser_version: TweetTimeline/v9
  auth_class: AUTHENTICATED_WEB
  page_limit: 20
```

Selection can consider:

- capability coverage;
- operation health;
- required authentication class;
- response fidelity;
- freshness;
- current session/budget availability;
- whether a candidate is stable/canary/quarantined;
- future cost/latency policies.

**Rule:** routing changes must not require queue/schema rewrites.

---

## 5.4 What an adapter means here

Use the adapter pattern only at true external/transport boundaries.

An `AcquisitionAdapter` receives an already planned capability operation and returns a standardized raw envelope.

Conceptually:

```python
class AcquisitionAdapter(Protocol):
    async def execute(
        self,
        plan: AcquisitionPlan,
        request: CapabilityRequest,
        session: SessionLease | None,
    ) -> RawAcquisitionEnvelope:
        ...
```

Target runtime adapters:

### `XInternalWebAdapter` — primary

Our first-party implementation of known X web/internal protocol operations.

Owns:

- supported transport/request construction;
- operation registry lookup;
- headers/cookies/session attachment;
- pagination/cursor extraction;
- raw response capture;
- protocol-specific error mapping.

### `BrowserObservationAdapter` — diagnostic/recovery

Browser-based path used for:

- observing how the X client obtains data;
- capturing authorized network responses;
- validating candidate operation behavior;
- selected recovery cases where browser fidelity is required.

It is not the default high-throughput collector because browser execution is substantially more expensive to scale than direct protocol requests.

### `LegacyLibraryAdapter` — transitional only

Twikit or another library may remain temporarily as:

- regression oracle;
- migration comparison path;
- fixture source;
- optional emergency fallback while first-party capabilities are being implemented.

It should not be the final protocol authority.

**Important:** do not create dozens of meaningless adapters. Queueing, normalization, analytics, health scoring, and retry logic are modules with their own contracts, not "adapters" simply for architectural style.

---

## 5.5 First-party protocol core

This is the most important new subsystem.

It should represent X protocol knowledge as versioned data + tested code rather than scattered literals.

### OperationDefinition

A versioned operation definition should be able to represent, where applicable:

```text
operation_key
capability
version
transport
method
path / operation identifier
variable schema
feature/config schema
auth class
required session state
pagination model
parser version
expected response invariants
known response schema fingerprint(s)
introduced_at
last_validated_at
status
```

Statuses:

```text
CANDIDATE
CANARY
STABLE
DEGRADED
QUARANTINED
RETIRED
```

Operation definitions should be immutable after promotion. A changed protocol contract becomes a new version.

### Protocol client

The transport layer handles reusable mechanics only:

- HTTP client lifecycle;
- request timeout;
- connection pooling;
- session/cookie attachment;
- safe request/response metadata capture;
- protocol error classification;
- correlation IDs;
- redaction.

Capability-specific response parsing stays outside the generic transport client.

---

## 5.6 Session and budget manager

Account/session state is independent of protocol operation definitions and independent of the task queue.

Responsibilities:

- session identity/metadata;
- active leases;
- `max_concurrency`;
- per-operation/per-capability usage budgets;
- known cooldown windows;
- last success/failure;
- refresh-required state;
- revocation/quarantine state;
- session cookies/tokens via secret references;
- session affinity where an operation chain requires it.

Suggested health states:

```text
HEALTHY
COOLDOWN
REFRESH_REQUIRED
DEGRADED
QUARANTINED
REVOKED
```

A lease is *not* a health state.

**Rule:** losing one session must not break the capability if other valid sessions/plans exist.

---

# 6. Protocol intelligence and self-healing

Self-healing should be staged. Do not begin with arbitrary autonomous code modification.

## Level 0 — observability

For every stable operation track:

- success/failure rate;
- latency;
- error class/status;
- parser failures;
- pagination anomalies;
- schema fingerprints;
- missing required fields;
- session-specific vs global failure distribution;
- first-failure and last-success times.

Output:

```text
SEARCH_TWEETS          HEALTHY
USER_TIMELINE          HEALTHY
TWEET_REPLIES/v6       DEGRADED
FOLLOWER_IDS/v3        BROKEN
```

This level is mandatory before any automatic repair.

---

## Level 1 — route around known failures

If two previously validated stable operation versions/plans exist and one degrades:

```text
stable A fails health threshold
        |
        v
router selects stable B
        |
        v
A becomes DEGRADED / investigation starts
```

No new endpoint is invented automatically.

This is the safest form of self-healing.

---

## Level 2 — declarative candidate discovery

Research tooling can observe changes from authorized browser/network captures or maintained client artifacts and produce a **candidate operation definition**.

Candidate discovery should capture enough evidence to answer:

- what operation changed;
- which request fields changed;
- which response shape changed;
- which capability it appears to satisfy;
- whether pagination still works;
- which auth class is required;
- which old fixtures are no longer compatible.

The candidate is not automatically production-stable.

---

## Level 3 — validation, canary, promotion

Candidate pipeline:

```text
DISCOVER
   |
   v
STATIC / SCHEMA VALIDATION
   |
   v
FIXTURE / CONTRACT TESTS
   |
   v
LIVE GATED CANARY
   |
   v
COMPARE WITH STABLE / EXPECTED INVARIANTS
   |
   +--> fail -> QUARANTINE + investigation package
   |
   v
PROMOTE -> STABLE
```

Promotion and rollback are audited operations.

---

## Level 4 — bounded automatic repair

Only narrow, explainable repairs should be eligible for automatic promotion, for example:

- a known response field moved to a validated alternate location;
- an operation identifier changed while variables/results satisfy the same validated contract;
- an already-known alternate parser version becomes the correct parser;
- a stable alternate operation is promoted after canary success.

Do **not** allow an LLM/agent to arbitrarily rewrite live protocol code and deploy it without tests/canary/rollback.

---

## Level 5 — investigation escalation

When safe automatic repair is impossible, create a machine-readable investigation package for a researcher/Codex.

Example contents:

```text
capability
broken operation/version
last known good time
first known bad time
failure distribution
old sanitized request metadata
new sanitized observation metadata
schema fingerprint diff
parser failure path
old/new fixtures
candidate operations discovered
session-independent evidence
recommended files/tests to inspect
```

This turns reverse engineering into a bounded engineering workflow rather than an emergency manual hunt.

---

# 7. Protocol research lab

Research/inspection tooling must be isolated from the production acquisition path.

Inputs can include:

- our own successful/failed protocol observations;
- browser network capture from authorized research sessions;
- public client artifacts where appropriate;
- current open-source X libraries;
- historical fixtures;
- public provider capability documentation.

Outputs:

- candidate operation definitions;
- capability mappings;
- sanitized fixtures;
- schema fingerprints;
- parser hypotheses;
- compatibility reports.

### Why isolate it?

If a research parser crashes, production collection should continue using the current stable registry.

If Twikit breaks, the research comparison loses one signal; production first-party protocol operations should continue.

If browser capture breaks, automatic discovery pauses; stable protocol operations should continue.

### Licensing/provenance rule

Reference implementations are not automatically copied verbatim. Record source/version/license and distinguish:

- observed protocol facts;
- independently implemented behavior;
- reusable code whose license permits incorporation.

---

# 8. Raw acquisition envelope

Every acquisition path returns the same top-level envelope before normalization.

Conceptually:

```text
RawAcquisitionEnvelope
  request_id
  task_id
  capability
  acquisition_plan_id
  adapter
  operation_key
  operation_version
  parser_hint
  session_db_id (safe reference only)
  captured_at
  source_status
  cursor_in
  cursor_out
  sanitized_request_fingerprint
  response_schema_fingerprint
  raw_payload_ref / raw_payload_hash
  fidelity
  warnings[]
```

Raw payloads should be immutable and content-addressable where practical.

Large raw payloads should eventually live in object/blob storage rather than expanding the control-plane database indefinitely.

**Critical property:** a parser bug must not force recollection if the raw payload is still available.

---

# 9. Normalized data plane

The normalized model must be capability-neutral enough to integrate into a larger system while preserving X-specific fields where necessary.

Core categories:

### Entities

- Tweet/Post
- User/Profile
- List
- Community
- Article/media references

### Relationships / edges

- user follows user;
- tweet replies to tweet;
- tweet quotes tweet;
- user retweeted tweet;
- user belongs to list/community;
- mention relationships.

### Observations

- engagement counters;
- profile counters;
- availability/deletion observations;
- membership/follow observations where temporally meaningful.

### Provenance

Every canonical or observation record should be traceable to:

- capability request;
- task/run;
- acquisition plan;
- protocol operation/version;
- parser/normalizer version;
- capture time;
- raw payload reference/hash.

This is necessary for trustworthy reprocessing and drift investigations.

---

# 10. Downstream analytics are not part of acquisition correctness

Current analytics/alerts/brief logic must eventually be decoupled from the collection worker.

Target:

```text
acquisition -> raw envelope -> normalized event/object
                                 |
               +-----------------+------------------+
               |                 |                  |
               v                 v                  v
            analytics          alerts             briefs
```

A broken brief generator must not stop tweet collection.

A bad trend formula must be rebuildable from normalized/raw data.

The ingestion worker should not know how executive briefs are generated.

---

# 11. Capability catalog

The initial catalog should use major X-data providers only as a completeness checklist.

## Tweets

- advanced/latest/top search;
- tweets by ID(s);
- replies;
- replies with alternate sorting where observable;
- quotations;
- retweeters;
- thread context;
- article/detail expansion;
- conversation traversal;
- incremental/query monitoring.

## Users

- by ID;
- by handle;
- batch lookup;
- search;
- timeline;
- tweets + replies where separately observable;
- mentions;
- followers with profiles;
- follower IDs/bulk identity edges;
- following;
- follow relationship;
- profile/about metadata;
- verified followers where meaningful/observable.

## Lists

- metadata where available;
- timeline;
- members;
- followers.

## Communities

- metadata;
- timeline/search;
- membership where observable and required.

## Monitoring

- monitor user tweets;
- monitor query/filter;
- incremental checkpointing;
- deduplicated event delivery;
- reconnect/backfill/gap detection.

## Mutating/account actions

Posting, following, liking, login automation, and other state-changing actions are **not part of the ingestion core**. If ever required, place them behind a separately authorized Action Plane with different audit/security requirements. Do not contaminate read/ingestion architecture with mutation semantics.

---

# 12. Failure isolation matrix

| Failure | Expected blast radius | System response |
|---|---|---|
| One X operation version changes | One capability/plan | mark degraded, choose validated alternate, investigate |
| First-party parser breaks | Parser/version | preserve raw data, quarantine parser, reprocess later |
| Twikit breaks | Research/transitional adapter | primary first-party acquisition unaffected after cutover |
| Browser observation breaks | Discovery/recovery only | stable operations continue; discovery alert raised |
| One session becomes invalid | That session | cooldown/quarantine; route to other session |
| All sessions for one auth class unavailable | Capabilities needing that auth class | queue/backoff with explicit degraded state |
| Redis unavailable | Delivery pauses | tasks/outbox remain durable in PostgreSQL |
| Worker dies | One in-flight lease | DB lease expires, Redis pending entry reclaimed |
| PostgreSQL unavailable | Control plane | stop new authoritative work rather than split-brain |
| Normalizer bug | A schema/normalizer version | raw data remains; reprocess after fix |
| Analytics bug | Analytics only | acquisition continues; rebuild analytics later |
| Brief provider fails | Brief subsystem only | acquisition/normalization continue |
| Parent API contract changes | Integration adapter/version | internal capability contract remains stable |
| Candidate self-heal is wrong | Candidate/canary traffic only | quarantine/rollback; stable version remains active |

If an implementation violates these blast-radius expectations, it should be treated as an architectural defect.

---

# 13. Process boundaries: avoid microservice theatre

Not every module needs a separate network service.

Start with a **modular Python codebase plus a small number of process boundaries** where scaling/failure isolation actually differs.

Recommended processes:

1. **API / parent-ingress service** — creates capability requests.
2. **Outbox dispatcher** — durable DB -> delivery bus.
3. **Protocol workers** — execute first-party protocol capabilities.
4. **Browser observation workers** — expensive browser/network-capture workloads, independently scalable.
5. **Protocol probe/intelligence service** — health probes, drift processing, candidate generation.
6. **Normalizer/data workers** — if normalization becomes heavy enough to decouple from acquisition.
7. **Analytics/alert/brief consumers** — downstream, independently restartable.

Inside those processes, use package/module boundaries rather than unnecessary RPC hops.

---

# 14. Replaceable infrastructure boundaries

The current implementation uses PostgreSQL + Redis Streams. Keep that until measured limits justify a change.

But code should depend conceptually on:

```text
TaskLedger
OutboxStore
DeliveryBus
RawPayloadStore
CanonicalStore
SecretStore
OperationRegistry
HealthStore
```

Current implementations can be:

```text
TaskLedger       -> PostgreSQL
DeliveryBus      -> Redis Streams
RawPayloadStore  -> PostgreSQL initially / object storage later
CanonicalStore   -> PostgreSQL
SecretStore      -> local development implementation initially, approved backend later
```

This lets the parent system eventually replace Redis with Kafka/SQS/NATS/etc. without rewriting protocol logic.

Do not replace infrastructure pre-emptively merely to look enterprise-grade.

---

# 15. Scaling model

Horizontal scale should happen along independent dimensions:

```text
API ingress
    |
Task ledger
    |
+-------------------------+
| protocol worker pool    | scale by request throughput
+-------------------------+
| browser worker pool     | scale by expensive browser demand
+-------------------------+
| normalizer pool         | scale by payload/CPU volume
+-------------------------+
| analytics consumers     | scale independently
+-------------------------+
```

Capability families can later be partitioned if one dominates load.

Session scheduling must be aware of per-session/per-operation budgets so adding workers does not simply overrun the same small identity pool.

Autoscaling should be driven by measured signals such as:

- queue age/depth;
- processing latency;
- session availability;
- operation health;
- CPU/memory;
- DB/Redis saturation;
- retry rate;
- browser demand.

---

# 16. Monitoring and protocol health

Production health is more than `/healthz`.

Minimum dimensions:

### Control plane

- tasks created/enqueued/running/completed;
- queue age;
- retry/dead-letter rate;
- lease expiry/reclaim rate;
- outbox lag.

### Sessions

- usable sessions by auth class;
- leases/concurrency;
- cooldown/quarantine;
- auth failures;
- operation-specific throttling/budgets.

### Capabilities

- success rate by capability;
- latency;
- completeness/fidelity indicators;
- pagination anomalies;
- fallback usage.

### Protocol versions

- stable/canary version;
- schema fingerprint drift;
- parser failures;
- request/response contract changes;
- last known good;
- first known bad;
- candidate validation state.

### Data

- raw payload write failures;
- normalization failures;
- duplicate/re-observation rate;
- provenance completeness;
- processing lag.

---

# 17. What we learned from other scraper architectures

The architecture deliberately borrows *patterns*, not code or private designs.

### Crawlee / Apify pattern

Useful separation:

- request queue with unique keys and explicit reclaim;
- session pool independent from request queue;
- router/handler separate from queue/session state;
- autoscaled concurrency independent from crawl logic;
- result stores separate from request scheduling.

We use the same principle at a larger protocol-capability level: control-plane, session manager, capability router, protocol operation, and data storage must remain separate.

### Zyte pattern

Useful separation:

- direct HTTP acquisition and browser acquisition are separate modes;
- sessions/cookies are shared supporting concerns;
- browser network capture is a first-class diagnostic/acquisition tool;
- extraction behavior can be versioned/pinned;
- retries/error semantics are explicit;
- reverse-engineered/direct requests are cheaper to run than browser execution once they are understood.

For this project, browser observation should therefore help discover/validate first-party protocol operations, while direct protocol execution remains the high-throughput primary path whenever possible.

### X library lesson

Open-source X libraries demonstrate both capability and fragility: protocol/query/schema/login changes repeatedly break assumptions. Therefore a single dependency/library cannot be our protocol authority. We need fixtures, operation versions, health probes, drift classification, raw evidence, and alternate plans.

---

# 18. Migration from the current repository

Do not throw away Segments 1-3.

They become the **Control Plane foundation**.

Current pieces map as follows:

```text
worker_tasks / task_outbox / Redis Streams
    -> Durable Control Plane

TaskRepository / RedisStreamQueue / lease guard
    -> task execution infrastructure

TokenRepository / service_token_leases
    -> early Session Manager, to be redesigned/refined

TwikitSearchAdapter
    -> LegacyLibraryAdapter during migration

PlaywrightSearchAdapter
    -> early BrowserObservation/Recovery adapter

analytics_parser
    -> early Normalizer/Data Plane, to be decoupled

analytics_alerts / analytics_briefs
    -> downstream consumers, not ingestion core

api_server
    -> future northbound/query API, requires redesign
```

Migration order must preserve working control-plane invariants while replacing the protocol acquisition layer underneath them.

---

# 19. Architectural acceptance tests

A design is not production-grade merely because it has many classes/services.

Before calling the architecture mature, prove these properties:

1. Changing an X search operation implementation does not change task-ledger code.
2. Changing Redis to another DeliveryBus implementation does not change protocol operation code.
3. Breaking one capability does not prevent unrelated capabilities from running.
4. Breaking browser discovery does not stop stable first-party protocol operations.
5. Breaking a parser does not destroy raw payloads; the same raw payload can be reparsed.
6. One failed session is isolated; session concurrency/budget limits remain enforced under multiple workers.
7. A candidate operation cannot become stable without validation/canary/promotion evidence.
8. A bad promoted operation can be rolled back without redeploying the entire application.
9. Old operation/parser versions and fixtures remain sufficient to explain historical data provenance.
10. Analytics/brief failures do not stop acquisition.
11. Parent-system integration uses versioned capability/data contracts rather than internal queue tables.
12. Worker/dispatcher crashes do not acknowledge lost work.
13. Scale claims are backed by load/soak/fault measurements, not architecture diagrams.

---

# 20. Explicit non-goals / guardrails

- Do not make TwitterAPI.io or any other provider's route names the canonical architecture.
- Do not depend on Twikit/twscrape as the long-term primary protocol implementation.
- Do not make browser automation the default high-throughput path if a validated direct protocol operation exists.
- Do not let self-healing mean unreviewed arbitrary code rewriting in production.
- Do not mix account-mutating actions into the ingestion core.
- Do not couple analytics/LLM briefs to acquisition success.
- Do not turn every module into a microservice without an operational reason.
- Do not replace PostgreSQL/Redis only because larger companies use different infrastructure; measure the bottleneck first.
- Do not sacrifice raw provenance for a cleaner canonical schema.

---

# 21. External decisions that remain intentionally open

These do not block architecture implementation today:

- production SecretStore/KMS/Vault choice;
- parent ingestion system's final ingress/egress transport and auth contract;
- final storage/retention requirements for raw payloads;
- production hardware/topology and scale/SLO targets;
- authorized live research identities/network environment for gated protocol validation;
- final multi-region strategy.

They are tracked in `implemented.md` and should be requested from the user only when a segment actually requires them.

---

# 22. Bottom line

The system should be treated as five stable planes:

```text
1. CAPABILITY PLANE
   what data is requested

2. CONTROL PLANE
   how durable work is scheduled/retried/recovered

3. PROTOCOL PLANE
   how X is actually queried

4. INTELLIGENCE PLANE
   how protocol drift is detected, investigated, validated and repaired

5. DATA PLANE
   how raw evidence becomes canonical, replayable data
```

A sixth boundary, **Integration/Analytics**, consumes those planes but must not be able to destabilize them.

The strongest architecture is not one where nothing ever breaks. X will change. The target is a system where **the thing that changed is the thing that breaks**, the blast radius is bounded, raw evidence survives, an alternate can be routed safely, and the investigation/repair path is explicit and increasingly automated.
