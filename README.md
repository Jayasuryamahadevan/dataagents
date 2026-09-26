# DataAgents

DataAgents is a script-first orchestration service for operational data. It connects REST APIs, MCP tools, and push/webhook feeds, normalizes ERP-shaped records, runs deterministic rules, and produces auditable reports without requiring an LLM.

## Design

- Connector adapters: generic REST and MCP `tools/call`; webhook sources ingest directly.
- Canonical records: invoices, orders, payments, inventory, and generic events use a shared shape.
- Deterministic insights: threshold, reconciliation, and freshness rules are plain configuration.
- Cloud ready: FastAPI, SQLite volume by default, Docker image, stateless API process apart from the database volume.
- AI optional: external AI can consume the generated report later, but it is not in the control path.

## Run locally

```bash
cp .env.example .env
docker compose up --build
```

The API will be available at `http://localhost:8080`; interactive API docs are at `/docs`.

## Example workflow

Create a source first. A webhook source is the fastest way to test; use a REST source for ERPNext, Odoo, SAP, Oracle, Dynamics, NetSuite, or an internal service when it exposes an API. For an MCP source, provide its streamable HTTP endpoint and the read-only tool that returns records.

```bash
export API_KEY=change-this-before-production

curl -X POST http://localhost:8080/v1/sources \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"name":"ERPNext production","kind":"webhook","config":{}}'

curl -X POST http://localhost:8080/v1/rules \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"name":"Invoice exposure","kind":"threshold","config":{"field":"amount","entity_type":"invoice","operator":">","value":500000,"severity":"warning"}}'
```

Use the returned source ID to ingest ERP-shaped records:

```bash
curl -X POST http://localhost:8080/v1/sources/SOURCE_ID/ingest \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"records":[{"doctype":"Sales Invoice","name":"SINV-1001","grand_total":550000,"posting_date":"2026-09-26"}]}'

curl -X POST http://localhost:8080/v1/reports \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"title":"Daily finance report"}'
```

Or run all four deterministic agents as one auditable pipeline. REST and MCP sources are fetched; webhook sources are retained as already-ingested records; the pipeline then evaluates rules and writes a report.

```bash
curl -X POST http://localhost:8080/v1/runs \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"report_title":"Daily operating report"}'
```

## Source configuration

REST source:

```json
{
  "name": "ERP invoices",
  "kind": "rest",
  "config": {
    "url": "https://erp.example.com/api/invoices",
    "method": "GET",
    "headers": {"Authorization": "Bearer use-a-secret-manager-in-production"},
    "params": {"modified_since": "2026-09-25"},
    "records_path": "data.items"
  }
}
```

MCP source:

```json
{
  "name": "Warehouse MCP",
  "kind": "mcp",
  "config": {
    "url": "https://mcp.example.com/mcp",
    "tool": "list_inventory",
    "arguments": {"warehouse": "CBE-01"},
    "headers": {"Authorization": "Bearer use-a-secret-manager-in-production"}
  }
}
```

## Production notes

- Put the API behind HTTPS and set `DATAAGENTS_API_KEY` to a high-entropy secret.
- Store connector credentials in a cloud secret manager and inject them at runtime. Do not put live credentials in source configurations or Git.
- Replace SQLite with PostgreSQL before horizontal scaling. Keep connector execution in a queue worker when sync volumes grow.
- Use read-only ERP credentials and allowlist outbound domains.
- Build once with `docker build -t dataagents .` and deploy that image to Cloud Run, AWS ECS/Fargate, Azure Container Apps, Render, Railway, or a VM. Mount persistent storage only for the SQLite development configuration.
