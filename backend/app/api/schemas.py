"""
Pydantic request / response schemas for the webhook ingestion endpoint.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class ErrorTelemetryPayload(BaseModel):
    """
    Incoming error telemetry payload from third-party distribution loggers.

    Attributes:
        order_id:    The database ID of the affected order.
        error_code:  Machine-readable error classification string.
        description: Human-readable error description.
        source:      Originating system identifier.
        metadata:    Arbitrary additional context from the logger.
        timestamp:   ISO-8601 timestamp of when the error was detected.
    """

    order_id: int = Field(..., gt=0, description="Affected order primary key")
    error_code: str = Field(
        ..., min_length=1, max_length=128, description="Machine-readable error code"
    )
    description: str = Field(
        ..., min_length=1, max_length=2048, description="Human-readable error details"
    )
    source: str = Field(
        ..., min_length=1, max_length=128, description="Originating system name"
    )
    metadata: Optional[dict[str, Any]] = Field(
        default=None, description="Optional extra context from the logger"
    )
    timestamp: datetime = Field(
        default_factory=datetime.utcnow,
        description="When the error was detected (ISO-8601)",
    )


class WebhookAcceptedResponse(BaseModel):
    """Response returned immediately upon successful ingestion."""

    status: str = "accepted"
    tracking_id: str = Field(..., description="Unique tracking identifier for this event")
    message: str = "Payload queued for asynchronous processing."
