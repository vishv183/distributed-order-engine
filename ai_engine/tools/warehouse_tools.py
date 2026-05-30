"""
LangChain Tool: inspect_warehouse_stock
========================================
Queries real-time item availability across all warehouses using a
pessimistic row lock (SELECT ... FOR UPDATE) to bypass dirty cache reads.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.db import Inventory, WarehouseCode

logger = logging.getLogger(__name__)


class InspectWarehouseStockInput(BaseModel):
    """Pydantic validation schema for inspect_warehouse_stock."""
    sku: str = Field(
        ..., min_length=1, max_length=64,
        description="Stock Keeping Unit identifier to look up",
    )


@tool(args_schema=InspectWarehouseStockInput)
def inspect_warehouse_stock(sku: str) -> dict[str, Any]:
    """
    Query real-time warehouse stock for a given SKU across all warehouses.
    Uses a pessimistic row lock (SELECT ... FOR UPDATE) to guarantee
    consistency and prevent dirty reads from cached data.

    Returns a dictionary with per-warehouse quantities and a total.
    """
    from backend.app.tasks.worker import SyncSessionLocal

    logger.info("[Tool:inspect_warehouse_stock] Querying SKU='%s'", sku)

    session: Session = SyncSessionLocal()
    try:
        # Pessimistic lock: FOR UPDATE prevents concurrent modifications
        rows = session.execute(
            select(Inventory)
            .where(Inventory.sku == sku)
            .with_for_update()
        ).scalars().all()

        if not rows:
            logger.warning(
                "[Tool:inspect_warehouse_stock] No inventory found for SKU='%s'",
                sku,
            )
            return {
                "sku": sku,
                "found": False,
                "warehouses": [],
                "total_available": 0,
            }

        warehouse_data: list[dict[str, Any]] = []
        total: int = 0

        for inv in rows:
            warehouse_data.append({
                "warehouse_code": inv.warehouse_code.value,
                "quantity": inv.quantity,
            })
            total += inv.quantity
            logger.info(
                "[Tool:inspect_warehouse_stock] %s → %s = %d units",
                sku, inv.warehouse_code.value, inv.quantity,
            )

        result = {
            "sku": sku,
            "found": True,
            "warehouses": warehouse_data,
            "total_available": total,
        }
        logger.info(
            "[Tool:inspect_warehouse_stock] Result: %s", result
        )
        return result

    finally:
        session.close()
