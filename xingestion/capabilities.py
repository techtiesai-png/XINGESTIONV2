from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from importlib.resources import files
from types import MappingProxyType
from typing import Any

CAPABILITY_TASK_TYPE = "CAPABILITY_REQUEST"
LEGACY_SEARCH_TASK_TYPE = "X_KEYWORD_SEARCH"


class CapabilityContractError(ValueError):
    pass


class UnsupportedCapability(CapabilityContractError):
    pass


class NoApprovedRoute(CapabilityContractError):
    pass


class ExecutorKind(StrEnum):
    LEGACY_SOURCE_ADAPTER = "LEGACY_SOURCE_ADAPTER"
    XREV_PROTOCOL_RUNTIME = "XREV_PROTOCOL_RUNTIME"
    FIXTURE = "FIXTURE"


@dataclass(frozen=True, slots=True)
class InputFieldSpec:
    value_type: str
    required: bool
    nullable: bool = False
    default: Any = None
    has_default: bool = False
    enum: tuple[Any, ...] = ()
    minimum: int | None = None
    maximum: int | None = None
    minimum_length: int | None = None
    value_format: str | None = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> InputFieldSpec:
        return cls(
            value_type=str(value["type"]),
            required=bool(value.get("required", False)),
            nullable=bool(value.get("nullable", False)),
            default=value.get("default"),
            has_default="default" in value,
            enum=tuple(value.get("enum", ())),
            minimum=value.get("minimum"),
            maximum=value.get("maximum"),
            minimum_length=value.get("minimum_length"),
            value_format=value.get("format"),
        )


@dataclass(frozen=True, slots=True)
class CapabilitySpec:
    capability_id: str
    contract_version: str
    description: str
    inputs: Mapping[str, InputFieldSpec]
    output: Mapping[str, Any]
    pagination: Mapping[str, Any]
    required_fidelity: tuple[str, ...]
    required_provenance: tuple[str, ...]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CapabilitySpec:
        inputs = {
            name: InputFieldSpec.from_dict(spec)
            for name, spec in sorted(value.get("inputs", {}).items())
        }
        return cls(
            capability_id=str(value["capability_id"]),
            contract_version=str(value["contract_version"]),
            description=str(value.get("description", "")),
            inputs=MappingProxyType(inputs),
            output=MappingProxyType(dict(value.get("output", {}))),
            pagination=MappingProxyType(dict(value.get("pagination", {}))),
            required_fidelity=tuple(value.get("required_fidelity", ())),
            required_provenance=tuple(value.get("required_provenance", ())),
        )

    def validate_params(self, params: Mapping[str, Any]) -> Mapping[str, Any]:
        unknown = sorted(set(params) - set(self.inputs))
        if unknown:
            raise CapabilityContractError(
                f"unsupported {self.capability_id} input(s): {', '.join(unknown)}"
            )

        normalized: dict[str, Any] = {}
        for name, spec in self.inputs.items():
            if name not in params:
                if spec.has_default:
                    normalized[name] = spec.default
                elif spec.required:
                    raise CapabilityContractError(
                        f"{self.capability_id} input {name!r} is required"
                    )
                continue
            normalized[name] = _validate_input(name, params[name], spec)
        return MappingProxyType(normalized)


@dataclass(frozen=True, slots=True)
class CapabilityRequest:
    capability_id: str
    capability_contract_version: str
    params: Mapping[str, Any]
    cursor: str | None = None
    page_size: int | None = None
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        if not self.capability_id or not self.capability_contract_version:
            raise CapabilityContractError(
                "capability_id and capability_contract_version are required"
            )
        if self.cursor is not None and not isinstance(self.cursor, str):
            raise CapabilityContractError("cursor must be an opaque string or null")
        if self.page_size is not None and (
            isinstance(self.page_size, bool)
            or not isinstance(self.page_size, int)
            or self.page_size <= 0
        ):
            raise CapabilityContractError("page_size must be a positive integer")

    def to_task_payload(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "capability_contract_version": self.capability_contract_version,
            "params": dict(self.params),
            "cursor": self.cursor,
            "page_size": self.page_size,
            "correlation_id": self.correlation_id,
        }


