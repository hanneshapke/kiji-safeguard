"""Pydantic models for the MCP server registry API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ServerRegistration(BaseModel):
    """Request body for ``POST /servers``."""

    name: str = Field(min_length=1, description="MCP server name")
    hash: str = Field(
        min_length=64,
        max_length=64,
        description="Aggregate SHA-256 hash of the interface components",
    )
    interface: list[dict[str, Any]] = Field(
        description="Interface components: tools, prompts, resources, instructions"
    )


class InterfaceSummary(BaseModel):
    """Human-readable summary of a registered interface."""

    tool_count: int
    prompt_count: int
    resource_count: int
    tools: list[str]


class ServerRecord(BaseModel):
    """A registered MCP server as returned by the API."""

    id: int
    name: str
    hash: str
    interface: list[dict[str, Any]]
    registered_at: str
    summary: InterfaceSummary


class ServerListResponse(BaseModel):
    """Response for ``GET /servers``."""

    servers: list[ServerRecord]
    total: int
