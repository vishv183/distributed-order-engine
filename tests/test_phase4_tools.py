"""
Phase 4 Tests — LangChain Tools against real PostgreSQL
========================================================
Tests each @tool function with actual database operations.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.models.db import (
    Account, AccountTier, Base, Inventory,
    Order, OrderStatus, WarehouseCode,
)
from ai_engine.tools.warehouse_tools import inspect_warehouse_stock
from ai_engine.tools.order_tools import split_order_batch
from ai_engine.tools.invoice_tools import recalculate_invoice_tier

TOOL_DB = "postgresql+psycopg2://vishvmunjapara@localhost:5432/b2b_exceptions_test"
tool_engine = create_engine(TOOL_DB, echo=False)
ToolSession = sessionmaker(bind=tool_engine)


@pytest.fixture(autouse=True)
def setup_tool_db():
    """Recreate tables + seed before each test, patch session factory."""
    Base.metadata.drop_all(tool_engine)
    Base.metadata.create_all(tool_engine)

    s = ToolSession()
    accts = [
        Account(company_name="Acme Co.", tier=AccountTier.STANDARD),
        Account(company_name="BulkTrade LLC", tier=AccountTier.WHOLESALE),
        Account(company_name="Premium Inc.", tier=AccountTier.VIP),
    ]
    s.add_all(accts); s.flush()

    s.add_all([
        Inventory(sku="WIDGET-001", warehouse_code=WarehouseCode.WH_A, quantity=500),
        Inventory(sku="WIDGET-001", warehouse_code=WarehouseCode.WH_B, quantity=300),
        Inventory(sku="GADGET-002", warehouse_code=WarehouseCode.WH_A, quantity=150),
        Inventory(sku="GADGET-002", warehouse_code=WarehouseCode.WH_B, quantity=0),
        Inventory(sku="SPROCKET-003", warehouse_code=WarehouseCode.WH_A, quantity=1200),
        Inventory(sku="SPROCKET-003", warehouse_code=WarehouseCode.WH_B, quantity=800),
    ])
    s.flush()

    s.add_all([
        Order(account_id=accts[0].id, sku="WIDGET-001", ordered_quantity=600,
              calculated_total=Decimal("11994.00"), status=OrderStatus.PENDING),
        Order(account_id=accts[1].id, sku="GADGET-002", ordered_quantity=200,
              calculated_total=Decimal("2898.00"), status=OrderStatus.PENDING),
        Order(account_id=accts[2].id, sku="SPROCKET-003", ordered_quantity=50,
              calculated_total=Decimal("499.50"), status=OrderStatus.PENDING),
    ])
    s.commit(); s.close()

    # Patch the source module that tools lazily import from
    with patch("backend.app.tasks.worker.SyncSessionLocal", ToolSession):
        yield

    Base.metadata.drop_all(tool_engine)


class TestInspectWarehouseStock:
    def test_known_sku_total(self):
        r = inspect_warehouse_stock.invoke({"sku": "WIDGET-001"})
        assert r["found"] is True
        assert r["total_available"] == 800

    def test_per_warehouse_breakdown(self):
        r = inspect_warehouse_stock.invoke({"sku": "WIDGET-001"})
        m = {w["warehouse_code"]: w["quantity"] for w in r["warehouses"]}
        assert m["WH_A"] == 500 and m["WH_B"] == 300

    def test_zero_stock_warehouse(self):
        r = inspect_warehouse_stock.invoke({"sku": "GADGET-002"})
        assert r["total_available"] == 150

    def test_unknown_sku(self):
        r = inspect_warehouse_stock.invoke({"sku": "FAKE-999"})
        assert r["found"] is False and r["total_available"] == 0

    def test_high_stock(self):
        r = inspect_warehouse_stock.invoke({"sku": "SPROCKET-003"})
        assert r["total_available"] == 2000


class TestSplitOrderBatch:
    def test_successful_split(self):
        r = split_order_batch.invoke({"order_id": 1, "warehouse_code": "WH_A", "partial_quantity": 200})
        assert r["success"] is True and r["remaining_inventory"] == 300

    def test_inventory_deducted(self):
        split_order_batch.invoke({"order_id": 1, "warehouse_code": "WH_A", "partial_quantity": 100})
        s = ToolSession()
        inv = s.execute(select(Inventory).where(
            Inventory.sku == "WIDGET-001", Inventory.warehouse_code == WarehouseCode.WH_A
        )).scalars().first()
        assert inv.quantity == 400
        s.close()

    def test_partial_order_created(self):
        r = split_order_batch.invoke({"order_id": 1, "warehouse_code": "WH_A", "partial_quantity": 300})
        s = ToolSession()
        o = s.execute(select(Order).where(Order.id == r["new_partial_order_id"])).scalars().first()
        assert o.ordered_quantity == 300 and o.status == OrderStatus.READY_FOR_SHIPPING
        s.close()

    def test_insufficient_stock(self):
        r = split_order_batch.invoke({"order_id": 1, "warehouse_code": "WH_A", "partial_quantity": 9999})
        assert r["success"] is False

    def test_invalid_warehouse(self):
        r = split_order_batch.invoke({"order_id": 1, "warehouse_code": "WH_X", "partial_quantity": 10})
        assert r["success"] is False

    def test_nonexistent_order(self):
        r = split_order_batch.invoke({"order_id": 9999, "warehouse_code": "WH_A", "partial_quantity": 10})
        assert r["success"] is False


class TestRecalculateInvoiceTier:
    def test_standard_price(self):
        r = recalculate_invoice_tier.invoke({"order_id": 1, "tier_id": "STANDARD"})
        assert r["success"] and r["new_total"] == "11994.00"

    def test_vip_price(self):
        r = recalculate_invoice_tier.invoke({"order_id": 3, "tier_id": "VIP"})
        assert r["success"] and r["new_total"] == "499.50"

    def test_tier_change_updates_total(self):
        r = recalculate_invoice_tier.invoke({"order_id": 1, "tier_id": "VIP"})
        assert Decimal(r["new_total"]) == Decimal("5994.00")

    def test_persisted_to_db(self):
        recalculate_invoice_tier.invoke({"order_id": 1, "tier_id": "VIP"})
        s = ToolSession()
        o = s.execute(select(Order).where(Order.id == 1)).scalars().first()
        assert o.calculated_total == Decimal("5994.00")
        s.close()

    def test_invalid_tier(self):
        r = recalculate_invoice_tier.invoke({"order_id": 1, "tier_id": "ULTRA"})
        assert r["success"] is False

    def test_case_insensitive(self):
        r = recalculate_invoice_tier.invoke({"order_id": 1, "tier_id": "wholesale"})
        assert r["success"] and r["tier_applied"] == "WHOLESALE"

    def test_decimal_precision(self):
        r = recalculate_invoice_tier.invoke({"order_id": 2, "tier_id": "WHOLESALE"})
        assert Decimal(r["new_total"]) == Decimal("200") * Decimal("14.49")