class CapabilityCatalog:
    def __init__(
        self, specs: Sequence[CapabilitySpec], *, artifact_schema_version: str
    ) -> None:
        if not artifact_schema_version:
            raise CapabilityContractError("artifact_schema_version is required")
        if not specs:
            raise CapabilityContractError("capability catalog must not be empty")
        by_key: dict[tuple[str, str], CapabilitySpec] = {}
        for spec in specs:
            key = (spec.capability_id, spec.contract_version)
            if key in by_key:
                raise CapabilityContractError(f"duplicate capability contract: {key}")
            by_key[key] = spec
        self._specs = MappingProxyType(by_key)
        self.artifact_schema_version = artifact_schema_version

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> CapabilityCatalog:
        return cls(
            [CapabilitySpec.from_dict(item) for item in document.get("capabilities", ())],
            artifact_schema_version=str(document["artifact_schema_version"]),
        )

    @classmethod
    def load_default(cls) -> CapabilityCatalog:
        resource = files("xingestion").joinpath("contracts/capabilities.v1.json")
        return cls.from_document(json.loads(resource.read_text(encoding="utf-8")))

    def require(self, capability_id: str, contract_version: str) -> CapabilitySpec:
        try:
            return self._specs[(capability_id, contract_version)]
        except KeyError as error:
            raise UnsupportedCapability(
                f"unsupported capability contract {capability_id}@{contract_version}"
            ) from error

    def validate(self, request: CapabilityRequest) -> CapabilityRequest:
        spec = self.require(request.capability_id, request.capability_contract_version)
        page_size = request.page_size
        if page_size is not None:
            maximum = int(spec.pagination.get("maximum_page_size", page_size))
            if page_size > maximum:
                raise CapabilityContractError(
                    f"page_size exceeds {request.capability_id} contract maximum {maximum}"
                )
        return CapabilityRequest(
            capability_id=request.capability_id,
            capability_contract_version=request.capability_contract_version,
            params=spec.validate_params(request.params),
            cursor=request.cursor,
            page_size=page_size,
            correlation_id=request.correlation_id,
        )


@dataclass(frozen=True, slots=True)
class CapabilityRoute:
    route_id: str
    capability_id: str
    capability_contract_version: str
    executor_kind: ExecutorKind
    priority: int = 100
    enabled: bool = True
    supported_products: tuple[str, ...] = ()
    supported_inputs: tuple[str, ...] = ()
    maximum_page_size: int | None = None
    maximum_pages: int | None = None
    protocol_release_manifest: str | None = None

    def __post_init__(self) -> None:
        if not self.route_id or not self.capability_id or not self.capability_contract_version:
            raise CapabilityContractError(
                "route_id, capability_id and capability_contract_version are required"
            )
        for name, value in (
            ("maximum_page_size", self.maximum_page_size),
            ("maximum_pages", self.maximum_pages),
        ):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value <= 0
            ):
                raise CapabilityContractError(f"{name} must be a positive integer")
        if (
            self.executor_kind is ExecutorKind.XREV_PROTOCOL_RUNTIME
            and not self.protocol_release_manifest
        ):
            raise CapabilityContractError(
                "XREV_PROTOCOL_RUNTIME routes require a protocol_release_manifest"
            )


@dataclass(frozen=True, slots=True)
class AcquisitionPlan:
    request: CapabilityRequest
    route: CapabilityRoute
    effective_page_size: int
    effective_max_pages: int
    warnings: tuple[str, ...] = ()


class CapabilityPlanner:
    def __init__(
        self, catalog: CapabilityCatalog, routes: Sequence[CapabilityRoute]
    ) -> None:
        self.catalog = catalog
        self.routes = tuple(routes)

    def plan(self, request: CapabilityRequest) -> AcquisitionPlan:
        validated = self.catalog.validate(request)
        spec = self.catalog.require(
            validated.capability_id, validated.capability_contract_version
        )
        product = validated.params.get("product")
        candidates = [
            route
            for route in self.routes
            if route.enabled
            and route.capability_id == validated.capability_id
            and route.capability_contract_version
            == validated.capability_contract_version
            and (
                not route.supported_products
                or product is None
                or product in route.supported_products
            )
            and _route_supports_params(route, spec, validated.params)
        ]
        if not candidates:
            raise NoApprovedRoute(
                f"no approved route for {validated.capability_id}@"
                f"{validated.capability_contract_version}"
            )
        route = sorted(candidates, key=lambda item: (item.priority, item.route_id))[0]
        requested_page_size = validated.page_size or int(
            spec.pagination.get("default_page_size", 20)
        )
        requested_pages = int(validated.params.get("max_pages", 1))
        warnings: list[str] = []
        effective_page_size = requested_page_size
        if (
            route.maximum_page_size is not None
            and effective_page_size > route.maximum_page_size
        ):
            effective_page_size = route.maximum_page_size
            warnings.append("PAGE_SIZE_CLAMPED_TO_ROUTE")
        effective_max_pages = requested_pages
        if (
            route.maximum_pages is not None
            and effective_max_pages > route.maximum_pages
        ):
            effective_max_pages = route.maximum_pages
            warnings.append("MAX_PAGES_CLAMPED_TO_ROUTE")
        return AcquisitionPlan(
            request=validated,
            route=route,
            effective_page_size=effective_page_size,
            effective_max_pages=effective_max_pages,
            warnings=tuple(warnings),
        )


