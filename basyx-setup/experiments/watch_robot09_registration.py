import base64
import json
import time
import urllib.error
import urllib.request


AAS_ID = "urn:agent-aas:aas:robot09"

SUBMODEL_IDS = [
    "urn:agent-aas:robot09:capability-description",
    "urn:agent-aas:robot09:control-component-instance",
    "urn:agent-aas:robot09:digital-nameplate",
    "urn:agent-aas:robot09:operational-state",
    "urn:agent-aas:robot09:reachability",
    "urn:agent-aas:robot09:skill-execution",
    "urn:agent-aas:robot09:technical-data",
]


def encode_identifier(identifier: str) -> str:
    encoded = base64.urlsafe_b64encode(
        identifier.encode("utf-8")
    ).decode("ascii")

    return encoded.rstrip("=")


def endpoint_exists(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return response.status == 200
    except (
        urllib.error.HTTPError,
        urllib.error.URLError,
        TimeoutError,
    ):
        return False


aas_descriptor_url = (
    "http://localhost:8082/shell-descriptors/"
    + encode_identifier(AAS_ID)
)

submodel_descriptor_urls = [
    "http://localhost:8083/submodel-descriptors/"
    + encode_identifier(submodel_id)
    for submodel_id in SUBMODEL_IDS
]

print("Waiting for Robot09 registration...")

while True:
    aas_visible = endpoint_exists(aas_descriptor_url)

    submodels_visible = all(
        endpoint_exists(url)
        for url in submodel_descriptor_urls
    )

    if aas_visible and submodels_visible:
        t_registered_unix_us = time.time_ns() // 1_000

        print(
            json.dumps(
                {
                    "event": "resource_registered",
                    "global_asset_id": (
                        "urn:agent-aas:asset-instance:robot09"
                    ),
                    "aas_id": AAS_ID,
                    "registered_unix_us": (
                        t_registered_unix_us
                    ),
                }
            ),
            flush=True,
        )
        break

    time.sleep(0.05)