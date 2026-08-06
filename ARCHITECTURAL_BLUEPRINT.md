# Architectural Blueprint

This document describes the current social listening platform in the workspace, the runtime services around it, and the operational boundaries that keep it maintainable and safe to deploy.

## 1. System Capability & Core Logic

### What the engine does

The platform is designed to ingest high-volume public text streams into PostgreSQL-backed storage, normalize the payloads, and make them available for downstream analytics and UI consumption.

- Handles sustained ingestion workloads in the 50,000 to 100,000+ items per day range.
- Stores structured records in `social_insights_feed`.
- Preserves deduplication using a stable unique tweet/document identifier.
- Feeds downstream alerting, brief generation, and API delivery layers.

### Internal API emulation paradigm

The core idea is to keep the extraction layer lightweight and deterministic by using API-shaped requests and JSON parsing rather than heavyweight browser automation for the main ingestion path.

Benefits:

- Lower memory footprint than browser-first crawlers.
- Faster turnaround for structured payload collection.
- Simpler retry and validation logic.
- Easier integration with async worker patterns.

Typical worker memory overhead is intentionally kept low by relying on async I/O, JSON payload validation, and database writes instead of rendering full pages.

```
Public text stream
       |
       v
Async request / JSON parse
       |
       v
Pydantic validation
       |
       v
PostgreSQL upsert
       |
       v
Alerting / briefs / API
```

## 2. Architectural Separation & Safety Features

### 3-tier resiliency strategy

The platform is separated into operational tiers so that one failure mode does not collapse the entire stack.

#### Tier 1: Raw HTTP async speed layer

- Primary path for structured payload retrieval.
- Uses `asyncio` and `httpx` for low-latency network work.
- Keeps CPU and RAM usage small by avoiding browser rendering.

#### Tier 2: Database token-leasing fallback pool

- Uses row-locked task and token checkout patterns in PostgreSQL.
- Prevents duplicate leasing across concurrent workers.
- Supports controlled retries, cooldowns, and dead-letter routing.

#### Tier 3: Browser-based recovery layer

- Reserved for exceptional cases where the primary structured path is unavailable.
- In a compliant production system, this tier should be governed by policy, review, and platform terms.
- It should be isolated from core app logic so it cannot destabilize the main worker loop.

```
Tier 1: Async HTTP ingestion
      | fail
      v
Tier 2: DB leasing + retry control
      | fail
      v
Tier 3: Reviewed recovery path
```

### Environment isolation

Runtime infrastructure is isolated from application logic using environment variables.

- `DATABASE_DSN` controls the PostgreSQL connection string.
- `HTTP_PROXY` can be used to route traffic through an approved gateway when required by infrastructure policy.
- `LLM_API_KEY` is isolated from source code and injected at runtime.
- `MOCK_MODE` allows safe local validation without touching production systems.

This separation keeps secrets and transport settings out of the application codebase.

## 3. Detailed State Machine Matrix

### Token lifecycle states

The worker token lifecycle is modeled in the database with clear operational states.

- `ACTIVE`: eligible for leasing and normal use.
- `COOLDOWN`: temporarily paused after an error condition or transient failure.
- `REVOKED`: no longer valid for use and should be removed from circulation.

### Failure handling flow

The worker pipeline uses structured exception handling so that failures do not cascade uncontrolled through the stack.

Operational behavior:

- `401` / `403` style responses are treated as authentication/session failures.
- `429` style responses are treated as rate-limit pressure.
- Network timeouts are treated as transient transport failures.
- Tokens are placed into cooldown for 300 seconds when recovery is possible.
- Tasks are retried until they hit their maximum attempts.
- Once attempts are exhausted, the task is routed to dead-letter handling.

State flow:

```
ACTIVE -> error detected -> COOLDOWN -> cooldown expires -> ACTIVE
ACTIVE -> repeated failures -> RETRYING -> DEAD_LETTER
```

### Notes on durability

- Cooldown transitions should be timestamp-driven and reversible.
- Dead-lettered tasks should preserve error context for later inspection.
- Recovery logic should be monitored so tokens do not remain paused indefinitely.

## 4. Commercial Competitive Comparison

### Positioning

This stack is best understood as a custom, workflow-owned ingestion system rather than a turnkey SaaS suite.

| Capability | This Platform | Brandwatch | Sprinklr | Talkwalker |
|---|---:|---:|---:|---:|
| Custom ingestion control | High | Medium | Medium | Medium |
| Async worker orchestration | High | Low | Low | Low |
| Database-native deduplication | High | Medium | Medium | Medium |
| Internal alerting/briefing extensibility | High | Medium | High | High |
| UI/API ownership | High | Medium | High | High |
| Operational customization | High | Medium | High | High |


