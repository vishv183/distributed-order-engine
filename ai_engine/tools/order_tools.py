"""
LangChain Tool: split_order_batch
===================================
Creates a segmented shipment allocation when a single warehouse cannot
fulfill a bulk order completely.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.db import (
    Inventory,
    Order,
    OrderStatus,
    WarehouseCode,
)

logger = logging.getLogger(__name__)


class SplitOrderBatchInput(BaseModel):
    """Pydantic validation schema for split_order_batch."""
    order_id: int = Field(..., gt=0, description="Target order primary key")
    warehouse_code: str = Field(
        ..., description="Warehouse to allocate from (WH_A or WH_B)",
    )
    partial_quantity: int = Field(
        ..., gt=0, description="Number of units to allocate from this warehouse",
    )


@tool(args_schema=SplitOrderBatchInput)
def split_order_batch(
    order_id: int,
    warehouse_code: str,
    partial_quantity: int,
) -> dict[str, Any]:
    """
    Create a segmented shipment allocation row for an order when a single
    warehouse cannot fulfill the full quantity.

    Deducts inventory from the specified warehouse and creates a new
    partial-fulfillment order row linked to the same account.
    """
    from backend.app.tasks.worker import SyncSessionLocal

    logger.info(
        "[Tool:split_order_batch] order_id=%s, warehouse=%s, qty=%d",
        order_id, warehouse_code, partial_quantity,
    )

    # Validate warehouse code enum
    try:
        wh_enum = WarehouseCode(warehouse_code)
    except ValueError:
        msg = f"Invalid warehouse_code '{warehouse_code}'. Must be WH_A or WH_B."
        logger.error("[Tool:split_order_batch] %s", msg)
        return {"success": False, "error": msg}

    session: Session = SyncSessionLocal()
    try:
        # Load the parent order
        order = session.execute(
            select(Order).where(Order.id == order_id)
        ).scalars().first()

        if order is None:
            msg = f"Order {order_id} not found."
            logger.error("[Tool:split_order_batch] %s", msg)
            return {"success": False, "error": msg}

        # Lock the inventory row
        inv = session.execute(
            select(Inventory)
            .where(
                Inventory.sku == order.sku,
                Inventory.warehouse_code == wh_enum,
            )
            .with_for_update()
        ).scalars().first()

        if inv is None or inv.quantity < partial_quantity:
            available = inv.quantity if inv else 0
            msg = (
                f"Insufficient stock in {warehouse_code} for SKU '{order.sku}'. "
                f"Requested={partial_quantity}, Available={available}."
            )
            logger.warning("[Tool:split_order_batch] %s", msg)
            return {"success": False, "error": msg}

        # Deduct inventory
        inv.quantity -= partial_quantity
        logger.info(
            "[Tool:split_order_batch] Deducted %d from %s → remaining=%d",
            partial_quantity, warehouse_code, inv.quantity,
        )

        # Create a partial-fulfillment order row
        partial_order = Order(
            account_id=order.account_id,
            sku=order.sku,
            ordered_quantity=partial_quantity,
            calculated_total=order.calculated_total,  # Will be recalculated
            status=OrderStatus.READY_FOR_SHIPPING,
            error_log=f'{{"split_from_order": {order_id}, "warehouse": "{warehouse_code}"}}',
        )
        session.add(partial_order)
        session.flush()

        logger.info(
            "[Tool:split_order_batch] Created partial order id=%s for %d units from %s",
            partial_order.id, partial_quantity, warehouse_code,
        )

        result = {
            "success": True,
            "parent_order_id": order_id,
            "new_partial_order_id": partial_order.id,
            "warehouse_code": warehouse_code,
            "allocated_quantity": partial_quantity,
            "remaining_inventory": inv.quantity,
        }
        session.commit()
        return result

    except Exception as exc:
        session.rollback()
        logger.exception(
            "[Tool:split_order_batch] Error | order_id=%s | err=%s",
            order_id, exc,
        )
        return {"success": False, "error": str(exc)}
    finally:
        session.close()
