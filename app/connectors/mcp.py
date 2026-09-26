import json
import uuid
from typing import Any

import httpx

from app.config import get_settings
from app.connectors.base import Connector


class McpConnector(Connector):
    def __init__(self, config: dict[str, Any]):
        self.config = config

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=get_settings().request_timeout_seconds)

    def _request(
        self, client: httpx.Client, method: str, params: dict[str, Any], notification: bool = False
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notification:
            payload["id"] = str(uuid.uuid4())
        response = client.post(
            self.config["url"],
            headers={
                "Accept": "application/json, text/event-stream",
                **self.config.get("headers", {}),
            },
            json=payload,
        )
        response.raise_for_status()
        if notification:
            return {}
        session_id = response.headers.get("Mcp-Session-Id")
        if session_id:
            client.headers["Mcp-Session-Id"] = session_id
        body = response.json()
        if body.get("error"):
            raise ValueError(f"MCP server error: {body['error']}")
        return body.get("result", {})

    def _initialize(self, client: httpx.Client) -> None:
        self._request(
            client,
            "initialize",
            {
                "protocolVersion": self.config.get("protocol_version", "2025-03-26"),
                "capabilities": {},
                "clientInfo": {"name": "dataagents", "version": "0.2.0"},
            },
        )
        self._request(client, "notifications/initialized", {}, notification=True)

    def discover_tools(self) -> list[dict[str, Any]]:
        with self._client() as client:
            self._initialize(client)
            tools = self._request(client, "tools/list", {}).get("tools", [])
        if not isinstance(tools, list):
            raise TypeError("MCP tools/list response did not contain a tools array")
        return tools

    def fetch(self) -> list[dict[str, Any]]:
        configured_tool = self.config["tool"]
        if configured_tool not in self.config.get("allowed_tools", [configured_tool]):
            raise ValueError("Configured MCP tool is not in allowed_tools")
        with self._client() as client:
            self._initialize(client)
            result = self._request(
                client,
                "tools/call",
                {"name": configured_tool, "arguments": self.config.get("arguments", {})},
            )
        content = result.get("content", [])
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
