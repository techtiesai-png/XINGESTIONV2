# XINGESTIONV2 Implementation Ledger

> Read [`AGENTS.md`](./AGENTS.md) for project rules and [`plan.md`](./plan.md) for the authoritative backlog.

This file records **completed work only**. Planned or suspected fixes belong in `plan.md` until they are actually implemented.

## Entry format

Each implementation entry should contain:

- date
- related `plan.md` item(s)
- files changed
- behavior changed
- migrations/configuration implications
- verification actually performed
- known limitations / follow-up
- relevant commit or PR when available

Do not mark an item verified if only static inspection was performed.

---

## 2026-08-08 — Repository audit and engineering-control documentation

**Related plan items:** audit baseline / documentation control only. No runtime remediation item is being marked complete yet.

### Files added

- `AGENTS.md`
- `plan.md`
- `implemented.md`

### What changed

- Established the standing engineering rules for this fork, including the production-first / no-capability-regression requirement, government-related research context, later larger-system integration goal, secret-handling requirements, verification discipline, and the relationship between `AGENTS.md`, `plan.md`, and `implemented.md`.
- Added a detailed architecture/correctness audit and phased production-hardening roadmap.
- Added this implementation ledger so future changes can be tied back to plan items and their actual verification state.

### Audit work performed

Static repository review covered the current runtime and deployment surface, including:

- `ARCHITECTURAL_BLUEPRINT.md`
- `worker.py`
- `analytics_parser.py`
- `analytics_alerts.py`
- `analytics_briefs.py`
- `api_server.py`
- `token_refresh_service.py`
- `bulk_account_seeder.py`
- `task_replay.py`
- `seed_test.py`
- `db_cleanup.py`
- `schema_analytics.sql`
- `docker-compose.yml`
- repository tree / deployment placeholders

The fork and upstream were also checked and were on the same original runtime commit (`8e7771a483d5ea57f440f7f410e7b0bea0176f4c`) before these documentation commits.

### Verification performed

- Confirmed the complete repository tree through the GitHub connector.
- Confirmed all checked-in `systemd/*` and nginx placeholder files are zero-byte files.
- Confirmed the repository lacks a Python dependency manifest/lock, tests, CI configuration, application Dockerfile, and versioned migration directory in the audited baseline.
- Reviewed Twikit's current documented login contract to validate that its `totp_secret` parameter expects the underlying TOTP secret rather than a pre-generated current code.
- Attempted a separate local `git clone` for executable/static-tool verification, but the working container has no outbound DNS/network access to GitHub. No runtime dependency installation or execution was therefore performed in this audit pass.

### Runtime behavior changed

None. The only repository changes in this entry are documentation/control files.

### Known limitations / next action

The defects in `plan.md` are based on code-level and architecture-level review; they have not yet been validated by running the full stack. The first implementation slice should establish a reproducible dependency/test environment and then fix the durable task/session state model before tuning collection behavior.

---

## 2026-08-08 — Segment 1: durable control-plane verification and outbox hardening

**Branch:** `hardening/control-plane-v1`

**Related plan items:** P0-01 durable task delivery, Phase A reproducibility/verification, P2-01 integration testing, and the initial portion of P2-02 CI quality gates.

### Files added or materially changed in this segment

- `xingestion/outbox.py`
- `migrations/0003_outbox_claims.sql`
- `dispatcher.py`
- `docker-compose.yml`
- `scripts/apply_migrations.py`
- `tests/test_control_plane_smoke.py`
- `tests/test_outbox_integration.py`
- `.github/workflows/control-plane-ci.yml`
- `pyproject.toml`

### Behavior changed

