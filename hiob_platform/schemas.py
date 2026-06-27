"""Pydantic schemas for platform entities (validation at system boundaries).

This module defines schemas that validate payloads at write sites, catching
typos and structural errors early with clear validation feedback.

Schemas are permissive (extra='ignore') to avoid breaking when new fields are
added to the database schema. They validate field presence and types only.
"""
from __future__ import annotations

from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class ProductionJobStatusUpdate(BaseModel):
    """Validation schema for production_jobs row updates.

    Fields match the actual production_jobs table columns:
      - status: "running" | "succeeded" | "failed" | "cancelled" | "skipped"
      - started_at: ISO timestamp or "now()" (Supabase special)
      - ended_at: ISO timestamp or "now()" (Supabase special)
      - span_id: Trace span ID (optional)
      - modal_call_id: Modal call ID (optional)
      - error: Error dict with message + code (optional)
      - attributes: Arbitrary metadata dict (optional)
      - updated_at: Updated timestamp (always set by DB trigger, omitted here)

    Design:
      - Permissive: extra='ignore' allows new DB columns without breaking validation
      - Required: status only (minimal safety)
      - Optional: span_id, modal_call_id, error, attributes
      - Special: "now()" is valid for timestamp fields (Supabase PostgreSQL function)
    """

    status: str = Field(
        ...,
        description="Job status: 'running', 'succeeded', 'failed', 'cancelled', or 'skipped'",
    )
    started_at: str | None = Field(
        None,
        description="ISO timestamp or 'now()' (Supabase special). Set when status='running'.",
    )
    ended_at: str | None = Field(
        None,
        description="ISO timestamp or 'now()' (Supabase special). Set when status in {succeeded, failed, cancelled, skipped}.",
    )
    span_id: str | None = Field(
        None,
        description="Langfuse trace span ID for linking observability.",
    )
    modal_call_id: str | None = Field(
        None,
        description="Modal call ID for tracing function execution.",
    )
    error: dict[str, Any] | None = Field(
        None,
        description="Error dict with 'message' and optional 'code' fields.",
    )
    attributes: dict[str, Any] | None = Field(
        None,
        description="Arbitrary JSON metadata (e.g., {'candidate_ids': [...]}, {'tokens_in': 12345}).",
    )

    model_config = ConfigDict(
        extra="ignore",  # Ignore unknown fields (new DB columns won't break validation)
        str_strip_whitespace=True,
    )


# Rebuild model to resolve forward references
ProductionJobStatusUpdate.model_rebuild()


def validate_production_job_update(payload: dict[str, Any]) -> ProductionJobStatusUpdate:
    """Validate a production_jobs update payload.

    Args:
        payload: Dict to validate (typically from team_orchestrator.py).

    Returns:
        Validated ProductionJobStatusUpdate instance.

    Raises:
        pydantic.ValidationError: On invalid structure, missing required fields, or type mismatch.
            Error message includes field path and reason.
    """
    return ProductionJobStatusUpdate(**payload)


__all__ = [
    "ProductionJobStatusUpdate",
    "validate_production_job_update",
]
