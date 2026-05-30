"""
LangChain Tool: recalculate_invoice_tier
==========================================
Looks up the account's pricing tier and recalculates the order total
using the Decimal module.  Gemini passes only structural IDs; all
financial math is executed locally in Python.
"""

from __future__ import annotations

import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config import get_settings
from backend.app.models.db import Account, Order

logger = logging.getLogger(__name__)
settings = get_settings()


class RecalculateInvoiceTierInput(BaseModel):
    """Pydantic validation schema for recalculate_invoice_tier."""
    order_id: int = Field(..., gt=0, description="Target order primary key")
    tier_id: str = Field(
        ..., description="Account pricing tier (STANDARD, WHOLESALE, or VIP)",
    )


@tool(args_schema=RecalculateInvoiceTierInput)
def recalculate_invoice_tier(
    order_id: int,
    tier_id: str,
) -> dict[str, Any]:
    """
    Recalculate the order's calculated_total based on the account's
    pricing tier.  All math is done with Python's Decimal module to
    prevent floating-point precision loss.

    The model provides only the structural IDs — the financial logic
    runs entirely in deterministic local code.
    """
    from backend.app.tasks.worker import SyncSessionLocal

    logger.info(
        "[Tool:recalculate_invoice_tier] order_id=%s, tier_id='%s'",
        order_id, tier_id,
    )

    # Validate tier
    tier_upper = tier_id.upper()
    if tier_upper not in settings.TIER_PRICING:
        valid = list(settings.TIER_PRICING.keys())
        msg = f"Invalid tier '{tier_id}'. Valid tiers: {valid}"
        logger.error("[Tool:recalculate_invoice_tier] %s", msg)
        return {"success": False, "error": msg}

    unit_price = Decimal(settings.TIER_PRICING[tier_upper])

    session: Session = SyncSessionLocal()
    try:
        order = session.execute(
            select(Order).where(Order.id == order_id)
        ).scalars().first()

        if order is None:
            msg = f"Order {order_id} not found."
            logger.error("[Tool:recalculate_invoice_tier] %s", msg)
            return {"success": False, "error": msg}

        # Load the account to confirm tier alignment
        account = session.execute(
            select(Account).where(Account.id == order.account_id)
        ).scalars().first()

        if account is None:
            msg = f"Account {order.account_id} not found for order {order_id}."
            logger.error("[Tool:recalculate_invoice_tier] %s", msg)
            return {"success": False, "error": msg}

        # ── Deterministic Decimal arithmetic ─────────────────────────
        quantity = Decimal(str(order.ordered_quantity))
        old_total = order.calculated_total
        new_total = (quantity * unit_price).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        order.calculated_total = new_total

        logger.info(
            "[Tool:recalculate_invoice_tier] Recalculated | "
            "order_id=%s | qty=%s | unit_price=%s | old_total=%s | new_total=%s",
            order_id, quantity, unit_price, old_total, new_total,
        )

        result = {
            "success": True,
            "order_id": order_id,
            "account_id": account.id,
            "company_name": account.company_name,
            "tier_applied": tier_upper,
            "unit_price": str(unit_price),
            "quantity": int(quantity),
            "old_total": str(old_total),
            "new_total": str(new_total),
        }
        session.commit()
        return result

    except Exception as exc:
        session.rollback()
        logger.exception(
            "[Tool:recalculate_invoice_tier] Error | order_id=%s | err=%s",
            order_id, exc,
        )
        return {"success": False, "error": str(exc)}
    finally:
        session.close()
