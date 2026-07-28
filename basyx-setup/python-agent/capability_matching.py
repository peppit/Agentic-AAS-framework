from typing import Optional

from config_models import normalize_station_id


def parse_capability_route(route: object) -> Optional[dict]:
    if not isinstance(route, dict):
        return None

    route_values = route.get("value", [])
    if not isinstance(route_values, list):
        return None

    properties = {
        element["idShort"]: element
        for element in route_values
        if isinstance(element, dict) and element.get("idShort")
    }
    return {
        "route_id": str(route.get("idShort") or "").strip(),
        "StationId": str(
            properties.get("StationId", {}).get("value") or ""
        ).strip(),
        "TriggerSensor": str(
            properties.get("TriggerSensor", {}).get("value") or ""
        ).strip(),
        "TargetOperation": str(
            properties.get("TargetOperation", {}).get("value") or ""
        ).strip(),
        "SourcePosition": str(
            properties.get("SourcePosition", {}).get("value") or ""
        ).strip(),
        "TargetPosition": str(
            properties.get("TargetPosition", {}).get("value") or ""
        ).strip(),
        "properties": properties,
    }


def build_operation_inputs(selected_route: dict) -> dict[str, dict]:
    if selected_route["TargetOperation"] == "ExecuteMoveBox":
        return {
            id_short: {
                "value": selected_route[id_short],
                "valueType": selected_route["properties"]
                .get(id_short, {})
                .get("valueType", "xs:string"),
            }
            for id_short in ("StationId", "SourcePosition", "TargetPosition")
        }

    # Fixed-home and other operations retain their advertised input contract.
    # StationId selects the route but is not itself an operation argument.
    return {
        id_short: {
            "value": element.get("value"),
            "valueType": element.get("valueType", "xs:string"),
        }
        for id_short, element in selected_route["properties"].items()
        if id_short not in {"StationId", "TriggerSensor", "TargetOperation"}
    }


def match_capability_route(
    routes: list,
    *,
    robot_id: str,
    station_id: str,
    triggering_sensor: str,
    required_operation: str = "",
) -> Optional[dict]:
    normalized_station_id = normalize_station_id(station_id)
    for route in routes:
        parsed_route = parse_capability_route(route)
        if parsed_route is None:
            print(
                f"[ORCHESTRATOR] Rejected malformed capability route "
                f"on robot {robot_id}"
            )
            continue

        route_id = parsed_route["route_id"] or "<unnamed>"
        route_station_id = parsed_route["StationId"]
        print(
            f"[ORCHESTRATOR] Robot {robot_id} route={route_id} "
            f"advertised_station={route_station_id or 'missing'} "
            f"requested_station={station_id or 'missing'} "
            f"source={parsed_route['SourcePosition'] or 'missing'} "
            f"target={parsed_route['TargetPosition'] or 'missing'} "
            f"operation={parsed_route['TargetOperation'] or 'missing'}"
        )

        rejection = _route_rejection(
            parsed_route,
            station_id=station_id,
            normalized_station_id=normalized_station_id,
            triggering_sensor=triggering_sensor,
            required_operation=required_operation,
        )
        if rejection:
            print(
                f"[ORCHESTRATOR] Rejected route {route_id} "
                f"on robot {robot_id}: {rejection}"
            )
            continue
        return parsed_route
    return None


def _route_rejection(
    route: dict,
    *,
    station_id: str,
    normalized_station_id: str,
    triggering_sensor: str,
    required_operation: str,
) -> str:
    route_station_id = route["StationId"]
    if not route_station_id:
        return "StationId is missing or blank"
    if normalize_station_id(route_station_id) != normalized_station_id:
        return (
            f"station {route_station_id} does not match requested "
            f"{station_id or 'missing'}"
        )
    if route["TriggerSensor"] != triggering_sensor:
        return (
            f"sensor {route['TriggerSensor'] or 'missing'} "
            f"does not match {triggering_sensor}"
        )
    target_operation = route["TargetOperation"]
    if not target_operation:
        return "TargetOperation is missing"
    if required_operation and target_operation != required_operation:
        return (
            f"operation {target_operation} "
            f"does not match required {required_operation}"
        )
    if target_operation == "ExecuteMoveBox" and (
        not route["SourcePosition"] or not route["TargetPosition"]
    ):
        return "ExecuteMoveBox requires SourcePosition and TargetPosition"
    return ""
