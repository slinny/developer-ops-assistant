from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, str_strip_whitespace=True, allow_inf_nan=False
    )


Repository = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")]


class SearchArgs(Strict):
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=5, ge=1, le=10)


class TaskArgs(Strict):
    state: Literal["open", "closed", "all"] = "open"
    query: str = Field(default="", max_length=200)
    offset: int = Field(default=0, ge=0, le=100000)
    limit: int = Field(default=20, ge=1, le=50)


class PRArgs(Strict):
    number: int = Field(ge=1)
    page: int = Field(default=1, ge=1, le=100)


class CreateArgs(Strict):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(min_length=1, max_length=4000)
    idempotency_key: str = Field(min_length=1, max_length=100)


class Evidence(Strict):
    id: str = Field(min_length=1, max_length=500)
    source: Literal["knowledge", "github", "task"]
    url: str = Field(min_length=1, max_length=2000)
    text: str = Field(min_length=1, max_length=12000)


class ToolResult(Strict):
    evidence: list[Evidence] = Field(default_factory=list, max_length=60)
    next_page: int | None = None
    note: str = Field(default="", max_length=500)


class Claim(Strict):
    statement: str = Field(min_length=1, max_length=2000)
    evidence_id: str
    quote: str = Field(min_length=1, max_length=4000)


class Decision(Strict):
    tool: Literal[
        "search_project_knowledge", "query_tasks", "get_pull_request", "create_task", "finish"
    ]
    arguments_json: str = Field(max_length=8000)
    claims: list[Claim] = Field(max_length=15)
    limitation: str = Field(max_length=2000)


class Limits(Strict):
    max_steps: int = Field(default=12, ge=1, le=50)
    max_tokens: int = Field(default=100000, ge=1, le=1000000)
    max_cost_usd: float | None = Field(default=None, gt=0)
    timeout_seconds: float = Field(default=180, gt=0, le=1800)
    tool_timeout_seconds: float = Field(default=20, gt=0, le=120)
    output_tokens: int = Field(default=1500, ge=100, le=8000)


class InvestigationRequest(Strict):
    repository: Repository
    question: str = Field(min_length=1, max_length=4000)
    limits: Limits = Field(default_factory=Limits)


class State(Strict):
    request: InvestigationRequest
    steps: int = 0
    reserved_tokens: int = 0
    reserved_cost_usd: float = 0
    actual_tokens: int = 0
    deadline: float = 0
    status: str = "running"
    history: list[dict[str, object]] = Field(default_factory=list)
    evidence: dict[str, Evidence] = Field(default_factory=dict)
    calls: dict[str, ToolResult] = Field(default_factory=dict)
    claims: list[Claim] = Field(default_factory=list)
    limitation: str = ""


CONTRACTS: dict[str, type[Strict]] = {
    "search_project_knowledge": SearchArgs,
    "query_tasks": TaskArgs,
    "get_pull_request": PRArgs,
    "create_task": CreateArgs,
}
POLICIES = {
    name: {"execution": "synchronous", "write": name == "create_task"} for name in CONTRACTS
}
