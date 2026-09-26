import uuid
from typing import Any

import httpx

from app.connectors.mcp import McpConnector
from app.connectors.rest import RestConnector
from app.db import connection, json_dump, now, unpack
from app.services.records import insert_records
from app.services.reports import generate

TOOL_CATALOG = {
    "source.sync": {
        "name": "source.sync",
        "description": "Read normalized records from a permitted REST or MCP source. Never writes to the source system.",
        "input_schema": {
            "type": "object",
            "required": ["source_id"],
            "properties": {"source_id": {"type": "string", "description": "Registered source ID"}},
            "additionalProperties": False,
        },
        "read_only": True,
        "source_scoped": True,
    },
    "report.generate": {
        "name": "report.generate",
        "description": "Evaluate deterministic rules and create an immutable Markdown and JSON report.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "source_ids": {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": False,
        },
        "read_only": False,
        "source_scoped": False,
    },
}


def source_by_id(source_id: str) -> dict[str, Any] | None:
    with connection() as con:
        row = con.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
    return unpack(row) if row else None


def sync_source(source_id: str) -> dict[str, Any]:
    source = source_by_id(source_id)
    if not source:
        raise ValueError("Source not found")
    if source["kind"] == "webhook":
        return {
            "source_id": source_id,
            "status": "skipped",
            "detail": "Webhook source waits for pushed data",
        }
    connector = (
        RestConnector(source["config"])
        if source["kind"] == "rest"
        else McpConnector(source["config"])
    )
    raw = connector.fetch()
    inserted = insert_records(source_id, raw)
    with connection() as con:
        con.execute("UPDATE sources SET last_synced_at = ? WHERE id = ?", (now(), source_id))
    return {
        "source_id": source_id,
        "status": "completed",
        "records_received": len(raw),
        "records_stored": inserted,
    }


def log_tool_call(
    agent_run_id: str,
    tool_name: str,
    tool_input: dict[str, Any],
    result: dict[str, Any],
    status: str,
) -> None:
    source_id = tool_input.get("source_id")
    completed = now()
    with connection() as con:
        con.execute(
            "INSERT INTO tool_calls (id, agent_run_id, tool_name, source_id, status, input_json, output_json, started_at, completed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                agent_run_id,
                tool_name,
                source_id,
                status,
                json_dump(tool_input),
                json_dump(result),
                completed,
                completed,
            ),
        )


class AgentRuntime:
    def __init__(self, allowed_tools: list[str], source_ids: list[str] | None):
        self.allowed_tools = set(allowed_tools)
        self.source_ids = source_ids

    def plan(self, report_title: str) -> list[dict[str, Any]]:
        with connection() as con:
            rows = con.execute("SELECT id, kind FROM sources").fetchall()
        allowed_sources = set(self.source_ids) if self.source_ids else {row["id"] for row in rows}
        steps = [
            {"tool_name": "source.sync", "arguments": {"source_id": row["id"]}}
            for row in rows
            if row["id"] in allowed_sources and row["kind"] in {"rest", "mcp"}
        ]
        if "report.generate" in self.allowed_tools:
            steps.append(
                {
                    "tool_name": "report.generate",
                    "arguments": {"title": report_title, "source_ids": self.source_ids},
                }
            )
        return steps

    def authorize(self, tool_name: str, arguments: dict[str, Any]) -> None:
        if tool_name not in TOOL_CATALOG:
            raise ValueError("Unknown tool")
        if tool_name not in self.allowed_tools:
            raise PermissionError(f"Tool {tool_name} is not allowed for this run")
        schema = TOOL_CATALOG[tool_name]["input_schema"]
        missing = [key for key in schema.get("required", []) if key not in arguments]
        if missing:
            raise ValueError(f"Missing required tool arguments: {', '.join(missing)}")
        unexpected = set(arguments) - set(schema.get("properties", {}))
        if unexpected:
            raise ValueError(f"Unexpected tool arguments: {', '.join(sorted(unexpected))}")
        source_id = arguments.get("source_id")
        if source_id and self.source_ids is not None and source_id not in self.source_ids:
            raise PermissionError("Source is outside the agent's allowed scope")

    def invoke(
        self, agent_run_id: str, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            self.authorize(tool_name, arguments)
            if tool_name == "source.sync":
                result = sync_source(arguments["source_id"])
            else:
                report = generate(
                    arguments.get("title", "Agent operations report"),
                    arguments.get("source_ids"),
                    True,
                )
                result = {
                    "status": "completed",
                    "report_id": report["id"],
                    "findings": len(report["data"]["insights"]),
                }
            log_tool_call(
                agent_run_id, tool_name, arguments, result, result.get("status", "completed")
            )
            return result
        except (httpx.HTTPError, KeyError, PermissionError, TypeError, ValueError) as exc:
            result = {"status": "failed", "detail": str(exc)}
            log_tool_call(agent_run_id, tool_name, arguments, result, "failed")
            return result

    def run(self, objective: str, report_title: str) -> dict[str, Any]:
        run_id, started = str(uuid.uuid4()), now()
        plan = self.plan(report_title)
        if not plan:
            completed = now()
            result = {
                "id": run_id,
                "objective": objective,
                "status": "blocked",
                "plan": [],
                "events": [
                    {
                        "agent": "planner",
                        "status": "blocked",
                        "detail": "No permitted tools or sources are available",
                    }
                ],
                "report_id": None,
                "started_at": started,
                "completed_at": completed,
            }
            self.persist(result)
            return result
        events: list[dict[str, Any]] = []
        report_id = None
        failed = False
        for step in plan:
            result = self.invoke(run_id, step["tool_name"], step["arguments"])
            failed = failed or result["status"] == "failed"
            if result.get("report_id"):
                report_id = result["report_id"]
            events.append({"agent": "tool-runtime", "tool_name": step["tool_name"], **result})
        completed = now()
        result = {
            "id": run_id,
            "objective": objective,
            "status": "completed_with_errors" if failed else "completed",
            "plan": plan,
            "events": events,
            "report_id": report_id,
            "started_at": started,
            "completed_at": completed,
        }
        self.persist(result)
        return result

    def persist(self, result: dict[str, Any]) -> None:
        with connection() as con:
            con.execute(
                "INSERT INTO agent_runs (id, objective, status, plan_json, result_json, started_at, completed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    result["id"],
                    result["objective"],
                    result["status"],
                    json_dump(result["plan"]),
                    json_dump(result),
                    result["started_at"],
                    result["completed_at"],
                ),
            )
            con.execute(
                "INSERT INTO runs (id, status, events_json, report_id, started_at, completed_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    result["id"],
                    result["status"],
                    json_dump(result["events"]),
                    result["report_id"],
                    result["started_at"],
                    result["completed_at"],
                ),
            )