def request_from_task(
    task_type: str,
    payload: Mapping[str, Any],
    *,
    correlation_id: str | None = None,
) -> CapabilityRequest:
    if task_type == CAPABILITY_TASK_TYPE:
        params = payload.get("params")
        if not isinstance(params, Mapping):
            raise CapabilityContractError("capability task params must be an object")
        return CapabilityRequest(
            capability_id=str(payload.get("capability_id") or ""),
            capability_contract_version=str(
                payload.get("capability_contract_version") or ""
            ),
            params=dict(params),
            cursor=_optional_string(payload.get("cursor")),
            page_size=_optional_integer(payload.get("page_size")),
            correlation_id=_optional_string(
                payload.get("correlation_id") or correlation_id
            ),
        )
    if task_type == LEGACY_SEARCH_TASK_TYPE:
        query = str(payload.get("search_keyword") or "").strip()
        params: dict[str, Any] = {
            "query": query,
            "product": str(payload.get("product") or "Latest"),
            "max_pages": _legacy_positive_integer(payload.get("max_pages", 1)),
        }
        return CapabilityRequest(
            capability_id="SEARCH_TWEETS",
            capability_contract_version="1",
            params=params,
            cursor=_optional_string(payload.get("cursor")),
            page_size=(
                _legacy_positive_integer(payload["page_size"])
                if payload.get("page_size") is not None
                else None
            ),
            correlation_id=correlation_id,
        )
    raise UnsupportedCapability(f"unsupported task_type={task_type}")


def legacy_search_planner(
    *, maximum_page_size: int = 20, maximum_pages: int = 1
) -> CapabilityPlanner:
    return CapabilityPlanner(
        CapabilityCatalog.load_default(),
        [
            CapabilityRoute(
                route_id="legacy-source-adapter/search-tweets/v1",
                capability_id="SEARCH_TWEETS",
                capability_contract_version="1",
                executor_kind=ExecutorKind.LEGACY_SOURCE_ADAPTER,
                supported_products=("Latest",),
                supported_inputs=("query", "product", "max_pages"),
                maximum_page_size=maximum_page_size,
                maximum_pages=maximum_pages,
            )
        ],
    )


def _validate_input(name: str, value: Any, spec: InputFieldSpec) -> Any:
    if value is None:
        if spec.nullable:
            return None
        raise CapabilityContractError(f"input {name!r} cannot be null")
    expected = {
        "string": str,
        "integer": int,
        "boolean": bool,
    }.get(spec.value_type)
    if expected is None:
        raise CapabilityContractError(
            f"unsupported contract field type {spec.value_type!r} for {name!r}"
        )
    if expected is int and isinstance(value, bool):
        raise CapabilityContractError(f"input {name!r} must be an integer")
    if not isinstance(value, expected):
        raise CapabilityContractError(f"input {name!r} must be {spec.value_type}")
    if spec.enum and value not in spec.enum:
        raise CapabilityContractError(
            f"input {name!r} must be one of {', '.join(map(str, spec.enum))}"
        )
    if isinstance(value, str) and spec.minimum_length is not None:
        if len(value.strip()) < spec.minimum_length:
            raise CapabilityContractError(
                f"input {name!r} must contain at least {spec.minimum_length} character(s)"
            )
        value = value.strip()
    if isinstance(value, str) and spec.value_format == "date":
        try:
            date.fromisoformat(value)
        except ValueError as error:
            raise CapabilityContractError(
                f"input {name!r} must use ISO date format YYYY-MM-DD"
            ) from error
    if isinstance(value, int) and not isinstance(value, bool):
        if spec.minimum is not None and value < spec.minimum:
            raise CapabilityContractError(f"input {name!r} is below minimum")
        if spec.maximum is not None and value > spec.maximum:
            raise CapabilityContractError(f"input {name!r} exceeds maximum")
    return value


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise CapabilityContractError("optional value must be a string")
    return value


def _optional_integer(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise CapabilityContractError("optional value must be an integer")
    return value


def _legacy_positive_integer(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise CapabilityContractError("legacy numeric value must be an integer") from error
    if parsed <= 0:
        raise CapabilityContractError("legacy numeric value must be positive")
    return parsed


def _route_supports_params(
    route: CapabilityRoute,
    spec: CapabilitySpec,
    params: Mapping[str, Any],
) -> bool:
    if not route.supported_inputs:
        return True
    supported = set(route.supported_inputs)
    for name, value in params.items():
        if name in supported:
            continue
        field_spec = spec.inputs[name]
        if value is None:
            continue
        if field_spec.has_default and value == field_spec.default:
            continue
        return False
    return True