- Replaced the first outbox dispatcher implementation, which could publish a Redis message before its enclosing PostgreSQL transaction committed the task's `ENQUEUED` state, with a claim-based dispatcher.
- Dispatcher instances now claim outbox rows using PostgreSQL row locks with `SKIP LOCKED`, persist a short-lived claim token/expiry, and commit the task to `ENQUEUED` before publishing anything to Redis. This removes the database/Redis visibility race and permits multiple dispatcher processes to operate concurrently.
- A dispatcher crash before Redis publication is recoverable after claim expiry. A crash after Redis publication but before recording `published_at` can produce a duplicate delivery; this is deliberately treated as an at-least-once delivery case rather than risking task loss. Task generation/state guards reject a duplicate logical execution after the task is complete.
- Added persistent Redis AOF configuration to the local Compose stack and explicit Redis/PostgreSQL health checks.
- Database initialization now applies the baseline schema and every numbered migration with failure-on-error behavior.
- Added a Python migration runner for CI/non-Compose execution.
- Corrected dependency pins to versions that were subsequently installed successfully by CI, including FastAPI `0.139.2` and Ruff `0.15.22`.

### Verification actually performed

GitHub Actions run `31241791142` completed successfully against real PostgreSQL 15 and Redis 7 service containers.

The successful run verified:

- editable installation of the declared project and development dependencies;
- Python compilation of the hardened package, worker/dispatcher entrypoints, migration script, and tests;
- Ruff correctness-oriented `E`, `F`, and `B` checks over the hardened control-plane scope;
- execution of `schema_analytics.sql` followed by `0001_control_plane.sql`, `0002_observations.sql`, and `0003_outbox_claims.sql` on PostgreSQL 15 without migration errors;
- control-plane configuration/value-object smoke tests;
- end-to-end task creation -> durable outbox claim -> committed `ENQUEUED` state -> Redis Stream publication -> consumer delivery -> PostgreSQL task lease -> durable completion -> Redis acknowledgement;
- explicit duplicate delivery of the same task generation after completion is not leased/re-executed and can be safely acknowledged as terminal/stale.

An earlier CI run (`31241703189`) also established that the full dependency set installs and the Python sources compile; that run stopped at broader style/modernization lint rules before database tests. Those style-only rules were intentionally separated from this correctness segment rather than allowed to block state-machine verification.

### What is *not* claimed complete

- P0-01 as a whole remains open. Worker lease heartbeats, stale in-flight task recovery timing, pending-entry reclaim behavior under forced worker termination, and retry/reclaim race tests still need a dedicated segment.
- PostgreSQL/Redis exactly-once delivery is **not** claimed. The design intentionally uses at-least-once transport plus idempotent/generation-guarded processing.
- Multi-dispatcher behavior is designed around `SKIP LOCKED` and expiring claims but has not yet been load/fault tested with multiple live dispatcher processes.
- Full style/import modernization lint is not part of the current correctness gate and remains cleanup work.
- Live X collection behavior was not exercised in this segment; collector contract/live tests belong to a later segment.

### Next recommended segment

Worker lease lifecycle and crash recovery: add task lease renewal/heartbeats, verify Redis `XAUTOCLAIM` + PostgreSQL lease-expiry interaction, inject worker termination between delivery and completion, and prove that work is reclaimed without simultaneous execution or acknowledged loss.

---

## 2026-08-08 — Segment 2: worker lease heartbeat and crash recovery

**Branch:** `hardening/control-plane-v1`

**Related plan items:** P0-01 task lease/crash recovery, the task-side portion of the reliability acceptance criteria, and P2-01 failure-injection/integration testing.

### Files added or materially changed in this segment

- `xingestion/lease_guard.py`
- `xingestion/config.py`
- `worker.py`
- `tests/test_worker_recovery_integration.py`
- `.github/workflows/control-plane-ci.yml`

### Behavior changed

