import json
from typing import Any

import httpx

from app.config import get_settings
from app.connectors.base import Connector


class McpConnector(Connector):
    def __init__(self, config: dict[str, Any]):
        self.config = config

    def fetch(self) -> list[dict[str, Any]]:
        payload = {
            "jsonrpc": "2.0",
            "id": "dataagents-sync",
            "method": "tools/call",
            "params": {"name": self.config["tool"], "arguments": self.config.get("arguments", {})},
        }
        response = httpx.post(
            self.config["url"],
            headers={"Accept": "application/json", **self.config.get("headers", {})},
            json=payload,
            timeout=get_settings().request_timeout_seconds,
        )
        response.raise_for_status()
        body = response.json()
        if body.get("error"):
            raise ValueError(f"MCP server error: {body['error']}")
        content = body.get("result", {}).get("content", [])
        records: list[dict[str, Any]] = []
        for item in content:
            if item.get("type") == "resource" and isinstance(
                item.get("resource", {}).get("json"), list
            ):
                records.extend(item["resource"]["json"])
            if item.get("type") == "text":
                parsed = json.loads(item["text"])
                if isinstance(parsed, list):
                    records.extend(parsed)
        if not all(isinstance(item, dict) for item in records):
            raise ValueError("MCP tool must return records as JSON objects")
        return records[: get_settings().max_records_per_sync]
