# XINGESTIONV2 Production Hardening Plan

> Read [`AGENTS.md`](./AGENTS.md) before implementing this plan. Record completed work in [`implemented.md`](./implemented.md).

## 0. Audit baseline

**Audit baseline:** runtime code originally present at commit `8e7771a483d5ea57f440f7f410e7b0bea0176f4c`.

**Target:** turn the current research prototype into a production-grade, integration-ready ingestion subsystem while preserving or improving useful collection capability.

**Stated design envelope:** the existing blueprint claims roughly 50,000–100,000+ items/day. That is only about 0.6–1.2 items/sec on average, but the design should tolerate burst traffic, worker restarts, dependency failures, duplicate delivery, and later integration into a substantially larger system.

### Status legend

- `[ ]` not implemented
- `[~]` partially implemented / replacement in progress
- `[x]` implemented and verified to the level recorded in `implemented.md`
- `P0` correctness, data-loss, security, or system-startup blocker
- `P1` production blocker / major architectural debt
- `P2` scale, operability, integration, or quality improvement

## 1. What actually exists today

The executable system is currently a flat Python repository with these effective components:

```text
Task producers / utilities
  seed_test.py
  task_replay.py
        |
        | currently PostgreSQL only
        v
  worker_tasks table

Redis list queue: queue:x_tasks
        |
        | LPOP
        v
worker.py
  |- service_tokens lease lookup in PostgreSQL
  |- Twikit collection path
  |- optional Playwright recovery path
  |- Pydantic normalization
  `- analytics_parser.py -> PostgreSQL
                             |
                             |- social_insights_feed
                             |- keyword_hourly_rollups
                             |
                             |- analytics_alerts.py
                             |- analytics_briefs.py
                             `- api_server.py

Session maintenance
  token_refresh_service.py
  bulk_account_seeder.py

Infrastructure
  docker-compose.yml -> PostgreSQL + Redis + schema initializer
  systemd/* -> currently empty files
```

The architecture document describes a more complete system than the repository currently contains. In particular, the documented deployment units are empty, the documented `ingestion_engine.py` is absent, and there is no dependency manifest, application Dockerfile, CI pipeline, migration system, test suite, or working queue producer/dispatcher connecting PostgreSQL tasks to Redis.

## 2. Proposed target architecture

Do not patch the current split-brain design indefinitely. Move toward this boundary:

```text
Query / schedule / replay request
            |
            v
PostgreSQL task ledger + transactional outbox
            |
            v
Outbox dispatcher
            |
            v
Redis Streams consumer group --------------+
            |                               |
            v                               | reclaim unacked work
Ingestion workers                           |
            |                               |
            v                               |
SourceAdapter interface                     |
  |- primary structured collector           |
  |- explicitly enabled recovery adapter    |
  `- fixture/mock adapter                   |
            |
            v
Raw observation / provenance store
            |
            +--> canonical source objects
            +--> engagement observations
            +--> idempotent rollups
                       |
                       +--> alert engine
                       +--> brief engine
                       `--> read API

Control plane:
  account/session metadata -> lease manager -> SecretStore reference

Cross-cutting:
  structured logging + metrics + tracing + tests + migrations + CI
```

### Architectural rules for the target

1. **PostgreSQL is the durable control-plane source of truth.** Redis is delivery/acceleration, not the only place a task exists.
2. **Redis transport uses acknowledgement semantics**, preferably Streams + consumer groups rather than destructive list pops.
3. **Collection is behind an adapter interface.** Twikit or any future transport can fail/change without contaminating queue, persistence, analytics, or API code.
4. **Canonical post identity and observations are separate.** A source object is not the same thing as a later measurement of its engagement counters.
5. **Secrets are not mixed with session state.** Credentials/session cookies live behind a secret abstraction or encrypted representation, not an ambiguous plaintext `token_value` column.
6. **Every replayable operation is idempotent and auditable.**
7. **Integration boundaries are explicit.** The subsystem must later be embeddable into a larger system without requiring a rewrite of its core state machine.

---

# P0 — Correctness and breakage

## P0-01 — Repair the broken PostgreSQL/Redis task pipeline

**Current defect**

