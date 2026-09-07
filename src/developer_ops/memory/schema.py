"""Versioned canonical documents and citation-bearing chunks."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Document(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: str = "document-v1"
    id: str = Field(min_length=1)
    repository: str = Field(min_length=1)
    source_type: Literal["issue", "pr", "discussion", "doc", "commit", "digest"]
    title: str = Field(min_length=1)
    url: str = Field(min_length=1)
    text: str = Field(min_length=1)
    author: str = ""
    created_at: str = ""
    updated_at: str = ""
    version: str = ""
    parent_id: str = ""
    related_ids: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    state: str = ""
    visibility: Literal["local-owner"] = "local-owner"
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    normalization_version: str = "clean-v1"

    @field_validator("created_at", "updated_at")
    @classmethod
    def timestamp(cls, value: str) -> str:
        if value:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("Timestamps require timezone")
        return value

    @field_validator("url")
    @classmethod
    def source_url(cls, value: str) -> str:
        if not value.startswith(("https://", "git:", "/")):
            raise ValueError("Source must be HTTPS, a git reference, or an absolute local path")
        return value


class Chunk(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    document_id: str
    repository: str
    title: str
    url: str
    source_type: str
    updated_at: str
    position: int = Field(ge=0)
    heading: str
    text: str = Field(min_length=1)
    word_count: int = Field(gt=0)
    start_line: int = Field(gt=0)
    chunking_version: str
