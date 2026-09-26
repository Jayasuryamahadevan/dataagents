import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import get_settings


def now() -> str:
    return datetime.now(UTC).isoformat()


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    path = Path(get_settings().database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def initialize() -> None:
    with connection() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS sources (
              id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
              config_json TEXT NOT NULL, created_at TEXT NOT NULL, last_synced_at TEXT
            );
            CREATE TABLE IF NOT EXISTS records (
              id TEXT PRIMARY KEY, source_id TEXT NOT NULL, entity_type TEXT NOT NULL,
              occurred_at TEXT, payload_json TEXT NOT NULL, ingested_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS records_source_idx ON records(source_id);
            CREATE INDEX IF NOT EXISTS records_entity_idx ON records(entity_type);
            CREATE TABLE IF NOT EXISTS rules (
              id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
              config_json TEXT NOT NULL, enabled INTEGER NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reports (
              id TEXT PRIMARY KEY, title TEXT NOT NULL, data_json TEXT NOT NULL,
              markdown TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS runs (
              id TEXT PRIMARY KEY, status TEXT NOT NULL, events_json TEXT NOT NULL,
              report_id TEXT, started_at TEXT NOT NULL, completed_at TEXT
            );
            """
        )


def unpack(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    for key in ("config_json", "payload_json", "data_json"):
        if key in item:
            item[key.removesuffix("_json")] = json.loads(item.pop(key))
    return item


def json_dump(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), default=str)
