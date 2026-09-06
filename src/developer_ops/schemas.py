from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class GitHubItem(BaseModel):
    number: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=1000)
    body: str | None = Field(default=None, max_length=100000)
    state: Literal["open", "closed"]
    updated_at: str


class Repository(BaseModel):
    full_name: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class WebhookPayload(BaseModel):
    action: str = Field(min_length=1, max_length=100)
    repository: Repository
    issue: GitHubItem | None = None
    pull_request: GitHubItem | None = None


class TaskUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1, max_length=2000)
    category: Literal["bug", "feature", "maintenance", "question"]


class DigestOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1, max_length=4000)
