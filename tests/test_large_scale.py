"""
Large Scale Performance & Load Tests
======================================
Verifies that the system remains responsive and indexes function correctly
when the database is populated with a large volume of data.
"""

from __future__ import annotations

import json
import random
import time
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, insert, select, text
from sqlalchemy.orm import sessionmaker

from backend.app.models.db import (
    Account, AccountTier, Base, Inventory,
    Order, OrderStatus, WarehouseCode,
)

LARGE_DB_URL = "postgresql+psycopg2://vishvmunjapara@localhost:5432/b2b_exceptions_test"
large_engine = create_engine(LARGE_DB_URL, echo=False)
LargeSession = sessionmaker(bind=large_engine)

# Configuration for Large Data Load
NUM_ACCOUNTS = 10_000
NUM_INVENTORY = 20_000  # 10k SKUs * 2 warehouses
NUM_ORDERS = 50_000

@pytest.fixture(scope="module")
def large_db_session():
    """Setup large database once for the whole module."""
    Base.metadata.drop_all(large_engine)
    Base.metadata.create_all(large_engine)

    s = LargeSession()
    
    # 1. Bulk insert Accounts
    print(f"\n[Load Test] Inserting {NUM_ACCOUNTS} Accounts...")
    accounts_data = [
        {
            "company_name": f"Enterprise Corp {i}",
            "tier": random.choice([AccountTier.STANDARD, AccountTier.WHOLESALE, AccountTier.VIP]),
        }
        for i in range(1, NUM_ACCOUNTS + 1)
    ]
    # Chunked insert to avoid memory issues
    for i in range(0, NUM_ACCOUNTS, 5000):
        s.execute(insert(Account).values(accounts_data[i:i+5000]))
    s.commit()

    # 2. Bulk insert Inventory
    print(f"[Load Test] Inserting {NUM_INVENTORY} Inventory records...")
    inventory_data = []
    for i in range(1, (NUM_INVENTORY // 2) + 1):
        sku = f"SKU-BULK-{i:06d}"
        inventory_data.extend([
            {"sku": sku, "warehouse_code": WarehouseCode.WH_A, "quantity": random.randint(10, 1000)},
            {"sku": sku, "warehouse_code": WarehouseCode.WH_B, "quantity": random.randint(0, 500)},
        ])
    for i in range(0, NUM_INVENTORY, 5000):
        s.execute(insert(Inventory).values(inventory_data[i:i+5000]))
    s.commit()

    # 3. Bulk insert Orders
    print(f"[Load Test] Inserting {NUM_ORDERS} Orders...")
    orders_data = []
    statuses = [OrderStatus.PENDING, OrderStatus.READY_FOR_SHIPPING, OrderStatus.EXCEPTIONAL_HOLD]
    for i in range(1, NUM_ORDERS + 1):
        orders_data.append({
            "account_id": random.randint(1, NUM_ACCOUNTS),
            "sku": f"SKU-BULK-{random.randint(1, NUM_INVENTORY // 2):06d}",
            "ordered_quantity": random.randint(1, 100),
            "calculated_total": Decimal(str(round(random.uniform(10.0, 5000.0), 2))),
            "status": random.choice(statuses),
            "error_log": json.dumps({"note": f"Bulk order {i}"}),
        })
    for i in range(0, NUM_ORDERS, 5000):
        s.execute(insert(Order).values(orders_data[i:i+5000]))
    s.commit()
    print("[Load Test] Data load complete.\n")

    yield s

    # Cleanup
    s.close()
    Base.metadata.drop_all(large_engine)


class TestLargeScalePerformance:
    def test_query_single_order_performance(self, large_db_session):
        """Fetching a single order by ID out of 50k should take < 50ms due to PK index."""
        target_id = NUM_ORDERS // 2  # Pick something in the middle
        
        start_time = time.perf_counter()
        order = large_db_session.execute(
            select(Order).where(Order.id == target_id)
        ).scalars().first()
        duration = time.perf_counter() - start_time

        assert order is not None
        assert order.id == target_id
        # Expect sub-millisecond or low millisecond timing, but assert < 50ms to be safe
        assert duration < 0.05, f"Query took too long: {duration:.4f}s"

    def test_inventory_sku_index_performance(self, large_db_session):
        """Fetching inventory by SKU out of 20k rows should take < 50ms due to indexing."""
        target_sku = f"SKU-BULK-{NUM_INVENTORY // 4:06d}"

        start_time = time.perf_counter()
        inventory = large_db_session.execute(
            select(Inventory).where(Inventory.sku == target_sku)
        ).scalars().all()
        duration = time.perf_counter() - start_time

        assert len(inventory) == 2  # WH_A and WH_B
        assert duration < 0.05, f"Inventory index query took too long: {duration:.4f}s"

    def test_join_query_performance(self, large_db_session):
        """Joining Order -> Account out of 50k orders and 10k accounts."""
        target_id = NUM_ORDERS - 100

        start_time = time.perf_counter()
        order = large_db_session.execute(
            select(Order).where(Order.id == target_id)
        ).scalars().first()
        # Accessing order.account triggers lazy load if not joined, or we can explicit join.
        # Here we just access it (lazy load by FK)
        account_tier = order.account.tier 
        duration = time.perf_counter() - start_time

        assert account_tier in [AccountTier.STANDARD, AccountTier.WHOLESALE, AccountTier.VIP]
        assert duration < 0.1, f"Join/Relationship query took too long: {duration:.4f}s"

    def test_tool_execution_performance(self, large_db_session):
        """Test that the inspect_warehouse_stock tool executes quickly on a large DB."""
        from ai_engine.tools.warehouse_tools import inspect_warehouse_stock
        from unittest.mock import patch

        target_sku = "SKU-BULK-000500"

        # Patch the session used by the tool to use our large DB session
        with patch("backend.app.tasks.worker.SyncSessionLocal", return_value=large_db_session):
            start_time = time.perf_counter()
            result = inspect_warehouse_stock.invoke({"sku": target_sku})
            duration = time.perf_counter() - start_time

        assert result["found"] is True
        assert duration < 0.1, f"Tool execution took too long: {duration:.4f}s"