- `seed_test.py` inserts `PENDING` rows into `worker_tasks`.
- `task_replay.py` also recreates tasks only in `worker_tasks`.
- `worker.py` does not lease from `worker_tasks`; it only `LPOP`s `queue:x_tasks` from Redis.
- No repository component publishes DB tasks into that Redis list.
- `checkout_task(worker_id)` never updates the DB row to `RUNNING`, never writes `leased_at`, and does not use `worker_id`.
- A worker can therefore be completely idle while PostgreSQL contains pending work.

**Required redesign**

- [ ] Make `worker_tasks` the authoritative task ledger.
- [ ] Add durable task identity/idempotency keys.
- [ ] Add a transactional outbox row in the same PostgreSQL transaction that creates/retries a task.
- [ ] Add an outbox dispatcher that publishes to a Redis Stream.
- [ ] Consume with Redis consumer groups (`XREADGROUP`) and acknowledge only after the durable task state is committed.
- [ ] Reclaim abandoned pending entries after lease expiry rather than losing them on process death.
- [ ] Make task state transitions explicit: `PENDING -> ENQUEUED -> RUNNING -> DONE`, with `RETRY_SCHEDULED` and `DEAD_LETTER` branches.
- [ ] Track `lease_owner`, `lease_started_at`, `lease_expires_at`, `completed_at`, and `updated_at`.
- [ ] Use `next_run_at` for real scheduled retry delivery.
- [ ] Close Redis clients during graceful shutdown.

**Acceptance**

- Killing a worker after receiving a task but before completion does not lose the task.
- Re-running a producer with the same idempotency key does not create duplicate logical work.
- DB task state and Redis delivery state can be reconciled automatically.
- `running_tasks` reflects real running work.

## P0-02 — Fix session/token lease concurrency

**Current defect**

`TokenRepository.checkout_token()` row-locks a candidate only during a short transaction and leaves it `ACTIVE`. After the transaction releases, another worker can lease the same token immediately. `last_leased_at` changes ordering but is not an exclusive lease.

**Required redesign**

- [ ] Add lease ownership/expiry (`lease_owner`, `lease_expires_at`) or a dedicated lease table.
- [ ] Atomically lease only sessions below their configured concurrency limit.
- [ ] Add configurable per-session concurrency and request budgets.
- [ ] Release a lease explicitly after work, including error paths.
- [ ] Add a sweeper for expired leases after worker death.
- [ ] Distinguish session state (`HEALTHY`, `COOLDOWN`, `REFRESH_REQUIRED`, `REVOKED`) from whether it is currently leased.

**Acceptance**

- Two workers cannot accidentally exceed the same session's configured concurrency.
- A dead worker does not permanently strand a session.

## P0-03 — Replace exception guessing with a collection error taxonomy

**Current defect**

`worker.py` expects `httpx.HTTPStatusError` to trigger the tier-2 failover path, but the primary collector is Twikit, which has its own exception model. This means expected auth/rate-limit failures can bypass the intended tier transition and fall into the generic retry path.

The configured `REQUEST_TIMEOUT_SECONDS` is also not wired into the current Twikit path, and proxy configuration reaches into internal client objects rather than using a stable adapter boundary.

**Required redesign**

- [ ] Define internal exception classes such as `AuthenticationFailure`, `RateLimited`, `TransientNetworkFailure`, `CollectorChanged`, `MalformedResponse`, and `PermanentTaskFailure`.
- [ ] Build a version-pinned Twikit adapter that converts library-specific exceptions into those internal classes.
- [ ] Contract-test the exact pinned Twikit version.
- [ ] Apply configured timeout/proxy settings through supported client configuration where available.
- [ ] Make fallback decisions from internal error classes, not library implementation details.
- [ ] Track failure class, HTTP/status metadata where safely available, retry-after information, and collector version.

**Acceptance**

- Auth failure, throttling, timeout, malformed response, and dependency breakage each produce deterministic state transitions and metrics.

## P0-04 — Prevent incorrect token reactivation after fallback

**Current defect**

When the original token is cooled down and a failover token or browser path succeeds, `process_task()` still calls `mark_token_active(token.id)` on the original token. A known-bad token can therefore be reactivated immediately after a successful fallback.

