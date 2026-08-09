from __future__ import annotations

import json
from importlib.resources import files

import pytest

from xingestion.capabilities import (
    CAPABILITY_TASK_TYPE,
    LEGACY_SEARCH_TASK_TYPE,
    CapabilityCatalog,
    CapabilityContractError,
    CapabilityPlanner,
    CapabilityRequest,
    CapabilityRoute,
    CapabilitySpec,
    ExecutorKind,
    NoApprovedRoute,
    legacy_search_planner,
    request_from_task,
)


def test_default_search_contract_is_typed_and_protocol_neutral():
    catalog = CapabilityCatalog.load_default()
    request = catalog.validate(
        CapabilityRequest(
            capability_id="SEARCH_TWEETS",
            capability_contract_version="1",
            params={"query": "  artificial intelligence  "},
        )
    )

    assert request.params["query"] == "artificial intelligence"
    assert request.params["product"] == "Latest"
    assert request.params["max_pages"] == 2

    raw_contract = files("xingestion").joinpath(
        "contracts/capabilities.v1.json"
    ).read_text(encoding="utf-8")
    lowered = raw_contract.lower()
    for forbidden in (
        "twikit",
        "twscrape",
        "operation_id",
        "query_id",
        "browser_selector",
        "authorization",
        "cookie",
    ):
        assert forbidden not in lowered


def test_legacy_search_task_maps_to_canonical_capability():
    request = request_from_task(
        LEGACY_SEARCH_TASK_TYPE,
        {
            "search_keyword": "open source",
            "max_pages": "2",
            "page_size": "20",
            "cursor": "opaque-value",
        },
        correlation_id="task:42:generation:0",
    )
    plan = legacy_search_planner(maximum_pages=2).plan(request)

    assert plan.request.capability_id == "SEARCH_TWEETS"
    assert plan.request.capability_contract_version == "1"
    assert plan.request.params["query"] == "open source"
    assert plan.effective_max_pages == 2
    assert plan.request.cursor == "opaque-value"


def test_canonical_task_payload_round_trip():
    original = CapabilityRequest(
        capability_id="SEARCH_TWEETS",
        capability_contract_version="1",
        params={"query": "OpenAI", "product": "Latest", "max_pages": 1},
        page_size=15,
        correlation_id="correlation-1",
    )
    restored = request_from_task(CAPABILITY_TASK_TYPE, original.to_task_payload())
    plan = legacy_search_planner(maximum_pages=2).plan(restored)

    assert plan.request.capability_id == original.capability_id
    assert plan.effective_page_size == 15
    assert plan.request.correlation_id == "correlation-1"


def test_legacy_route_rejects_semantics_it_cannot_honor():
    planner = legacy_search_planner(maximum_pages=2)
    with pytest.raises(NoApprovedRoute):
        planner.plan(
            CapabilityRequest(
                capability_id="SEARCH_TWEETS",
                capability_contract_version="1",
                params={"query": "example", "product": "Top"},
            )
        )
    with pytest.raises(NoApprovedRoute):
        planner.plan(
            CapabilityRequest(
                capability_id="SEARCH_TWEETS",
                capability_contract_version="1",
                params={"query": "example", "from_user": "someone"},
            )
        )


def test_contract_rejects_unknown_and_malformed_inputs():
    catalog = CapabilityCatalog.load_default()
    with pytest.raises(CapabilityContractError):
        catalog.validate(
            CapabilityRequest(
                capability_id="SEARCH_TWEETS",
                capability_contract_version="1",
                params={"query": "example", "unknown": True},
            )
        )
    with pytest.raises(CapabilityContractError):
        catalog.validate(
            CapabilityRequest(
                capability_id="SEARCH_TWEETS",
                capability_contract_version="1",
                params={"query": "example", "since": "not-a-date"},
            )
        )


def test_xrev_runtime_route_requires_an_immutable_release_manifest():
    with pytest.raises(CapabilityContractError):
        CapabilityRoute(
            route_id="xrev/search-tweets/v1",
            capability_id="SEARCH_TWEETS",
            capability_contract_version="1",
            executor_kind=ExecutorKind.XREV_PROTOCOL_RUNTIME,
        )


def test_second_capability_uses_same_planner_without_queue_changes():
    fake_spec = CapabilitySpec.from_dict(
        {
            "capability_id": "TEST_ECHO",
            "contract_version": "1",
            "description": "Planner extensibility fixture.",
            "inputs": {
                "value": {"type": "string", "required": True, "minimum_length": 1}
            },
            "output": {"record_type": "test"},
            "pagination": {"default_page_size": 1, "maximum_page_size": 1},
            "required_fidelity": [],
            "required_provenance": ["task_id"],
        }
    )
    planner = CapabilityPlanner(
        CapabilityCatalog([fake_spec], artifact_schema_version="test"),
        [
            CapabilityRoute(
                route_id="fixture/test-echo/v1",
                capability_id="TEST_ECHO",
                capability_contract_version="1",
                executor_kind=ExecutorKind.FIXTURE,
                supported_inputs=("value",),
                maximum_page_size=1,
                maximum_pages=1,
            )
        ],
    )

    plan = planner.plan(
        CapabilityRequest(
            capability_id="TEST_ECHO",
            capability_contract_version="1",
            params={"value": "hello"},
        )
    )
    assert plan.route.executor_kind is ExecutorKind.FIXTURE
    assert plan.request.params["value"] == "hello"


def test_contract_artifact_is_valid_json_and_versioned():
    document = json.loads(
        files("xingestion")
        .joinpath("contracts/capabilities.v1.json")
        .read_text(encoding="utf-8")
    )
    assert document["artifact_schema_version"] == "1"
    assert document["capabilities"][0]["contract_version"] == "1"
