# XINGESTIONV2 Implementation Ledger

> Read [`AGENTS.md`](./AGENTS.md) for project rules and [`plan.md`](./plan.md) for the authoritative backlog.

This file records implementation evidence and cross-segment dependencies discovered while implementing the plan. A change is not called verified unless the stated verification actually ran.

## External / user-dependent items

**Current status: none of these block the next engineering segment.** Ask the user only when one becomes necessary to proceed.

- **LIVE-X-01 — live collector validation:** live Twikit/browser contract tests will eventually require authorized research test identities/session material plus whatever approved outbound network/proxy setup will be used. Fixture/contract work can continue without this. **User/environment input required later.**
- **SECRETS-01 — production secret backend:** before production credential/session storage is finalized, the deployment needs an approved secret backend (for example a cloud secret manager, Vault/KMS-style system, or parent-platform secret service). A provider-neutral `SecretStore` interface can be implemented before this choice. **User/platform decision required later.**
- **PARENT-01 — parent ingestion integration contract:** final merge into the larger ingestion system needs its ingress/egress schema, event/API transport, authentication method, correlation IDs, and compatibility/versioning expectations. Internal modularization can continue now. **User/parent-system details required later.**
- **SCALE-01 — deployment/SLO envelope:** meaningful production capacity claims and final tuning require target hardware/topology, region/network assumptions, PostgreSQL/Redis deployment model, expected sustained/burst throughput, latency SLOs, and availability targets. **User/infrastructure details required before final load certification.**
- **RETENTION-01 — retention policy:** final cleanup/archival behavior needs required retention periods for raw observations, canonical records, tasks, dead letters, alerts, briefs, and audit history. **Organizational/user decision required before final retention implementation.**
- **AUTH-01 — parent/API authentication:** final API/gateway hardening needs the larger system's preferred trust model (mTLS, workload identity/JWT, gateway auth, etc.). The API can be structurally hardened before this decision. **User/platform decision required later.**

These are dependencies, not excuses to stop unrelated implementation. When a segment reaches one of them and a safe provider-neutral design is no longer sufficient, ask the user then.

---

## 2026-08-08 — Repository audit and engineering-control documentation

**Scope:** audit baseline and documentation controls.

**Files:** `AGENTS.md`, `plan.md`, `implemented.md`.

Established the production-first/no-capability-regression rules, government-related research context, future larger-system integration target, verification discipline, and the `AGENTS.md` -> `plan.md` -> `implemented.md` workflow. Static review covered the worker, analytics, API, session refresh/seeding, replay/cleanup, schema, Compose, and deployment placeholders. The original runtime baseline was commit `8e7771a483d5ea57f440f7f410e7b0bea0176f4c`.

No runtime remediation was claimed in this entry.

---

## 2026-08-08 — Segment 1: durable control plane and outbox

**Related plan:** P0-01, Phase A reproducibility, P2-01/P2-02 foundations.

**Main files:** `xingestion/control_plane.py`, `xingestion/outbox.py`, `dispatcher.py`, `migrations/0001_control_plane.sql`, `migrations/0003_outbox_claims.sql`, `pyproject.toml`, `docker-compose.yml`, migration runner, control-plane/outbox tests, CI.

### Implemented

- PostgreSQL became the durable task ledger; Redis is delivery/acceleration.
- Added task idempotency keys, delivery generations, explicit states, durable leases, retry timing, and task result metadata.
- Replaced lossy Redis `LPOP` semantics with Redis Streams consumer groups and ACK-after-durable-transition behavior.
- Added a transactional task outbox and a claim-based dispatcher using PostgreSQL `SKIP LOCKED`.
- Dispatcher commits the task to `ENQUEUED` before Redis publication, avoiding the DB/Redis visibility race.
- Dispatcher crash after Redis publication can produce duplicate delivery; this is intentionally handled as at-least-once transport with generation/idempotency guards rather than risking task loss.
- Added Redis AOF persistence for the local stack, PostgreSQL/Redis health checks, versioned migration execution, and pinned dependencies.

### Verified

GitHub Actions run **`31241791142`** passed against PostgreSQL 15 + Redis 7. It verified dependency installation, compilation, correctness lint, migrations, task -> outbox -> Redis -> DB lease -> DONE -> ACK, and rejection/ACK of duplicate delivery after durable completion.

### Remaining after Segment 1

Worker-heartbeat/reclaim correctness, retry-generation lifecycle, and large-scale/multi-dispatcher fault testing were intentionally left to later segments rather than called complete.

---

## 2026-08-08 — Segment 2: worker lease heartbeat and crash recovery

**Related plan:** P0-01 crash recovery, reliability acceptance, P2-01 failure injection.

**Main files:** `xingestion/lease_guard.py`, `xingestion/config.py`, `worker.py`, worker-recovery integration tests, CI.

### Implemented

- Added renewable PostgreSQL execution leases for in-flight tasks.
- Heartbeat renewal only succeeds for the same task ID, delivery generation, lease owner, `RUNNING` status, and still-valid lease; an expired/stolen lease cannot be resurrected.
- Added Redis PEL idle refresh with `XCLAIM` so healthy long-running work stays below reclaim thresholds.
- PostgreSQL remains the stronger execution fence; Redis ownership alone never authorizes execution.
- Added validated `TASK_HEARTBEAT_SECONDS` / lease / reclaim timing relationships.
- If the durable DB lease is lost, guarded collection/persistence is cancelled rather than allowed to continue unfenced.
- Graceful cancellation returns work to `ENQUEUED` without fabricating a retry; hard crash is recovered through lease expiry + Redis pending-entry reclaim.

