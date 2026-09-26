import json
import uuid
from datetime import UTC, datetime
from typing import Any

from app.db import connection, json_dump
from app.services.insights import evaluate_rule, summarize
from app.services.records import query_records


def generate(title: str, source_ids: list[str] | None, include_insights: bool) -> dict[str, Any]:
    records = query_records(source_ids)
    with connection() as con:
        rules = [
            dict(row) for row in con.execute("SELECT * FROM rules WHERE enabled = 1").fetchall()
        ]
        source_rows = con.execute("SELECT id, last_synced_at FROM sources").fetchall()
    for rule in rules:
        rule["config"] = json.loads(rule.pop("config_json"))
    syncs = {row["id"]: row["last_synced_at"] for row in source_rows}
    insights = (
        [item for rule in rules if (item := evaluate_rule(rule, records, syncs))]
        if include_insights
        else []
    )
    data = {
        "summary": summarize(records),
        "insights": insights,
        "generated_at": datetime.now(UTC).isoformat(),
    }
    lines = [
        f"# {title}",
        "",
        f"Records analysed: {data['summary']['record_count']}",
        "",
        "## Entity summary",
    ]
    lines += [
        f"- {kind}: {count} records, amount {data['summary']['amount_by_entity_type'].get(kind, 0):,.2f}"
        for kind, count in data["summary"]["by_entity_type"].items()
    ]
    lines += ["", "## Findings"]
    lines += [
        f"- [{item['severity'].upper()}] {item['title']}: {item['detail']}" for item in insights
    ] or ["- No active rule violations."]
    report = {
        "id": str(uuid.uuid4()),
        "title": title,
        "created_at": datetime.now(UTC).isoformat(),
        "data": data,
        "markdown": "\n".join(lines),
    }
    with connection() as con:
        con.execute(
            "INSERT INTO reports (id, title, data_json, markdown, created_at) VALUES (?, ?, ?, ?, ?)",
            (report["id"], title, json_dump(data), report["markdown"], report["created_at"]),
        )
    return report
