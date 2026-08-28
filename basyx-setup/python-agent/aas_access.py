"""Generic BaSyx Operation invocation through discovered Submodel endpoints."""

from typing import Any
from urllib.parse import quote

import httpx

from semantic_model import OperationBinding


def operation_invoke_url(binding: OperationBinding) -> str:
    if not binding.submodel_endpoint:
        raise ValueError("Operation Submodel has no Registry endpoint")
    if not binding.operation_ref.id_short_path:
        raise ValueError("Operation has no idShort path")
    path = quote(binding.operation_ref.id_short_path, safe=".")
    return (
        f"{binding.submodel_endpoint.rstrip('/')}"
        f"/submodel-elements/{path}/invoke"
    )


def build_invocation_payload(
    binding: OperationBinding,
    semantic_arguments: dict[str, Any],
    *,
    requested_timeout_ms: int,
    metadata_arguments: dict[str, Any] | None = None,
) -> dict:
    input_arguments: list[dict] = []
    mapped_semantics: set[str] = set()
    for parameter in binding.parameters:
        matches = sorted(parameter.semantic_ids.intersection(semantic_arguments))
        if not matches:
            continue
        semantic_id = matches[0]
        mapped_semantics.add(semantic_id)
        input_arguments.append(
            {
                "value": {
                    "modelType": "Property",
                    "idShort": parameter.id_short,
                    "valueType": parameter.value_type or "xs:string",
                    "value": semantic_arguments[semantic_id],
                    "semanticId": {
                        "type": "ExternalReference",
                        "keys": [
                            {
                                "type": "GlobalReference",
                                "value": semantic_id,
                            }
                        ],
                    },
                }
            }
        )

    missing = set(semantic_arguments) - mapped_semantics
    if missing:
        raise ValueError(
            "Operation parameters missing semantic IDs: "
            + ", ".join(sorted(missing))
        )

    # Correlation metadata is transport integration metadata, not a scheduling
    # identity. The deployed delegation adapter accepts these extra arguments.
    for id_short, value in (metadata_arguments or {}).items():
        input_arguments.append(
            {
                "value": {
                    "modelType": "Property",
                    "idShort": id_short,
                    "valueType": "xs:string",
                    "value": str(value),
                }
            }
        )

    return {
        "inputArguments": input_arguments,
        "inoutputArguments": [],
        "requestedTimeout": requested_timeout_ms,
    }


async def invoke_operation(
    binding: OperationBinding,
    semantic_arguments: dict[str, Any],
    *,
    client: httpx.AsyncClient,
    retry_count: int,
    requested_timeout_ms: int,
    metadata_arguments: dict[str, Any] | None = None,
) -> httpx.Response | None:
    url = operation_invoke_url(binding)
    body = build_invocation_payload(
        binding,
        semantic_arguments,
        requested_timeout_ms=requested_timeout_ms,
        metadata_arguments=metadata_arguments,
    )
    attempts = max(1, retry_count)
    for attempt in range(1, attempts + 1):
        try:
            response = await client.post(url, json=body)
            if response.status_code < 500 or attempt == attempts:
                return response
            print(
                f"[ORCHESTRATOR] Invocation HTTP {response.status_code} "
                f"(attempt {attempt}/{attempts})"
            )
        except httpx.HTTPError as exc:
            print(
                f"[ORCHESTRATOR] Invocation failed "
                f"(attempt {attempt}/{attempts}): {exc}"
            )
            if attempt == attempts:
                return None
    return None
