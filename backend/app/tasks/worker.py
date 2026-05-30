"""
Phase 3 — Asynchronous Celery Worker
======================================
Listens to the Redis stream, extracts order_id, invokes the LangChain
agent loop inside a PostgreSQL transactional block.  On any error or
timeout the entire DB state rolls back automatically.
"""

from __future__ import annotations

import json
import logging
import signal
import time
from contextlib import contextmanager
from typing import Any

import redis
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.config import get_settings
from backend.app.models.db import Order, OrderStatus
from backend.app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)
settings = get_settings()

# ── Synchronous DB engine (Celery workers are sync) ─────────────────────────
sync_engine = create_engine(
    settings.SYNC_DATABASE_URL,
    echo=False,
    pool_size=5,
    max_overflow=10,
    connect_args={
        "options": f"-c statement_timeout={settings.DB_STATEMENT_TIMEOUT_MS}",
    },
)
SyncSessionLocal = sessionmaker(bind=sync_engine)

# ── Synchronous Redis client for stream reading ────────────────────────────
sync_redis = redis.Redis(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    db=settings.REDIS_DB,
    decode_responses=True,
)


class AgentTimeoutError(Exception):
    """Raised when agent execution exceeds the configured timeout."""
    pass


@contextmanager
def _timeout_guard(seconds: int):
    """Context manager that raises AgentTimeoutError after *seconds*."""
    def _handler(signum, frame):
        raise AgentTimeoutError(
            f"Agent execution exceeded {seconds}s timeout"
        )
    old_handler = signal.signal(signal.SIGALRM, _handler)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)


# ─────────────────────────────────────────────────────────────────────────────
#  Celery Task
# ─────────────────────────────────────────────────────────────────────────────


@celery_app.task(
    name="process_order_exception",
    bind=True,
    max_retries=1,
    default_retry_delay=30,
    acks_late=True,
)
def process_order_exception(self, stream_entry: dict[str, Any]) -> dict[str, str]:
    """
    Process a single order exception from the Redis stream.

    1. Extract the order_id from the payload.
    2. Open a PostgreSQL transactional block.
    3. Run the LangChain agent loop inside the transaction.
    4. On success → commit.  On error/timeout → rollback.
    """
    tracking_id: str = stream_entry.get("tracking_id", "unknown")
    raw_payload: str = stream_entry.get("payload", "{}")

    logger.info(
        "[Worker] Picked up stream entry | tracking_id=%s", tracking_id
    )

    try:
        payload: dict = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        logger.error(
            "[Worker] Malformed JSON in stream entry | tracking_id=%s | err=%s",
            tracking_id, exc,
        )
        return {"status": "failed", "reason": "malformed_json"}

    order_id: int = payload.get("order_id", 0)
    if order_id <= 0:
        logger.error(
            "[Worker] Invalid order_id=%s | tracking_id=%s", order_id, tracking_id
        )
        return {"status": "failed", "reason": "invalid_order_id"}

    logger.info(
        "[Worker] Beginning transactional agent loop | order_id=%s | tracking_id=%s",
        order_id, tracking_id,
    )

    # ── Transactional block: auto-rollback on any exception ──────────
    session: Session = SyncSessionLocal()
    try:
        with session.begin():
            # Verify the order exists
            order = session.execute(
                select(Order).where(Order.id == order_id)
            ).scalars().first()

            if order is None:
                logger.error(
                    "[Worker] Order not found | order_id=%s", order_id
                )
                return {"status": "failed", "reason": "order_not_found"}

            logger.info(
                "[Worker] Order loaded | %r", order
            )

            # Run the agent with a timeout guard
            with _timeout_guard(settings.AGENT_TIMEOUT_SECONDS):
                from ai_engine.agent import run_exception_agent
                result = run_exception_agent(
                    order_id=order_id,
                    payload=payload,
                    db_session=session,
                )

            logger.info(
                "[Worker] Agent completed | order_id=%s | result=%s",
                order_id, result,
            )

        # Transaction committed successfully
        return {
            "status": "success",
            "tracking_id": tracking_id,
            "order_id": str(order_id),
            "agent_result": str(result),
        }

    except AgentTimeoutError:
        session.rollback()
        logger.error(
            "[Worker] TIMEOUT — agent exceeded %ss | order_id=%s",
            settings.AGENT_TIMEOUT_SECONDS, order_id,
        )
        # Flag order for human review outside the rolled-back txn
        _flag_exceptional_hold(order_id, "agent_timeout")
        return {"status": "timeout", "order_id": str(order_id)}

    except Exception as exc:
        session.rollback()
        logger.exception(
            "[Worker] UNHANDLED ERROR — full rollback | order_id=%s | err=%s",
            order_id, exc,
        )
        _flag_exceptional_hold(order_id, f"unhandled_error: {exc}")
        return {"status": "error", "order_id": str(order_id)}

    finally:
        session.close()


def _flag_exceptional_hold(order_id: int, reason: str) -> None:
    """
    Mark an order as EXCEPTIONAL_HOLD in a fresh, independent transaction.
    This must succeed even if the main transaction was rolled back.
    """
    session = SyncSessionLocal()
    try:
        with session.begin():
            order = session.execute(
                select(Order).where(Order.id == order_id)
            ).scalars().first()
            if order:
                order.status = OrderStatus.EXCEPTIONAL_HOLD
                order.error_log = json.dumps({
                    "hold_reason": reason,
                    "flagged_by": "celery_worker",
                })
                logger.warning(
                    "[Worker] Order flagged EXCEPTIONAL_HOLD | order_id=%s | reason=%s",
                    order_id, reason,
                )
    except Exception as exc:
        logger.critical(
            "[Worker] FAILED to flag order %s as EXCEPTIONAL_HOLD | err=%s",
            order_id, exc,
        )
    finally:
        session.close()


# ─────────────────────────────────────────────────────────────────────────────
#  Redis Stream Consumer (invoked on worker startup or via beat)
# ─────────────────────────────────────────────────────────────────────────────


def consume_redis_stream() -> None:
    """
    Blocking loop that reads from the Redis stream and dispatches
    each entry to the Celery task queue.
    """
    stream_name = settings.REDIS_STREAM_NAME
    last_id = "0-0"

    logger.info("[StreamConsumer] Listening on Redis stream '%s'", stream_name)

    while True:
        try:
            entries = sync_redis.xread(
                {stream_name: last_id}, count=10, block=5000
            )
            if not entries:
                continue

            for _stream, messages in entries:
                for msg_id, fields in messages:
                    logger.info(
                        "[StreamConsumer] Dispatching message %s → Celery",
                        msg_id,
                    )
                    process_order_exception.delay(dict(fields))
                    last_id = msg_id

        except KeyboardInterrupt:
            logger.info("[StreamConsumer] Shutting down gracefully.")
            break
        except Exception as exc:
            logger.error(
                "[StreamConsumer] Error reading stream | err=%s", exc
            )
            time.sleep(2)
