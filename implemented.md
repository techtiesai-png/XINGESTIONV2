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