**Required redesign**

- [ ] Return structured execution metadata including which session/adapter actually succeeded.
- [ ] Centralize lease release and session-state updates in one state machine.
- [ ] Never mutate original-session health merely because another collector succeeded.
- [ ] Add state-transition tests for primary success, primary auth failure + failover success, primary throttle + retry, and complete failure.

## P0-05 — Correct canonical identity, deduplication, and engagement semantics

**Current defect**

`analytics_parser.py` uses a text hash to find an existing row before insert. If a matching text hash is found, it **adds** incoming likes/retweets to the stored values. Re-observing the same or copied text therefore creates artificial engagement. It can also merge distinct posts/authors that happen to contain the same normalized text.

The blueprint says repeated observations preserve the highest counters, but this special hash path does not do that.

There is also a race: `content_text_hash` is indexed but not unique, so concurrent workers can both see no matching hash and insert separate rows.

**Required redesign**

- [ ] Treat `(platform, platform_object_id)` as canonical identity.
- [ ] Use content hashes for clustering/similarity, not destructive identity merging.
- [ ] Add a separate engagement-observation table keyed by source object + capture time/run.
- [ ] Store `first_seen_at`, `last_seen_at`, `source_created_at`, and `captured_at` explicitly.
- [ ] Canonical current counters should be `GREATEST(current, observed)` where counters are monotonic; preserve raw observations for analysis.
- [ ] Remove read-before-write dedup races; enforce identity with unique constraints and atomic upserts.
- [ ] Preserve author/source provenance even for identical text.
- [ ] Make multilingual hashing/tokenization Unicode-aware.

**Acceptance**

- Observing one post ten times does not produce 10x engagement or 10x volume.
- Two different posts with identical text remain separately identifiable.

## P0-06 — Make rollups idempotent and transactional

**Current defect**

Every parsed record increments `keyword_hourly_rollups`, including re-observations and content-hash collisions. A task can therefore inflate trend volume without new posts. Rollup updates happen as many sequential SQL statements and are not wrapped atomically with the canonical write.

The rollup bucket is hour-truncated, while `analytics_alerts.py` queries it as if it represented a true trailing 60-minute window. Near hour boundaries that does not represent the requested interval.

**Required redesign**

- [ ] Decide whether rollups count unique posts, observations, or both; encode separate metrics if both matter.
- [ ] Make rollup contribution idempotent using a contribution/event ID.
- [ ] Prefer minute-level buckets or compute a correct time-window model.
- [ ] Bulk-write keyword contributions instead of one DB round trip per word.
- [ ] Keep canonical persistence and derived-event publication transactionally consistent.
- [ ] Add a rebuildable rollup pipeline so analytics can be recomputed from source observations.

## P0-07 — Redesign session refresh and secret storage

**Current defect**

The same `service_tokens.token_value` field is used as a JSON cookie blob by the worker/seeder but is parsed as `{email,password,totp_secret}` by `token_refresh_service.py`. These representations are incompatible.

The refresh implementation also generates a current TOTP value and passes that value into Twikit's `totp_secret` parameter. Twikit's documented contract expects the underlying TOTP secret and generates the one-time code internally. This must be fixed against a pinned library version rather than guessed.

`bulk_account_seeder.py` contains example password/TOTP material directly in source, establishing the wrong production pattern.

**Required redesign**

- [ ] Split account metadata, session state, and credential secret references.
- [ ] Introduce a `SecretStore` interface (production implementation backed by an approved secret manager/KMS; local development implementation via explicit local secret file/env with safe permissions).
- [ ] Store session cookies/tokens encrypted at rest if persistence is necessary.
- [ ] Never put passwords or TOTP seeds in source, logs, task payloads, or general-purpose DB columns.
- [ ] Pass the actual TOTP secret according to the pinned adapter contract; do not pre-generate and mislabel a code.
- [ ] Distinguish transient refresh failures from permanent revocation; do not revoke an identity because of one network/library failure.
- [ ] Add refresh attempt counters, backoff, last-success, and reason codes.

## P0-08 — Repair the executive brief pipeline

