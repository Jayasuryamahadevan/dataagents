import json
import uuid
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.connectors.mcp import McpConnector
from app.connectors.rest import RestConnector
from app.db import connection, initialize, json_dump, now, unpack
from app.schemas import (
    IngestRequest,
    PipelineRun,
    PipelineRunRequest,
    Report,
    ReportRequest,
    Rule,
    RuleCreate,
    Source,
    SourceCreate,
)
from app.services.orchestration import get_run, run_pipeline
from app.services.records import insert_records
from app.services.reports import generate

app = FastAPI(
    title="DataAgents",
    version="0.1.0",
    description="Script-first multi-source business insight service",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup() -> None:
    initialize()


def authorized(x_api_key: Annotated[str | None, Header()] = None) -> None:
    expected = get_settings().api_key
    if expected and x_api_key != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")


Auth = Depends(authorized)


@app.get("/health")
def health():
    return {"status": "ok", "service": "dataagents", "ai_required": False}


@app.post("/v1/sources", response_model=Source, dependencies=[Auth])
def create_source(payload: SourceCreate):
    source_id, created = str(uuid.uuid4()), now()
    try:
        with connection() as con:
            con.execute(
                "INSERT INTO sources (id, name, kind, config_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (source_id, payload.name, payload.kind, json_dump(payload.config), created),
            )
    except Exception as exc:
        raise HTTPException(409, "Source name already exists") from exc
    return {**payload.model_dump(), "id": source_id, "created_at": created, "last_synced_at": None}


@app.get("/v1/sources", response_model=list[Source], dependencies=[Auth])
def list_sources():
    with connection() as con:
        return [
            unpack(row)
            for row in con.execute("SELECT * FROM sources ORDER BY created_at DESC").fetchall()
        ]


@app.post("/v1/sources/{source_id}/ingest", dependencies=[Auth])
def ingest(source_id: str, payload: IngestRequest):
    with connection() as con:
        if not con.execute("SELECT 1 FROM sources WHERE id = ?", (source_id,)).fetchone():
            raise HTTPException(404, "Source not found")
    inserted = insert_records(source_id, payload.records)
    with connection() as con:
        con.execute("UPDATE sources SET last_synced_at = ? WHERE id = ?", (now(), source_id))
    return {"source_id": source_id, "inserted": inserted}


@app.post("/v1/sources/{source_id}/sync", dependencies=[Auth])
def sync(source_id: str):
    with connection() as con:
        row = con.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Source not found")
    source = unpack(row)
    if source["kind"] == "webhook":
        raise HTTPException(400, "Webhook sources receive data through /ingest")
    try:
        connector = (
            RestConnector(source["config"])
            if source["kind"] == "rest"
            else McpConnector(source["config"])
        )
        inserted = insert_records(source_id, connector.fetch())
        with connection() as con:
            con.execute("UPDATE sources SET last_synced_at = ? WHERE id = ?", (now(), source_id))
        return {"source_id": source_id, "inserted": inserted}
    except Exception as exc:
        raise HTTPException(502, f"Source sync failed: {exc}") from exc


@app.post("/v1/rules", response_model=Rule, dependencies=[Auth])
def create_rule(payload: RuleCreate):
    rule_id, created = str(uuid.uuid4()), now()
    try:
        with connection() as con:
            con.execute(
                "INSERT INTO rules (id, name, kind, config_json, enabled, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    rule_id,
                    payload.name,
                    payload.kind,
                    json_dump(payload.config),
                    int(payload.enabled),
                    created,
                ),
            )
    except Exception as exc:
        raise HTTPException(409, "Rule name already exists") from exc
    return {**payload.model_dump(), "id": rule_id, "created_at": created}


@app.get("/v1/rules", response_model=list[Rule], dependencies=[Auth])
def list_rules():
    with connection() as con:
        return [
            unpack(row)
            for row in con.execute("SELECT * FROM rules ORDER BY created_at DESC").fetchall()
        ]


@app.post("/v1/reports", response_model=Report, dependencies=[Auth])
def create_report(payload: ReportRequest):
    return generate(payload.title, payload.source_ids, payload.include_insights)


@app.post("/v1/runs", response_model=PipelineRun, dependencies=[Auth])
def create_pipeline_run(payload: PipelineRunRequest):
    return run_pipeline(payload.source_ids, payload.report_title, payload.create_report)


@app.get("/v1/runs/{run_id}", response_model=PipelineRun, dependencies=[Auth])
def read_pipeline_run(run_id: str):
    result = get_run(run_id)
    if not result:
        raise HTTPException(404, "Pipeline run not found")
    return result


@app.get("/v1/reports/{report_id}", response_model=Report, dependencies=[Auth])
def get_report(report_id: str):
    with connection() as con:
        row = con.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Report not found")
    report = dict(row)
    return {
        "id": report["id"],
        "title": report["title"],
        "created_at": report["created_at"],
        "data": json.loads(report["data_json"]),
        "markdown": report["markdown"],
    }
