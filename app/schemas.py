from datetime import datetime
from typing import Any, Literal

from pydantic import AnyHttpUrl, BaseModel, Field, model_validator

SourceKind = Literal["rest", "mcp", "webhook"]
RuleKind = Literal["threshold", "reconciliation", "freshness"]


class SourceCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    kind: SourceKind
    config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_connection(self):
        if self.kind in {"rest", "mcp"} and not self.config.get("url"):
            raise ValueError("config.url is required for REST and MCP sources")
        return self


class Source(SourceCreate):
    id: str
    created_at: datetime
    last_synced_at: datetime | None = None


class RestSourceConfig(BaseModel):
    url: AnyHttpUrl
    method: Literal["GET", "POST"] = "GET"
    headers: dict[str, str] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)
    records_path: str | None = None


class McpSourceConfig(BaseModel):
    url: AnyHttpUrl
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)


class RuleCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    kind: RuleKind
    config: dict[str, Any]
    enabled: bool = True


class Rule(RuleCreate):
    id: str
    created_at: datetime


class Insight(BaseModel):
    rule_id: str
    rule_name: str
    severity: Literal["info", "warning", "critical"]
    title: str
    detail: str
    metrics: dict[str, Any]


class IngestRequest(BaseModel):
    records: list[dict[str, Any]] = Field(min_length=1, max_length=10_000)


class ReportRequest(BaseModel):
    title: str = "Operations Insight Report"
    source_ids: list[str] | None = None
    include_insights: bool = True


class Report(BaseModel):
    id: str
    title: str
    created_at: datetime
    data: dict[str, Any]
    markdown: str


class PipelineRunRequest(BaseModel):
    source_ids: list[str] | None = None
    report_title: str = "Operations Insight Report"
    create_report: bool = True


class PipelineRun(BaseModel):
    id: str
    status: Literal["completed", "completed_with_errors"]
    events: list[dict[str, Any]]
    report_id: str | None = None
    started_at: datetime
    completed_at: datetime


class AgentRunRequest(BaseModel):
    objective: str = Field(
        default="Refresh permitted sources and generate an operations report",
        min_length=8,
        max_length=300,
    )
    source_ids: list[str] | None = None
    report_title: str = "Agent operations report"
    allowed_tools: list[Literal["source.sync", "report.generate"]] = Field(
        default_factory=lambda: ["source.sync", "report.generate"]
    )


class ToolDescriptor(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]
    read_only: bool
    source_scoped: bool


class AgentRun(BaseModel):
    id: str
    objective: str
    status: Literal["completed", "completed_with_errors", "blocked"]
    plan: list[dict[str, Any]]
    events: list[dict[str, Any]]
    report_id: str | None = None
    started_at: datetime
    completed_at: datetime