**Current defect**

`analytics_briefs.py` sends a chat-completions-shaped request to `https://openai.com`, which is not an API request endpoint. The default API key is a fake non-empty string, so an unconfigured service attempts a doomed network call instead of failing configuration validation.

It also holds a PostgreSQL connection while performing the external model request, has no idempotency per time window, and directly embeds untrusted collected text into model context without a strong data/instruction boundary.

**Required redesign**

- [ ] Create a provider-agnostic `BriefGenerator` interface.
- [ ] Validate provider configuration at startup; no fake default credential.
- [ ] Use the provider's supported SDK/API behind the adapter.
- [ ] Fetch source data, release DB connection, perform model call, then reacquire DB connection to persist.
- [ ] Add bounded retries/timeouts and circuit breaking for provider failures.
- [ ] Give each brief an idempotency key/window (`window_start`, `window_end`, model/provider/version).
- [ ] Store evidence/source object IDs used to produce each brief.
- [ ] Treat collected text strictly as untrusted data and use structured output validation.
- [ ] Enforce input-size/token budgets deterministically.

## P0-09 — Fix API semantic breakage and health behavior

**Current defect**

- `/api/v1/alerts/live` does not read `system_operational_alerts`. It maps `sentiment_label` into `target_keyword`, likes into event volume, and task rows into alerts.
- `/api/v1/trends/spikes` recomputes word counts over raw text rather than using the analytics model named by the repository.
- `/healthz` always returns `{"status":"ok"}` even when database initialization failed.
- The blueprint calls the API secured, but no authentication/authorization layer exists.

**Required redesign**

- [ ] Serve alerts from the actual alerts model/table.
- [ ] Define trend response semantics and source them from the correct rollup/trend materialization.
- [ ] Split liveness (`/healthz`) from readiness (`/readyz` checking DB and required queue dependency).
- [ ] Add API authentication appropriate to the larger-system integration boundary.
- [ ] Add pagination, bounded limits, stable ordering, and time-range filters.
- [ ] Add request correlation IDs and API metrics.
- [ ] Add contract tests matching SQL output to response models.

## P0-10 — Make the repository reproducibly runnable

**Current defect**

- No `pyproject.toml`, requirements lock, or equivalent dependency declaration exists.
- There is no application `Dockerfile`.
- All systemd service files and nginx config currently present are empty.
- Compose only launches PostgreSQL/Redis/schema initialization, not the application services.
- The DB initializer does not explicitly provide the PostgreSQL password to `psql`, so the stock Compose path needs an integration test/fix for non-interactive authentication.
- `python-3.11.9.pkg` is a binary artifact in the repository with no documented role.
- No `.env.example`, `.gitignore`, or startup/readiness validation is present.

**Required redesign**

- [ ] Add a real Python project manifest and locked dependency set.
- [ ] Pin Python/runtime dependency versions known to pass tests.
- [ ] Add application container image(s) or a documented alternative deployment artifact.
- [ ] Complete service definitions or remove misleading empty placeholders in favor of the chosen deployment method.
- [ ] Fix Compose initialization/auth, add health checks, private service networking, Redis persistence if Streams are authoritative for delivery, and avoid exposing data services unnecessarily.
- [ ] Classify/remove the unexplained binary package artifact after confirming it has no runtime purpose.
- [ ] Add `.env.example` containing names/default-safe values only.
- [ ] Add startup configuration validation.

---

# P1 — Production architecture and maintainability

## P1-01 — Create a proper package layout and service boundaries

Move away from unrelated root-level scripts toward an importable package, for example:

```text
src/xingestion/
  config.py
  logging.py
  db/
  queue/
  collectors/
  sessions/
  ingestion/
  analytics/
  briefs/
  api/
  models/
  observability/
commands/
  worker.py
  dispatcher.py
  alert_worker.py
  brief_worker.py
  cleanup.py
  replay.py
migrations/
tests/
```

- [ ] Centralize typed configuration rather than independently parsing env vars in each script.
- [ ] Centralize DB/Redis client lifecycle.
- [ ] Separate domain state machines from CLI/service entrypoints.
- [ ] Keep collection-specific dependencies isolated in `collectors/`.

