from typing import Any

import httpx

from app.config import get_settings
from app.connectors.base import Connector


def select_path(value: Any, path: str | None) -> Any:
    if not path:
        return value
    current = value
    for part in path.split("."):
        current = current[int(part)] if isinstance(current, list) else current[part]
    return current


class RestConnector(Connector):
    def __init__(self, config: dict[str, Any]):
        self.config = config

    def fetch(self) -> list[dict[str, Any]]:
        response = httpx.request(
            method=self.config.get("method", "GET"),
            url=self.config["url"],
            headers=self.config.get("headers", {}),
            params=self.config.get("params", {}),
            timeout=get_settings().request_timeout_seconds,
        )
        response.raise_for_status()
        records = select_path(response.json(), self.config.get("records_path"))
        if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
            raise ValueError("REST response must resolve to a list of JSON objects")
        return records[: get_settings().max_records_per_sync]