## 5. Data De-duplication and Multi-Process Consumption

### Deduplication guarantees

The ingestion schema includes a unique constraint on `original_tweet_id`, which prevents duplicate rows from being inserted for the same item.

The parser layer uses `ON CONFLICT (original_tweet_id) DO UPDATE` so repeat observations update the existing record instead of creating duplicates.

The update strategy preserves the highest observed engagement counters:

- `engagement_likes` uses `GREATEST(existing, incoming)`.
- `engagement_retweets` uses `GREATEST(existing, incoming)`.

That means a viral item can be observed multiple times without corrupting totals.

### Multi-process consumption model

Separate processes read from the same analytical store without blocking the core ingestion loop.

- `analytics_alerts.py` scans recent records every 60 seconds.
- `analytics_briefs.py` runs hourly summarization over the latest window.
- `api_server.py` exposes read-only endpoints for the UI.

This arrangement keeps write-heavy ingestion separate from read-heavy analytics.

```
worker.py
   |
   v
social_insights_feed
   |        |           |
   v        v           v
alerts   briefs       api_server
```

## 6. Developer Integration & Systemd Deployment Guide

### File layout

```
/Users/pruthavirajsingh/Geoatlas-x-NOS/
├── .venv/
├── .vscode/
├── ARCHITECTURAL_BLUEPRINT.md
├── analytics_alerts.py
├── analytics_briefs.py
├── analytics_parser.py
├── api_server.py
├── docker-compose.yml
├── ingestion_engine.py
├── schema_analytics.sql
├── seed_test.py
├── systemd/
│   ├── x_api_server.service
│   ├── x_analytics_alerts.service
│   └── x_ingestion_worker.service
└── worker.py
```

### Onboarding flow for a new engineer

#### Local validation

1. Activate the virtual environment.
2. Install dependencies if needed.
3. Run the schema initializer via Docker Compose.
4. Seed the test database.
5. Launch the worker in mock mode or against the local database.

Example flow:

```bash
docker compose up -d postgres-db db-initializer
DATABASE_DSN='postgresql://app_user:app_password@localhost:5432/appdb' .venv/bin/python seed_test.py
MOCK_MODE=true .venv/bin/python worker.py
```

#### Production-style deployment

1. Confirm environment variables are configured.
2. Install the systemd unit files into the server’s service directory.
3. Enable the services.
4. Start them in dependency order.
5. Monitor logs and health endpoints.

Example service set:

- `x_ingestion_worker.service`
- `x_analytics_alerts.service`
- `x_api_server.service`

### Operational checklist

- Confirm `DATABASE_DSN` is valid.
- Confirm PostgreSQL is reachable.
- Confirm dead-letter handling is monitored.
- Confirm `MOCK_MODE` is disabled in production.
- Confirm `LLM_API_KEY` is only present on the briefing host.
- Confirm logs are centralized.

## 7. Current Workspace Inventory

This is the current product-facing file layout in the root workspace directory.

### Root Directory Files

- `worker.py` - The parallel ingestion engine running the 3-tier fallback machine and structured JSON logging formatters.
- `token_refresh_service.py` - The out-of-band programmatic session repair worker script.
- `analytics_parser.py` - The validation layer holding the Pydantic schemas and SHA-256 text content deduplication hashes.
- `analytics_alerts.py` - The velocity anomaly scanner monitoring pre-computed hourly database rollups.
- `analytics_briefs.py` - The hourly summary daemon routing trending text into LLM context templates.
- `api_server.py` - The secured FastAPI gateway delivering JSON metrics to dashboard interfaces.
- `seed_test.py` - The testing integration seeder for local queue and token population.
- `schema_analytics.sql` - The SQL setup defining indices, dead-letter tables, and summary archives.
- `docker-compose.yml` - The infrastructure file orchestrating PostgreSQL and Redis containers.
- `nginx_gateway.conf` - The server edge proxy config handling reverse routing and rate limiting.
- `ARCHITECTURAL_BLUEPRINT.md` - The developer handover reference manual.

### Systemd Subdirectory

- `systemd/x_ingestion_worker.service` - Linux process monitor configuration for the core workers.
- `systemd/x_token_refresh.service` - Linux process monitor configuration for the session restorer.
- `systemd/x_analytics_alerts.service` - Linux process monitor configuration for the spike metrics tracker.
- `systemd/x_api_server.service` - Linux process monitor configuration for the FastAPI Uvicorn engine.

## Closing Notes

The current codebase gives you a modular social listening platform with clear ingestion, analytics, and API layers. For production use, the next hardening steps are:

- durable dead-letter storage and replay tooling
- dedicated briefs table
- token cooldown cleanup jobs
- observability dashboards
- load testing and memory guardrails
- security review of all collection paths and secrets handling