## P1-02 — Introduce an explicit `SourceAdapter` contract

- [ ] Define normalized collection request/result models.
- [ ] Preserve raw provider payloads or a controlled lossless subset for research provenance.
- [ ] Support pagination/cursors/checkpoints instead of a single fixed `count=20` call.
- [ ] Track adapter version and collection method per run.
- [ ] Support fixture/mock adapter without branching core production logic on `MOCK_MODE` everywhere.
- [ ] Keep any browser recovery mechanism isolated, explicitly enabled, observable, and contract-tested; do not let it silently produce lower-fidelity records presented as equivalent data.

## P1-03 — Add scheduler/query management and resumability

The current repository has one-shot tasks but no persistent query definition or recurring scheduler.

- [ ] Add research query/source configuration records.
- [ ] Add schedule/cadence and enable/disable state.
- [ ] Store per-query checkpoints/cursors/time windows.
- [ ] Detect and measure collection gaps.
- [ ] Support controlled backfill without colliding with live ingestion.
- [ ] Add per-query priority and budgets.

## P1-04 — Version the database schema

- [ ] Convert `schema_analytics.sql` into numbered migrations.
- [ ] Add schema version tracking.
- [ ] Add check constraints/enums for task/session states where appropriate.
- [ ] Add foreign keys and unique constraints for task lineage, dead letters, observations, alerts, and brief windows.
- [ ] Add indexes based on real API/worker query patterns.
- [ ] Test migration from the current schema with representative data rather than only fresh database creation.

## P1-05 — Preserve research provenance and auditability

Add first-class concepts for:

- [ ] `ingestion_run_id`
- [ ] source/platform
- [ ] source query/task ID
- [ ] source object ID
- [ ] source-created timestamp
- [ ] captured/observed timestamp
- [ ] collector + collector version
- [ ] normalized record version
- [ ] raw-payload reference/hash
- [ ] retry/fallback path used

This enables later reproducibility and integration into a larger analytical system.

## P1-06 — Replace naive keyword alerting with defined anomaly semantics

Current alerting is an absolute threshold (`tweet_count > N`), not a velocity anomaly model, and it writes the same alert repeatedly every scan.

- [ ] Define a baseline method (for example rolling median/MAD or another robust baseline appropriate to observed data).
- [ ] Define minimum absolute volume so tiny baselines do not create noise.
- [ ] Add alert identity/dedup key, first-triggered, last-seen, severity, state, and resolution.
- [ ] Add cooldown/debounce so one spike does not create a new database alert each minute.
- [ ] Store the baseline and calculation inputs necessary to explain why an alert fired.

## P1-07 — Rework brief generation around evidence, not raw top-like rows

- [ ] Feed briefs from defined trend/alert clusters, with source IDs and quantitative evidence.
- [ ] Preserve citations/references back to source rows.
- [ ] Store model/provider/prompt version and generation window.
- [ ] Make repeated generation for the same window idempotent unless an explicit regeneration version is requested.
- [ ] Add deterministic schema validation for the model output.

## P1-08 — API hardening

- [ ] Version and document API contracts.
- [ ] Add integration auth/authorization.
- [ ] Add rate limiting at gateway/application boundary as appropriate.
- [ ] Add pagination and query bounds to avoid accidental full scans.
- [ ] Add database statement timeouts for read endpoints.
- [ ] Add API error taxonomy instead of generic 500s for all data failures.
- [ ] Add readiness, dependency state, build SHA/version, and optional metrics endpoint.
- [ ] Fill gateway config only after the intended deployment topology is decided.

## P1-09 — Observability

- [ ] Structured logs with correlation fields instead of string-only messages.
- [ ] Prometheus/OpenTelemetry-compatible metrics for queue depth/age, task lifecycle, collection results by error class, session health, throttling, DB/Redis latency, ingestion throughput, duplicate/re-observation ratio, analytics lag, brief generation latency/failures, and API latency/status.
- [ ] Distributed trace/correlation propagation from task -> collection -> persistence -> analytics where useful.
- [ ] Alerts for stuck queue, zero-ingestion periods, retry storms, session-pool exhaustion, high dead-letter rate, database saturation, and stale briefs.

