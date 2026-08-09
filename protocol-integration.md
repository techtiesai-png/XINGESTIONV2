# XINGESTIONV2 ↔ X-rev-os Protocol Integration Contract

> Status: authoritative cross-repository boundary. Read with `AGENTS.md`, `architecture.md`, `plan.md`, and `implemented.md` before protocol/acquisition work.

## 1. Why the repositories are separate

XINGESTIONV2 is the **core production ingestion repository**. It owns the complete ingestion system and remains the place where all major system-level architecture stays bonded together and cross-referenced.

`techtiesai-png/X-rev-os` is a deliberately separated specialist repository for X/Twitter protocol research, reverse engineering, validation and the canonical X-specific executable protocol runtime.

The split exists so reverse-engineering/browser/research machinery can change aggressively without forcing production queue/storage/analytics code to change or making production depend on a research browser.

The split is **not** permission for the repositories to become independent undocumented projects. They share a versioned integration contract and must cross-reference material changes.

---

## 2. Canonical ownership

### XINGESTIONV2 owns

- canonical product/public `CapabilitySpec` and contract versions;
- durable task ledger, outbox, delivery bus and workers;
- task lease/fencing/recovery;
- **all production retry/backoff policy**;
- production account/session pool and session leasing;
- production proxy/network allocation;
- production raw-evidence storage/retention;
- canonical production entity/observation/edge models;
- production normalization/reprocessing;
- monitor/subscription scheduling and catch-up;
- analytics/alerts/briefs;
- northbound API and parent/NOS integration;
- provider-scale deployment, capacity and multi-region decisions;
- the pinned X-rev release used by production.

### X-rev-os owns

- X protocol observation/research evidence;
- `ProtocolCapabilityBinding` from XINGESTION capability IDs to X acquisition recipes;
- acquisition recipe revisions;
- operation/request definitions;
- X request construction;
- X-specific parser implementations;
- X pagination interpretation;
- X auth/session attachment semantics;
- X client-transaction/shared-request-metadata algorithms;
- feature/config/client-profile protocol bundles;
- protocol-specific typed errors;
- fixture/corpus and protocol validation;
- runtime and protocol-bundle exports;
- protocol drift/candidate research and future bounded protocol-healing logic.

There is **one canonical implementation of X-specific protocol behavior: X-rev-os**.

Do not maintain separate competing production parser/request/pagination/transaction implementations in XINGESTIONV2 after the integration boundary is established.

---

## 3. Capability mapping

XINGESTIONV2's northbound capability contract remains protocol-neutral.

Example:

```text
CapabilityRequest
    capability_id = SEARCH_TWEETS
    capability_contract_version = 1
    params = {...}
```

X-rev-os publishes a binding such as:

```text
ProtocolCapabilityBinding
    capability_id = SEARCH_TWEETS
    capability_contract_version = 1
    acquisition_recipe_revision = recipe_search_latest_7
```

XINGESTIONV2 must not expose operation/query IDs in its public capability contract.

Until a generated shared contract artifact exists, stable capability IDs may be mirrored in X-rev-os only for research compatibility and must be marked non-authoritative there.

---

## 4. Production execution boundary

Conceptually XINGESTIONV2 invokes the pinned X-rev runtime as:

```text
ProtocolRequest
+ SessionContext selected/leased by XINGESTIONV2
+ NetworkContext/transport allocated by XINGESTIONV2
+ RawEvidenceSink implemented by XINGESTIONV2
+ pinned X-rev protocol release
        |
        v
X-rev protocol runtime
        |
        +--> AcquisitionResult
        |
        +--> typed ProtocolError
```

X-rev owns how the supplied session state is attached to X requests.

XINGESTIONV2 owns which session/network route gets selected and leased.

Credentials/password/TOTP material are not ordinary protocol-request inputs.

---

## 5. Retry ownership

The exported X-rev runtime performs **zero automatic production retries**.

XINGESTIONV2 is the only owner of production task retry/backoff/attempt policy.

X-rev typed failures may provide:

```text
retry_disposition = NEVER | MAY_RETRY | RETRY_AFTER
retry_after
scope_hint
protocol provenance
raw evidence refs
safe diagnostics
```

XINGESTIONV2 uses that information when applying its durable retry policy.

Do not multiply retries by combining worker attempts with hidden protocol-runtime or HTTP-client retries.

---

## 6. Production raw evidence

