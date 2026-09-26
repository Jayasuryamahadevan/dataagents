import json
import uuid
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.config import get_settings
from app.connectors.mcp import McpConnector
from app.db import connection, initialize, json_dump, now, unpack
from app.schemas import (
    AgentRun,
    AgentRunRequest,
    IngestRequest,
    PipelineRun,
    PipelineRunRequest,
    Report,
    ReportRequest,
    Rule,
    RuleCreate,
    Source,
    SourceCreate,
    ToolDescriptor,
)
from app.services.agent_runtime import TOOL_CATALOG, AgentRuntime, source_by_id, sync_source
from app.services.insights import summarize
from app.services.orchestration import get_run, run_pipeline
from app.services.records import insert_records, query_records
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

STATIC_DIRECTORY = Path(__file__).parent / "static"


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


@app.get("/", include_in_schema=False)
def dashboard_page():
    return FileResponse(STATIC_DIRECTORY / "index.html")


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
    try:
        result = sync_source(source_id)
        if result["status"] == "skipped":
            raise HTTPException(400, result["detail"])
        return {"source_id": source_id, "inserted": result["records_stored"]}
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(502, f"Source sync failed: {exc}") from exc


@app.get("/v1/tools", response_model=list[ToolDescriptor], dependencies=[Auth])
def list_tools():
    return list(TOOL_CATALOG.values())


@app.post("/v1/sources/{source_id}/discover-tools", dependencies=[Auth])
def discover_source_tools(source_id: str):
    source = source_by_id(source_id)
    if not source:
        raise HTTPException(404, "Source not found")
    if source["kind"] != "mcp":
        raise HTTPException(400, "Tool discovery is available only for MCP sources")
    try:
        tools = McpConnector(source["config"]).discover_tools()
        return {
            "source_id": source_id,
            "allowed_tools": source["config"].get("allowed_tools", []),
            "discovered_tools": tools,
        }
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(502, f"MCP tool discovery failed: {exc}") from exc


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


@app.get("/v1/dashboard", dependencies=[Auth])
def dashboard_data():
    with connection() as con:
        sources = [
            unpack(row)
            for row in con.execute(
                "SELECT * FROM sources ORDER BY last_synced_at DESC, created_at DESC"
            ).fetchall()
        ]
        rules = [
            unpack(row)
            for row in con.execute("SELECT * FROM rules ORDER BY created_at DESC").fetchall()
        ]
        report_rows = con.execute(
            "SELECT id, title, created_at, data_json FROM reports ORDER BY created_at DESC LIMIT 6"
        ).fetchall()
        run_rows = con.execute(
            "SELECT id, status, events_json, report_id, started_at, completed_at FROM runs ORDER BY started_at DESC LIMIT 4"
        ).fetchall()
    records = query_records()
    reports = [
        {
            "id": row["id"],
            "title": row["title"],
            "created_at": row["created_at"],
            "summary": json.loads(row["data_json"])["summary"],
            "insight_count": len(json.loads(row["data_json"]).get("insights", [])),
        }
        for row in report_rows
    ]
    runs = [
        {
            "id": row["id"],
            "status": row["status"],
            "events": json.loads(row["events_json"]),
            "report_id": row["report_id"],
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
        }
        for row in run_rows
    ]
    return {
        "summary": summarize(records),
        "sources": sources,
        "rules": rules,
        "reports": reports,
        "runs": runs,
        "generated_at": now(),
    }


@app.post("/v1/reports", response_model=Report, dependencies=[Auth])
def create_report(payload: ReportRequest):
    return generate(payload.title, payload.source_ids, payload.include_insights)


@app.post("/v1/runs", response_model=PipelineRun, dependencies=[Auth])
def create_pipeline_run(payload: PipelineRunRequest):
    return run_pipeline(payload.source_ids, payload.report_title, payload.create_report)


@app.post("/v1/agent/runs", response_model=AgentRun, dependencies=[Auth])
def create_agent_run(payload: AgentRunRequest):
    return AgentRuntime(payload.allowed_tools, payload.source_ids).run(
        payload.objective, payload.report_title
    )


@app.get("/v1/agent/runs/{agent_run_id}", response_model=AgentRun, dependencies=[Auth])
def get_agent_run(agent_run_id: str):
    with connection() as con:
        row = con.execute(
            "SELECT result_json FROM agent_runs WHERE id = ?", (agent_run_id,)
        ).fetchone()
    if not row:
        raise HTTPException(404, "Agent run not found")
    return json.loads(row["result_json"])


@app.get("/v1/agent/runs/{agent_run_id}/tool-calls", dependencies=[Auth])
def get_agent_tool_calls(agent_run_id: str):
    with connection() as con:
        rows = con.execute(
            "SELECT id, tool_name, source_id, status, input_json, output_json, started_at, completed_at FROM tool_calls WHERE agent_run_id = ? ORDER BY started_at",
            (agent_run_id,),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "tool_name": row["tool_name"],
            "source_id": row["source_id"],
            "status": row["status"],
            "input": json.loads(row["input_json"]),
            "output": json.loads(row["output_json"]),
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
        }
        for row in rows
    ]


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