## P1-10 — Safe retention and replay

Current `db_cleanup.py` deletes all `DONE` tasks immediately, which removes useful operational history. `task_replay.py` deletes dead-letter rows after recreating tasks, destroying failure history.

- [ ] Define retention independently for raw observations, canonical records, engagement observations, task history, dead letters, alerts, rollups, and briefs.
- [ ] Keep replay history immutable; mark a dead letter as replayed and link the new task rather than deleting the archive row.
- [ ] Make replay selective by task/error/date/query.
- [ ] Add dry-run and max-count controls.
- [ ] Add poison-message protection/replay generation limits.
- [ ] Batch large retention deletes; rely on normal PostgreSQL autovacuum unless measured evidence requires explicit maintenance jobs.

---

# P2 — Verification, scale, and larger-system integration

## P2-01 — Automated test matrix

### Unit

- [ ] Pydantic/normalization behavior, including Unicode/multilingual text.
- [ ] error classification.
- [ ] retry/backoff calculation.
- [ ] task/session state transitions.
- [ ] deduplication/idempotency.
- [ ] analytics calculations.

### Integration

- [ ] PostgreSQL migrations and task leasing.
- [ ] Redis Stream publish/consume/ack/reclaim.
- [ ] worker crash after delivery but before DB commit.
- [ ] Redis unavailable during outbox dispatch.
- [ ] DB unavailable during completion.
- [ ] duplicate delivery.
- [ ] dead-letter and selective replay.
- [ ] API response/schema tests.

### Collector contract

- [ ] Recorded/fixture responses for the pinned primary adapter.
- [ ] auth failure classification.
- [ ] throttling classification.
- [ ] malformed/changed response classification.
- [ ] pagination/checkpoint continuation.
- [ ] live tests only behind an explicit gate and separate credentials/configuration.

## P2-02 — CI quality gates

- [ ] formatter/linter (`ruff` or equivalent).
- [ ] type checking for stable domain/service interfaces.
- [ ] unit + integration tests.
- [ ] migration validation.
- [ ] dependency vulnerability audit.
- [ ] secret scanning.
- [ ] container build.
- [ ] test coverage visibility without treating percentage alone as correctness.

## P2-03 — Load and soak testing

Define measurable targets rather than claiming scale from architecture alone.

Initial acceptance target based on the existing blueprint:

- [ ] sustain >=100,000 normalized records/day equivalent under representative batching.
- [ ] survive burst load substantially above average without task loss.
- [ ] zero acknowledged task loss during forced worker termination tests.
- [ ] bounded queue age under normal capacity.
- [ ] bounded DB connection usage under brief/API/worker concurrency.
- [ ] measured p50/p95/p99 collection, persistence, and API latency.
- [ ] 24-hour soak without unbounded memory/connection growth.

Exact thresholds should be finalized after the deployment hardware and upstream integration contract are known.

## P2-04 — Larger-system integration contract

- [ ] Define inbound task/query contract independently of Redis implementation.
- [ ] Define outbound normalized event schema/version.
- [ ] Add event/schema versioning and backward compatibility rules.
- [ ] Add health/metrics endpoints suitable for orchestration.
- [ ] Externalize secrets, identity management, logging sink, and telemetry exporters.
- [ ] Support correlation/run IDs supplied by the parent system.
- [ ] Document resource requirements and horizontal-scaling behavior.

---

# 3. Known file-specific defects to retain during implementation

This is a checklist so individual bugs are not lost when larger components are rewritten.

## `worker.py`

- [ ] Redis `LPOP` can lose work on crash.
- [ ] DB task is never marked `RUNNING` during checkout.
- [ ] `worker_id` is unused in task checkout.
- [ ] `next_run_at` and `backoff_delay_seconds()` are effectively unused.
- [ ] DB retry state and Redis requeue are not atomic.
- [ ] token checkout is not an exclusive lease.
- [ ] Twikit errors are not normalized to the exception path used for failover.
- [ ] configured request timeout is not applied to the primary collector.
- [ ] original token can be marked active after fallback succeeds.
- [ ] browser recovery URL construction is malformed and query is not encoded.
- [ ] browser recovery emits synthetic text-hash IDs and loses author/engagement provenance; it must not be silently treated as equivalent fidelity.
- [ ] DB startup failure logs a mock fallback but actually waits indefinitely; mock mode still depends on DB/task/session infrastructure.
- [ ] Redis client is not explicitly closed.
- [ ] task type is not validated/dispatched; all tasks are treated as keyword search.
- [ ] primary collection fetches only a small single page with no persistent continuation/checkpoint.

