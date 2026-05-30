"""
Phase 1 Tests — Database Schema & Seeding (PostgreSQL)
=======================================================
Validates ORM models, enum constraints, relationships, and seed data.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from sqlalchemy import select

from backend.app.models.db import (
    Account,
    AccountTier,
    Inventory,
    Order,
    OrderStatus,
    WarehouseCode,
)


class TestAccountModel:
    def test_create_account(self, db_session):
        acct = Account(company_name="Test Corp", tier=AccountTier.VIP)
        db_session.add(acct)
        db_session.flush()

        result = db_session.execute(
            select(Account).where(Account.company_name == "Test Corp")
        ).scalars().first()

        assert result is not None
        assert result.company_name == "Test Corp"
        assert result.tier == AccountTier.VIP
        assert result.id is not None

    def test_account_tier_enum_values(self):
        assert AccountTier.STANDARD.value == "STANDARD"
        assert AccountTier.WHOLESALE.value == "WHOLESALE"
        assert AccountTier.VIP.value == "VIP"

    def test_account_default_tier(self, db_session):
        acct = Account(company_name="Default Tier Co.")
        db_session.add(acct)
        db_session.flush()
        result = db_session.execute(
            select(Account).where(Account.company_name == "Default Tier Co.")
        ).scalars().first()
        assert result.tier == AccountTier.STANDARD

    def test_account_repr(self):
        acct = Account(id=99, company_name="Repr Test", tier=AccountTier.WHOLESALE)
        assert "Repr Test" in repr(acct)
        assert "WHOLESALE" in repr(acct)


class TestInventoryModel:
    def test_create_inventory(self, db_session):
        inv = Inventory(sku="TEST-SKU-001", warehouse_code=WarehouseCode.WH_A, quantity=100)
        db_session.add(inv)
        db_session.flush()

        result = db_session.execute(
            select(Inventory).where(Inventory.sku == "TEST-SKU-001")
        ).scalars().first()

        assert result is not None
        assert result.sku == "TEST-SKU-001"
        assert result.warehouse_code == WarehouseCode.WH_A
        assert result.quantity == 100

    def test_warehouse_code_enum_values(self):
        assert WarehouseCode.WH_A.value == "WH_A"
        assert WarehouseCode.WH_B.value == "WH_B"

    def test_multiple_warehouses_same_sku(self, db_session):
        db_session.add(Inventory(sku="MULTI-001", warehouse_code=WarehouseCode.WH_A, quantity=50))
        db_session.add(Inventory(sku="MULTI-001", warehouse_code=WarehouseCode.WH_B, quantity=75))
        db_session.flush()

        results = db_session.execute(
            select(Inventory).where(Inventory.sku == "MULTI-001")
        ).scalars().all()
        assert len(results) == 2
        quantities = {r.warehouse_code: r.quantity for r in results}
        assert quantities[WarehouseCode.WH_A] == 50
        assert quantities[WarehouseCode.WH_B] == 75

    def test_inventory_default_quantity(self, db_session):
        inv = Inventory(sku="ZERO-SKU", warehouse_code=WarehouseCode.WH_A)
        db_session.add(inv)
        db_session.flush()
        assert inv.quantity == 0


class TestOrderModel:
    def test_create_order(self, seeded_session):
        """Order can be created with FK to a real account."""
        acct_id = seeded_session._test_accounts[0].id
        order = Order(
            account_id=acct_id,
            sku="TEST-SKU",
            ordered_quantity=10,
            calculated_total=Decimal("199.90"),
            status=OrderStatus.PENDING,
        )
        seeded_session.add(order)
        seeded_session.flush()

        result = seeded_session.execute(
            select(Order).where(Order.sku == "TEST-SKU")
        ).scalars().first()
        assert result is not None
        assert result.ordered_quantity == 10
        assert result.calculated_total == Decimal("199.90")

    def test_order_status_enum_values(self):
        assert OrderStatus.PENDING.value == "PENDING"
        assert OrderStatus.READY_FOR_SHIPPING.value == "READY_FOR_SHIPPING"
        assert OrderStatus.EXCEPTIONAL_HOLD.value == "EXCEPTIONAL_HOLD"

    def test_order_account_relationship(self, seeded_session):
        order = seeded_session._test_orders[0]
        assert order.account is not None
        assert order.account.company_name == "Acme Distribution Co."
        assert order.account.tier == AccountTier.STANDARD

    def test_account_orders_relationship(self, seeded_session):
        acct = seeded_session._test_accounts[0]
        assert len(acct.orders) >= 1
        assert any(o.sku == "WIDGET-001" for o in acct.orders)

    def test_order_error_log_stores_json(self, seeded_session):
        order = seeded_session._test_orders[0]
        parsed = json.loads(order.error_log)
        assert parsed["error"] == "quantity_exceeds_single_warehouse"

    def test_order_decimal_precision(self, seeded_session):
        order = seeded_session._test_orders[2]
        assert order.calculated_total == Decimal("499.50")


class TestSeedData:
    def test_seed_accounts_count(self, seeded_session):
        count = len(seeded_session.execute(select(Account)).scalars().all())
        assert count == 3

    def test_seed_inventory_count(self, seeded_session):
        count = len(seeded_session.execute(select(Inventory)).scalars().all())
        assert count == 6

    def test_seed_orders_count(self, seeded_session):
        count = len(seeded_session.execute(select(Order)).scalars().all())
        assert count == 3

    def test_seed_widget_stock(self, seeded_session):
        rows = seeded_session.execute(
            select(Inventory).where(Inventory.sku == "WIDGET-001")
        ).scalars().all()
        stock = {r.warehouse_code: r.quantity for r in rows}
        assert stock[WarehouseCode.WH_A] == 500
        assert stock[WarehouseCode.WH_B] == 300

    def test_seed_order_statuses(self, seeded_session):
        orders = seeded_session.execute(select(Order)).scalars().all()
        for order in orders:
            assert order.status == OrderStatus.PENDING
