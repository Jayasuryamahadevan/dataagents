import json
import uuid
from typing import Any

from app.db import connection, json_dump, now
from app.services.normalizer import normalize


def insert_records(source_id: str, raw_records: list[dict[str, Any]]) -> int:
    normalized = [normalize(record, source_id) for record in raw_records]
    with connection() as con:
        con.executemany(
            "INSERT INTO records (id, source_id, entity_type, occurred_at, payload_json, ingested_at) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    str(uuid.uuid4()),
                    source_id,
                    record["entity_type"],
                    record["occurred_at"],
                    json_dump(record),
                    now(),
                )
                for record in normalized
            ],
        )
    return len(normalized)


def query_records(source_ids: list[str] | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM records"
    params: list[Any] = []
    if source_ids:
        sql += " WHERE source_id IN (" + ",".join("?" for _ in source_ids) + ")"
        params = source_ids
    sql += " ORDER BY occurred_at DESC"
    with connection() as con:
        rows = con.execute(sql, params).fetchall()
    return [json.loads(row["payload_json"]) for row in rows]