## `analytics_parser.py`

- [ ] content-hash match adds engagement counters and corrupts totals.
- [ ] text hash can merge distinct source objects.
- [ ] hash lookup + write has a concurrency race.
- [ ] `ingested_at` can be moved forward on re-observation.
- [ ] rollups count re-observations as new posts.
- [ ] rollup DB writes are per-keyword and inefficient.
- [ ] feed write and rollup writes are not one idempotent event transaction.
- [ ] ASCII-only keyword extraction excludes most multilingual research content.
- [ ] naive local `datetime.now()` is used for a timezone-aware database field when `ingested_at` is missing.

## `analytics_alerts.py`

- [ ] absolute threshold is mislabeled as velocity anomaly detection.
- [ ] same persistent spike is inserted every polling cycle.
- [ ] hour-truncated rollups do not implement a correct rolling 60-minute window.
- [ ] no resolution/debounce/severity model.

## `analytics_briefs.py`

- [ ] invalid/non-API model endpoint.
- [ ] fake non-empty API key default masks missing configuration.
- [ ] DB connection is held across external network/model request.
- [ ] no generation-window idempotency.
- [ ] no structured output validation.
- [ ] untrusted collected text is mixed directly into model context without a strong instruction/data boundary.
- [ ] no input-size budget or evidence lineage.

## `api_server.py`

- [ ] alerts endpoint reads task/feed rows rather than actual alert rows.
- [ ] `target_keyword` is populated from sentiment.
- [ ] trend endpoint bypasses precomputed analytics and performs expensive raw-text tokenization.
- [ ] liveness reports OK when DB can be unavailable.
- [ ] API is not actually secured despite documentation.
- [ ] no pagination/time-range contract.

## `token_refresh_service.py`

- [ ] cookie JSON is misinterpreted as credential JSON.
- [ ] current TOTP code is passed where Twikit documents a TOTP secret.
- [ ] any refresh exception can revoke an account, including potentially transient failures.
- [ ] no graceful shutdown or bounded refresh attempt policy.

## `bulk_account_seeder.py`

- [ ] source code contains an example username/password/TOTP secret pattern that must not be used for real identities.
- [ ] credentials are coupled directly to login logic rather than secret references.
- [ ] resulting DB row contains cookies only, incompatible with the refresh service's expectation.

## `task_replay.py`

- [ ] recreated task is not delivered to Redis, so current worker does not receive it.
- [ ] dead-letter history is deleted on replay.
- [ ] all dead letters replay at once.
- [ ] attempts reset without replay generation/poison-task controls.

## `seed_test.py`

- [ ] creates DB tasks but not Redis deliveries.
- [ ] `ON CONFLICT DO NOTHING` has no meaningful task uniqueness constraint to conflict on, so repeated seeding duplicates logical tasks.
- [ ] mock token strings are not cookie JSON and therefore only work when collection is bypassed.

## `db_cleanup.py`

- [ ] completed task audit history is deleted immediately rather than by a defined retention policy.
- [ ] dead letters/alerts/rollups/briefs have no coherent retention strategy.
- [ ] explicit `VACUUM ANALYZE` every cleanup cycle should be justified by measurements rather than used as default maintenance.
- [ ] large deletions are not batched.

## `docker-compose.yml` / `systemd/`

- [ ] application processes are not in Compose.
- [ ] DB/Redis ports are published directly by default.
- [ ] Redis has no persistent volume/configuration suitable for durable Streams.
- [ ] DB initializer authentication/config needs a working non-interactive path.
- [ ] all checked-in systemd/nginx files are empty.

## repository-level

