"""
Phase 2 — FastAPI Ingestion Layer
===================================
POST /api/webhooks/  →  Validates payload, pushes raw JSON into a Redis
Stream (``order_exceptions``), and returns 202 Accepted immediately.
"""

from __future__ import annotations

import json
import logging
import uuid

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.schemas import ErrorTelemetryPayload, WebhookAcceptedResponse
from backend.app.config import get_settings
from backend.app.models.db import AgentAuditLog, Order, get_async_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["webhooks"])

settings = get_settings()

# ── Async Redis client (per-request, avoids stale event loop issues) ─────────


async def _get_redis() -> aioredis.Redis:
    """Create a fresh async Redis connection for this request."""
    client = aioredis.Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        db=settings.REDIS_DB,
        decode_responses=True,
    )
    return client


# ─────────────────────────────────────────────────────────────────────────────
#  POST /api/webhooks/
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/webhooks/",
    response_model=WebhookAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ingest error telemetry from distribution loggers",
    description=(
        "Accepts a validated error payload, dumps it into a Redis Stream "
        "for asynchronous processing, and returns a 202 with a tracking ID."
    ),
)
async def ingest_error_telemetry(
    payload: ErrorTelemetryPayload,
) -> WebhookAcceptedResponse:
    """
    1. Validate incoming payload (handled by Pydantic automatically).
    2. Generate a unique tracking UUID.
    3. Serialize the payload + tracking_id into the Redis Stream.
    4. Return 202 Accepted with the tracking identifier.
    """
    tracking_id: str = str(uuid.uuid4())

    logger.info(
        "Received error telemetry | order_id=%s | error_code=%s | tracking_id=%s",
        payload.order_id,
        payload.error_code,
        tracking_id,
    )

    # Serialize the full payload + tracking_id as a single JSON string field
    stream_entry: dict[str, str] = {
        "tracking_id": tracking_id,
        "payload": payload.model_dump_json(),
    }

    try:
        redis = await _get_redis()
        message_id: str = await redis.xadd(
            name=settings.REDIS_STREAM_NAME,
            fields=stream_entry,
        )
        logger.info(
            "Payload pushed to Redis Stream '%s' | message_id=%s | tracking_id=%s",
            settings.REDIS_STREAM_NAME,
            message_id,
            tracking_id,
        )
    except Exception as exc:
        logger.error(
            "Failed to push payload to Redis Stream | tracking_id=%s | error=%s",
            tracking_id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Queueing service temporarily unavailable. Please retry.",
        ) from exc

    return WebhookAcceptedResponse(tracking_id=tracking_id)


# ─────────────────────────────────────────────────────────────────────────────
#  GET /api/orders/
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/orders/")
async def list_orders(
    skip: int = 0,
    limit: int = 100,
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Return orders with their statuses using pagination."""
    # Count total orders
    from sqlalchemy import func
    total_result = await session.execute(select(func.count(Order.id)))
    total = total_result.scalar_one()

    # Fetch paginated orders
    result = await session.execute(
        select(Order).order_by(Order.id.asc()).offset(skip).limit(limit)
    )
    orders = result.scalars().all()
    
    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "data": [
            {
                "id": o.id,
                "account_id": o.account_id,
                "sku": o.sku,
                "ordered_quantity": o.ordered_quantity,
                "calculated_total": float(o.calculated_total),
                "status": o.status.value,
                "error_log": json.loads(o.error_log) if o.error_log else None,
            }
            for o in orders
        ]
    }


# ─────────────────────────────────────────────────────────────────────────────
#  GET /api/orders/{order_id}/audit-logs/
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/orders/{order_id}/audit-logs/")
async def get_audit_logs(
    order_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> list[dict]:
    """Return audit logs for a specific order."""
    result = await session.execute(
        select(AgentAuditLog)
        .where(AgentAuditLog.order_id == order_id)
        .order_by(AgentAuditLog.timestamp.asc())
    )
    logs = result.scalars().all()
    
    return [
        {
            "id": log.id,
            "order_id": log.order_id,
            "tool_executed": log.tool_executed,
            "arguments_passed": json.loads(log.arguments_passed),
            "tool_output": json.loads(log.tool_output) if log.tool_output.startswith("{") else log.tool_output,
            "timestamp": log.timestamp.isoformat(),
        }
        for log in logs
    ]
