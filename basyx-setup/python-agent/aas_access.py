from typing import Optional

import httpx

from config_models import parse_bool_value


async def fetch_supported_capabilities(client: httpx.AsyncClient, skills_url: str, robot_id: str) -> Optional[list]:
    try:
        response = await client.get(
            f"{skills_url}/submodel-elements/SupportedCapabilities"
        )
        if response.status_code != 200:
            print(
                f"[ORCHESTRATOR] Robot skills submodel {robot_id} has no "
                f"SupportedCapabilities (HTTP {response.status_code})"
            )
            return None
        routes = response.json().get("value", [])
        if not isinstance(routes, list):
            print(
                f"[ORCHESTRATOR] Robot {robot_id} SupportedCapabilities "
                "is not a collection"
            )
            return None
        return routes
    except Exception as exc:
        print(
            f"[ORCHESTRATOR] Could not fetch capabilities for robot "
            f"{robot_id}: {exc}"
        )
        return None


async def read_robot_bool_state(client: httpx.AsyncClient, state_url: str, property_id: str) -> Optional[bool]:
    try:
        response = await client.get(
            f"{state_url}/submodel-elements/{property_id}"
        )
        if response.status_code != 200:
            print(
                f"[ORCHESTRATOR] Robot state {property_id} read returned "
                f"HTTP {response.status_code}"
            )
            return None

        value = parse_bool_value(response.text)
        if value is None:
            print(
                f"[ORCHESTRATOR] Invalid {property_id} value: "
                f"{response.text!r}"
            )
        return value
    except Exception as exc:
        print(
            f"[ORCHESTRATOR] Error reading robot state {property_id}: {exc}"
        )
        return None


async def invoke_operation(
    client: httpx.AsyncClient,
    invoke_url: str,
    body: dict,
    retry_count: int,
):
    response = None
    attempts = max(1, retry_count)
    for attempt in range(1, attempts + 1):
        try:
            response = await client.post(invoke_url, json=body)
            if response.status_code < 500:
                break
            print(
                f"[ORCHESTRATOR] Robot invocation returned "
                f"HTTP {response.status_code} "
                f"(attempt {attempt}/{attempts})"
            )
            if attempt < attempts:
                response = None
        except httpx.HTTPError as exc:
            response = None
            print(
                f"[ORCHESTRATOR] Robot invocation failed "
                f"(attempt {attempt}/{attempts}): {exc}"
            )
    return response
