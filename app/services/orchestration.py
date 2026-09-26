import json
import uuid
from typing import Any

import httpx

from app.connectors.mcp import McpConnector
from app.connectors.rest import RestConnector
from app.db import connection, json_dump, now, unpack
from app.services.records import insert_records
from app.services.reports import generate


def source_records(source_ids: list[str] | None) -> list[dict[str, Any]]:
    query = "SELECT * FROM sources"
    values: list[str] = []
    if source_ids:
        query += " WHERE id IN (" + ",".join("?" for _ in source_ids) + ")"
        values = source_ids
    with connection() as con:
        return [unpack(row) for row in con.execute(query, values).fetchall()]


def run_pipeline(
    source_ids: list[str] | None, report_title: str, create_report: bool
) -> dict[str, Any]:
    run_id = str(uuid.uuid4())
    started_at = now()
    events: list[dict[str, Any]] = []
    failed = False
    for source in source_records(source_ids):
        if source["kind"] == "webhook":
            events.append(
                {
                    "agent": "connector",
                    "source_id": source["id"],
                    "status": "skipped",
                    "detail": "Webhook source waits for pushed data",
                }
            )
            continue
        try:
            connector = (
                RestConnector(source["config"])
                if source["kind"] == "rest"
                else McpConnector(source["config"])
            )
            raw = connector.fetch()
            inserted = insert_records(source["id"], raw)
            with connection() as con:
                con.execute(
                    "UPDATE sources SET last_synced_at = ? WHERE id = ?", (now(), source["id"])
                )
            events.extend(
                [
                    {
                        "agent": "connector",
                        "source_id": source["id"],
                        "status": "completed",
                        "records_received": len(raw),
                    },
                    {
                        "agent": "normalizer",
                        "source_id": source["id"],
                        "status": "completed",
                        "records_stored": inserted,
                    },
                ]
            )
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            failed = True
            events.append(
                {
                    "agent": "connector",
                    "source_id": source["id"],
                    "status": "failed",
                    "detail": str(exc),
                }
            )
    report_id = None
    if create_report:
        report = generate(report_title, source_ids, True)
        report_id = report["id"]
        events.extend(
            [
                {
                    "agent": "insight",
                    "status": "completed",
                    "findings": len(report["data"]["insights"]),
                },
                {"agent": "report", "status": "completed", "report_id": report_id},
            ]
        )
    status = "completed_with_errors" if failed else "completed"
    completed_at = now()
    result = {
        "id": run_id,
        "status": status,
        "events": events,
        "report_id": report_id,
        "started_at": started_at,
        "completed_at": completed_at,
    }
    with connection() as con:
        con.execute(
            "INSERT INTO runs (id, status, events_json, report_id, started_at, completed_at) VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, status, json_dump(events), report_id, started_at, completed_at),
        )
    return result


def get_run(run_id: str) -> dict[str, Any] | None:
    with connection() as con:
        row = con.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "status": row["status"],
        "events": json.loads(row["events_json"]),
        "report_id": row["report_id"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
    }