- Added a renewable PostgreSQL task lease heartbeat. A worker processing a `RUNNING` task now periodically extends `lease_expires_at` only if the task generation, current lease owner, status, and still-unexpired lease all match. A worker cannot resurrect a lease after it has expired or after ownership has changed.
- Added Redis pending-entry heartbeats using `XCLAIM` to the same consumer with zero idle time. This resets the PEL idle clock so healthy long-running work stays below the `XAUTOCLAIM` recovery threshold.
- PostgreSQL remains the stronger execution-authority fence. Redis ownership transfer alone does not allow a second worker to execute while the DB lease is valid.
- Added configurable `TASK_HEARTBEAT_SECONDS` with startup validation that it is shorter than `TASK_LEASE_SECONDS`. The default reclaim threshold is later than the durable lease horizon, giving an expired DB lease a deterministic opportunity to become reclaimable before another worker executes it.
- Added `TaskLeaseGuard.run_guarded()`: collection and persistence run under a background heartbeat. If the durable DB fence is lost, the in-flight operation is cancelled rather than continuing without ownership.
- Graceful cancellation releases the DB task back to `ENQUEUED` without manufacturing a failed attempt or incrementing retry counters. A hard crash remains recoverable through normal DB lease expiry plus Redis PEL reclaim.
- Task final completion intentionally occurs after the heartbeat-protected collection/persistence section, preventing a completion/heartbeat race where a successful `DONE` transition could be misread as lease loss.

### External behavior verified

Redis's official `XAUTOCLAIM`/Streams documentation was checked before implementation. The design relies on documented behavior that:

- `XAUTOCLAIM` transfers ownership only for pending entries older than the configured minimum idle time;
- claiming resets the pending entry's idle time;
- `XCLAIM` can refresh ownership/idle state for a known pending message;
- Redis consumer-group recovery is therefore an at-least-once transport mechanism, not an exactly-once execution guarantee.

### Verification actually performed

GitHub Actions run `31243415471` completed successfully against real PostgreSQL 15 and Redis 7 service containers.

The run passed dependency installation, Python compilation, correctness-oriented Ruff checks (including `worker.py` and the new lease guard), all database migrations, the previous outbox/control-plane tests, and the new worker recovery tests.

The new integration tests verified:

1. **Healthy heartbeat fencing:** a Redis pending message was deliberately aged to make it reclaimable, then a heartbeat renewed the PostgreSQL lease and reset Redis idle state. A second consumer could not reclaim the message and could not acquire the task lease.
2. **Hard-crash state recovery:** a worker was simulated as dead by leaving its Redis message unacknowledged and expiring its PostgreSQL lease. The pending Redis entry was then reclaimed by another consumer, which successfully acquired the expired DB task lease. The stale original lease was unable to commit; the replacement lease completed successfully.
3. **Lease-loss cancellation:** durable ownership was deliberately changed while a guarded long-running operation was active. The next heartbeat detected that the original worker no longer owned the DB fence, raised `TaskLeaseLost`, and cancelled the in-flight operation.

These tests avoid long sleeps by deterministically aging Redis PEL entries and expiring PostgreSQL leases, so they test the same state transitions without timing-flaky CI delays.

### What is *not* claimed complete

- This segment does not claim literal process-level `SIGKILL` fault injection; the hard-crash condition is reproduced deterministically by the exact durable state left behind by a dead process: an unacknowledged PEL entry plus an expired PostgreSQL lease.
- Exactly-once transport is not claimed. Correctness relies on at-least-once Redis delivery plus PostgreSQL lease fencing, generation checks, and idempotent persistence.
- Session/token leases are not heartbeated in this segment. Collector calls currently hold those leases for a much shorter bounded request window, but session lifecycle hardening remains its own plan area.
- Cross-region Redis/PostgreSQL latency, clock skew assumptions, worker pauses longer than the lease horizon, and large-scale reclaim storms have not yet been load/chaos tested.
- Retry scheduling and reclaim racing under repeated collector failures still deserve one focused integration segment before P0-01 is considered fully closed.

### Next recommended segment

**Segment 3 — retry/dead-letter state-machine verification.** Exercise transient failure -> scheduled retry -> new delivery generation -> successful completion, retry exhaustion -> dead letter, stale old-generation messages after retry, and selective replay without duplicate logical execution. This closes the remaining high-risk task lifecycle before moving deeper into session/collector hardening.