Production raw evidence belongs to XINGESTIONV2's Data Plane.

XINGESTIONV2 supplies a `RawEvidenceSink` to the X-rev runtime.

For each relevant protocol response:

1. X-rev passes the raw capture + safe provenance to the injected sink;
2. the sink persists it to XINGESTIONV2-managed production storage;
3. the sink returns a durable `RawEvidenceRef`;
4. X-rev parses/returns protocol-normalized output with that reference;
5. XINGESTIONV2 performs production canonical normalization/reprocessing downstream.

Production must never depend on `X-rev-os/captures/local`, its SQLite research store or any research workstation path.

If required raw persistence fails, the acquisition should fail with a typed raw-evidence persistence error rather than silently claiming safe acquisition.

---

## 7. Version/release contract

Version independently:

```text
xrev-runtime
protocol-bundle
capability-contract
```

A production `ProtocolReleaseManifest` binds an exact tested combination:

```text
runtime_version
protocol_bundle_version
capability_contract_version
source_git_commit(s)
checksums
validated acquisition recipes
compatibility constraints
```

XINGESTIONV2 pins an exact release manifest/checksum.

Do not consume floating `latest` state from X-rev-os in production.

A query/document-ID-only change may require only a new protocol bundle/release validation. A parser/transaction/pagination code change normally requires a runtime version change as well.

---

## 8. Validation and promotion responsibility

X-rev-os validates exact immutable `AcquisitionRecipeRevision` compositions and records evidence maturity, validation freshness and protocol health separately.

Initially, human/researcher approval governs promotion/quarantine/retirement of newly validated X-rev protocol releases.

XINGESTIONV2 owns production rollout/routing among **already approved and compatible** X-rev releases/recipes.

Future safe failover may select a previously approved compatible alternate automatically. XINGESTIONV2 must not invent or mutate new X protocol definitions itself when current protocol knowledge breaks.

If no approved route remains, production should degrade the affected capability and request/consume an X-rev investigation/candidate package rather than allowing the queue/control plane to reverse-engineer X inline.

---

## 9. Health boundary

XINGESTIONV2 observes production behavior:

- success/failure rates;
- latency;
- session/cohort distribution;
- protocol typed error codes/scope hints;
- parser/pagination warnings;
- raw schema fingerprints/provenance;
- capability availability.

X-rev-os owns deep protocol diagnosis/research and candidate validation.

This creates a feedback loop:

```text
XINGESTIONV2 production evidence
        |
        v
protocol degradation signal / investigation package
        |
        v
X-rev-os research + candidate validation
        |
        v
new approved ProtocolReleaseManifest
        |
        v
XINGESTIONV2 controlled rollout
```

Production research/discovery tooling is not required for normal stable collection.

---

## 10. Documentation synchronization

Both repositories must remain cross-referenced.

When an X-rev change affects any of the following:

- production runtime API;
- capability binding/contract compatibility;
- typed error contract;
- raw-evidence sink contract;
- release manifest/version compatibility;
- session/network input requirements;
- validated capability availability;

then the X-rev change should record the impact in `X-rev-os/implemented.md`, and the corresponding XINGESTIONV2 docs should be updated in the same workstream when practical.

When XINGESTIONV2 changes the canonical CapabilitySpec or production integration requirements, update/cross-reference X-rev-os accordingly.

XINGESTIONV2's `implemented.md` should record the exact X-rev release/version/commit currently integrated once runtime integration exists.

Do not leave cross-repository integration decisions only in chat history.

---

## 11. Research repository location

Canonical specialist repository:

```text
GitHub: techtiesai-png/X-rev-os
```

Its standing docs are:

```text
AGENTS.md
architecture.md
plan.md
implemented.md
```

Do not copy the full research architecture into this repository. Keep this document as the integration/ownership contract and link to the X-rev repository for detailed protocol research/runtime design.

---

## 12. Evolution rule

This boundary is strong but not sacred implementation ceremony.

If measured engineering evidence shows a better packaging, repository split, documentation structure or runtime interface, Codex/researchers may propose it.

Any change must:

1. preserve one canonical protocol authority;
2. preserve production/research failure isolation;
3. preserve evidence/provenance and reproducibility;
4. preserve capability-contract stability;
5. prevent duplicate retry/storage authorities;
6. include a migration/compatibility plan;
7. update both repositories' relevant documentation and ledgers coherently.