### Verified

GitHub Actions run **`31243415471`** passed with PostgreSQL 15 + Redis 7. It verified:

1. healthy heartbeat renews the DB fence and makes an artificially aged Redis entry non-reclaimable;
2. a simulated hard-crash state (unacked PEL entry + expired DB lease) is reclaimed by another worker;
3. the stale original owner cannot commit after replacement ownership wins;
4. deliberately losing the DB fence cancels an active guarded operation.

### Remaining after Segment 2

Literal OS-level `SIGKILL`, cross-region/clock-skew behavior, reclaim storms, and high-scale chaos remain later reliability/load work. Token/session lease lifecycle is separate from task lease lifecycle and remains to be hardened/verified.

---

## 2026-08-08 — Segment 3: retry, dead-letter, and replay lifecycle

**Related plan:** remaining P0-01 retry lifecycle, safe dead-letter/replay behavior, P1-10 replay auditability, P2-01 integration tests.

**Main files:**

- `xingestion/outbox.py`
- `xingestion/replay.py`
- `task_replay.py`
- `migrations/0004_retry_replay_audit.sql`
- `tests/test_retry_replay_integration.py`
- `.github/workflows/control-plane-ci.yml`

### Implemented

- Corrected `enqueued_at` so each delivery generation receives its own enqueue timestamp rather than retaining the original generation's queue time.
- Verified durable retry state transitions: `RUNNING -> RETRY_SCHEDULED`, attempt increment, delivery-generation increment, new outbox event, due-time gating, then `ENQUEUED -> RUNNING` for the new generation.
- Old-generation Redis messages are explicitly stale after generation rollover and cannot lease/re-execute the task.
- Retry exhaustion transitions exactly once to `DEAD_LETTER` and preserves failure class, payload, error, attempts, and failed delivery generation.
- Added replay lineage columns (`origin_task_id`, `replay_of_dead_letter_id`) and an immutable `worker_dead_letter_replays` audit table.
- Replay is now selective by dead-letter ID(s), task type, and/or failure class.
- Dead-letter selection, replacement task creation, replacement outbox creation, replay audit insertion, and archive replay marking happen in one PostgreSQL transaction under row locks/`SKIP LOCKED`.
- Replaying an already replayed archive through the normal operator path creates no second logical replay.
- The CLI now exposes selective replay controls through `REPLAY_DEAD_LETTER_IDS`, `REPLAY_TASK_TYPE`, `REPLAY_FAILURE_CLASS`, `REPLAY_LIMIT`, `REPLAY_MAX_ATTEMPTS`, and `REPLAY_PRIORITY`.
- Replay audit deliberately stores the historical replay task ID without a restrictive FK because the existing cleanup daemon still deletes completed tasks; adding a restrictive FK here would have broken unrelated cleanup transactions before their own retention redesign segment.

### Verification actually performed

The first Segment 3 CI attempt (`31245423900`) installed and compiled successfully but stopped on one unused import; it did not reach migrations/tests. The import was removed.

A subsequent full lifecycle run passed, and the final strengthened run **`31245531114`** passed completely against PostgreSQL 15 + Redis 7 after adding deterministic backoff-gating verification.

The final gate verified:

1. **Scheduled retry is actually delayed:** a task was scheduled 60 seconds ahead and the dispatcher returned zero eligible events before the due time. CI then advanced only durable scheduler timestamps (no real sleep) and the retry became dispatchable.
2. **Generation rollover:** first failure incremented attempts and moved generation `0 -> 1`; generation 0 delivery became stale and could not acquire a lease.
3. **Retry success:** generation 1 leased and completed with attempts preserved correctly.
4. **Retry exhaustion:** a `max_attempts=2` task dead-lettered on the second failed execution with exactly one archive row and the correct failed generation/class.
5. **Selective replay:** a mismatched failure-class filter replayed nothing; the correct ID/type/class selector created exactly one replacement task.
6. **Replay audit/lineage:** replacement task carries original-task and dead-letter lineage, configured priority/max attempts, and one replay-audit row.
7. **Replay idempotency:** selecting the already replayed archive again produced no duplicate replacement task.
8. **Replay executability:** the replacement task passed through the same outbox/Redis/lease path and completed normally.
9. All prior control-plane, outbox, and crash-recovery tests continued to pass with migration `0004_retry_replay_audit.sql` applied.

### Status after Segment 3

The **core P0-01 task lifecycle is now implemented and integration-verified at the current single-region control-plane scope**: durable creation, idempotency, outbox delivery, generation fencing, worker leasing/heartbeat, crash recovery, scheduled retry, stale-message rejection, dead-lettering, replay, and durable completion/ACK.

This does **not** mean provider-scale production certification is complete. Multi-process load tests, dispatcher/worker storms, literal process kills, Redis/PostgreSQL failover, cross-region timing, and sustained/soak testing remain P2 reliability/scale work.

### Next recommended segment

**Segment 4 — session/token lease state machine verification.** Validate per-session `max_concurrency`, lease expiry/recovery, cooldown transitions, failover ownership, and ensure a dead worker cannot strand or over-consume one session. Keep credential/secret-store redesign as the following segment unless verification exposes a schema blocker.