- [ ] no dependency manifest/lock.
- [ ] no tests.
- [ ] no CI.
- [ ] no application Dockerfile.
- [ ] no versioned migrations.
- [ ] no `.env.example` / startup configuration schema.
- [ ] no normal README/runbook separate from an architecture document that currently overstates implementation.
- [ ] unexplained `python-3.11.9.pkg` binary artifact should be classified or removed.

---

# 4. Implementation order

The order matters because several apparent local bugs are symptoms of the same state-model problem.

## Phase A — Reproducible baseline and safety net

1. Add project/dependency manifest and pinned versions.
2. Add test harness and local integration stack.
3. Add typed configuration/startup validation.
4. Convert current schema into migration `0001` without changing semantics.
5. Add baseline tests that reproduce the known queue, dedup, API, and session-state defects before changing architecture.

**Gate:** current behavior can be reproduced in a clean environment and defects have executable regression tests where feasible.

## Phase B — Durable control plane

1. Migrate task schema/state model.
2. Add outbox + Redis Streams dispatcher.
3. Add consumer group lease/ack/reclaim behavior.
4. Rewrite retry/dead-letter/replay around durable state and idempotency.
5. Add session lease semantics.

**Gate:** forced worker termination and Redis/DB interruption tests show no acknowledged task loss and no duplicate logical completion.

## Phase C — Collection adapter and session subsystem

1. Introduce `SourceAdapter` and normalized error taxonomy.
2. Move Twikit usage behind pinned adapter.
3. Correct session representation/secret handling.
4. Implement controlled refresh state machine.
5. Add checkpoint/pagination support.
6. Isolate recovery collector behind the same result/provenance contract.

**Gate:** fixture tests exercise successful pagination and each major failure class; live collector verification is separately gated.

## Phase D — Data correctness

1. Migrate to canonical object + observation/provenance schema.
2. Correct engagement semantics.
3. Make analytical contributions idempotent and rebuildable.
4. Add multilingual-safe normalization.
5. Backfill/migrate existing records with explicit assumptions.

**Gate:** duplicate-delivery/re-observation tests produce stable canonical counts and rebuilds reproduce rollups.

## Phase E — Analytics, briefs, and API

1. Correct anomaly model and alert state/dedup.
2. Build evidence-backed brief generation adapter with structured outputs.
3. Correct API endpoints/response semantics.
4. Add authentication, pagination, readiness, and integration metadata.

**Gate:** API contract tests and analytics fixture tests pass; brief generation cannot mutate source data and is idempotent per window.

## Phase F — Deployment and operations

1. Build application image and local Compose stack.
2. Complete/remove systemd/nginx placeholders according to chosen topology.
3. Add CI gates, metrics/tracing, dashboards/alerts, backup/restore notes, and runbooks.
4. Run load, fault, and soak tests.

**Gate:** a clean deployment can be built from the repository with no undocumented manual file creation and passes the production acceptance suite.

---

# 5. Production acceptance definition

Do **not** label the project production-ready until all of the following are demonstrated:

- [ ] clean reproducible build from declared dependencies.
- [ ] versioned migration from current schema.
- [ ] no plaintext real credentials/session material in repository or logs.
- [ ] no acknowledged task loss under forced worker termination.
- [ ] deterministic retry/dead-letter/replay behavior.
- [ ] exclusive/bounded account-session leasing.
- [ ] duplicate observations cannot inflate canonical engagement or unique-post volume.
- [ ] collection dependency failures map to explicit internal error states.
- [ ] API alert/trend/health endpoints expose what their names claim.
- [ ] readiness fails when required dependencies are unavailable.
- [ ] analytics and brief generation are idempotent/reproducible by window.
- [ ] observability exposes queue lag, failure reasons, session health, throughput, and dependency saturation.
- [ ] integration and fault-injection tests pass in CI.
- [ ] sustained-load/soak results are recorded against known hardware and configuration.
- [ ] deployment/runbook accurately describes the files that actually exist.

## Immediate recommended implementation slice

Start with **Phase A + P0-01/P0-02**, not collector tweaks. Until task delivery and session leases are durable, improvements to extraction can make the system faster while still losing work, duplicating use of sessions, and reporting incorrect operational state.
