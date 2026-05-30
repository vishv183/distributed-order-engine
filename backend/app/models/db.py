"""
Phase 1 — Database Schema & Seeding
====================================
Defines three structured tables using SQLAlchemy ORM:
  • Accounts  — B2B customer entities with pricing tiers
  • Inventory — Warehouse stock tracking with SKU indexing
  • Orders    — Distribution order records with exception status tracking

All Enum types are native PostgreSQL enums for data integrity.
"""

from __future__ import annotations

import enum
import logging
import uuid
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    event,
    func,
)
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
    sessionmaker,
)
from sqlalchemy.dialects.postgresql import UUID

from backend.app.config import get_settings

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
#  Enumerations
# ─────────────────────────────────────────────────────────────────────────────


class AccountTier(str, enum.Enum):
    """Pricing tier classification for B2B accounts."""
    STANDARD = "STANDARD"
    WHOLESALE = "WHOLESALE"
    VIP = "VIP"


class WarehouseCode(str, enum.Enum):
    """Physical warehouse location identifiers."""
    WH_A = "WH_A"
    WH_B = "WH_B"


class OrderStatus(str, enum.Enum):
    """Lifecycle status of a distribution order."""
    PENDING = "PENDING"
    READY_FOR_SHIPPING = "READY_FOR_SHIPPING"
    EXCEPTIONAL_HOLD = "EXCEPTIONAL_HOLD"


# ─────────────────────────────────────────────────────────────────────────────
#  Base Model
# ─────────────────────────────────────────────────────────────────────────────


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""
    pass


# ─────────────────────────────────────────────────────────────────────────────
#  Accounts Table
# ─────────────────────────────────────────────────────────────────────────────


class Account(Base):
    """
    B2B customer account.

    Attributes:
        id:           Primary key.
        company_name: Legal entity name.
        tier:         Pricing tier (STANDARD | WHOLESALE | VIP).
    """

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    tier: Mapped[AccountTier] = mapped_column(
        Enum(AccountTier, name="account_tier", native_enum=True),
        nullable=False,
        default=AccountTier.STANDARD,
    )

    # Relationship: one account → many orders
    orders: Mapped[List["Order"]] = relationship("Order", back_populates="account")

    def __repr__(self) -> str:
        return f"<Account(id={self.id}, company='{self.company_name}', tier={self.tier.value})>"


# ─────────────────────────────────────────────────────────────────────────────
#  Inventory Table
# ─────────────────────────────────────────────────────────────────────────────


class Inventory(Base):
    """
    Warehouse stock record.

    Attributes:
        id:             Primary key.
        sku:            Stock Keeping Unit (indexed for fast lookup).
        warehouse_code: Physical warehouse identifier.
        quantity:        Current on-hand item count.
    """

    __tablename__ = "inventory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sku: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    warehouse_code: Mapped[WarehouseCode] = mapped_column(
        Enum(WarehouseCode, name="warehouse_code", native_enum=True),
        nullable=False,
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Composite index for warehouse+sku lookups
    __table_args__ = (
        Index("ix_inventory_sku_warehouse", "sku", "warehouse_code"),
    )

    def __repr__(self) -> str:
        return (
            f"<Inventory(id={self.id}, sku='{self.sku}', "
            f"warehouse={self.warehouse_code.value}, qty={self.quantity})>"
        )


# ─────────────────────────────────────────────────────────────────────────────
#  Orders Table
# ─────────────────────────────────────────────────────────────────────────────


class Order(Base):
    """
    Distribution order record.

    Attributes:
        id:                Primary key.
        account_id:        Foreign key → accounts.id.
        sku:               Ordered product SKU.
        ordered_quantity:   Number of units requested.
        calculated_total:   Invoice total computed via Decimal arithmetic.
        status:            Current lifecycle status.
        error_log:         JSON-formatted error telemetry string.
    """

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("accounts.id"), nullable=False
    )
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    ordered_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    calculated_total: Mapped[Decimal] = mapped_column(
        Numeric(precision=12, scale=2), nullable=False, default=Decimal("0.00")
    )
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus, name="order_status", native_enum=True),
        nullable=False,
        default=OrderStatus.PENDING,
    )
    error_log: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationship: order → parent account
    account: Mapped["Account"] = relationship("Account", back_populates="orders")

    def __repr__(self) -> str:
        return (
            f"<Order(id={self.id}, account_id={self.account_id}, sku='{self.sku}', "
            f"qty={self.ordered_quantity}, total={self.calculated_total}, "
            f"status={self.status.value})>"
        )


# ─────────────────────────────────────────────────────────────────────────────
#  Agent Audit Logs Table (Historical Observability)
# ─────────────────────────────────────────────────────────────────────────────


class AgentAuditLog(Base):
    """
    Immutable audit trail for every tool call executed by the LangChain agent.

    Enterprise compliance requirement: once an audit row is written it is
    never updated or deleted.  This guarantees stakeholders can reconstruct
    exactly why an invoice total or warehouse allocation was altered.

    Attributes:
        id:               UUID primary key (non-sequential for security).
        order_id:         FK → orders.id — which order was being triaged.
        tool_executed:    Name of the LangChain tool that ran.
        arguments_passed: JSON block of the arguments Gemini generated.
        tool_output:      JSON block of what the database/tool returned.
        timestamp:        Server-side UTC timestamp of execution.
    """

    __tablename__ = "agent_audit_logs"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    order_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("orders.id"), nullable=False, index=True
    )
    tool_executed: Mapped[str] = mapped_column(String(128), nullable=False)
    arguments_passed: Mapped[str] = mapped_column(Text, nullable=False)
    tool_output: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationship back to Order
    order: Mapped["Order"] = relationship("Order", backref="audit_logs")

    def __repr__(self) -> str:
        return (
            f"<AgentAuditLog(id={self.id[:8]}..., order_id={self.order_id}, "
            f"tool='{self.tool_executed}', ts={self.timestamp})>"
        )


# ─────────────────────────────────────────────────────────────────────────────
#  Async Engine & Session Factory
# ─────────────────────────────────────────────────────────────────────────────

settings = get_settings()

async_engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_size=10,
    max_overflow=20,
    connect_args={
        "server_settings": {
            "statement_timeout": str(settings.DB_STATEMENT_TIMEOUT_MS),
        }
    },
)

AsyncSessionLocal = sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_async_session() -> AsyncSession:
    """Dependency-injectable async session generator."""
    async with AsyncSessionLocal() as session:
        yield session


async def init_db() -> None:
    """Create all tables if they do not exist."""
    async with async_engine.begin() as conn:
        logger.info("Initializing database schema — creating tables if absent.")
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database schema initialization complete.")


async def seed_db() -> None:
    """
    Populate the database with deterministic seed data for development.
    Idempotent: skips seeding if accounts already exist.
    """
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        async with session.begin():
            result = await session.execute(select(Account).limit(1))
            if result.scalars().first() is not None:
                logger.info("Seed data already present — skipping.")
                return

            # ── Accounts ──────────────────────────────────────────────
            accounts = [
                Account(company_name="Acme Distribution Co.", tier=AccountTier.STANDARD),
                Account(company_name="BulkTrade Wholesale LLC", tier=AccountTier.WHOLESALE),
                Account(company_name="Premium Partners Inc.", tier=AccountTier.VIP),
            ]
            session.add_all(accounts)
            await session.flush()  # Generate IDs

            # ── Inventory ─────────────────────────────────────────────
            inventory_rows = [
                Inventory(sku="WIDGET-001", warehouse_code=WarehouseCode.WH_A, quantity=500),
                Inventory(sku="WIDGET-001", warehouse_code=WarehouseCode.WH_B, quantity=300),
                Inventory(sku="GADGET-002", warehouse_code=WarehouseCode.WH_A, quantity=150),
                Inventory(sku="GADGET-002", warehouse_code=WarehouseCode.WH_B, quantity=0),
                Inventory(sku="SPROCKET-003", warehouse_code=WarehouseCode.WH_A, quantity=1200),
                Inventory(sku="SPROCKET-003", warehouse_code=WarehouseCode.WH_B, quantity=800),
            ]
            session.add_all(inventory_rows)

            # ── Orders ────────────────────────────────────────────────
            orders = [
                Order(
                    account_id=accounts[0].id,
                    sku="WIDGET-001",
                    ordered_quantity=600,
                    calculated_total=Decimal("11994.00"),
                    status=OrderStatus.PENDING,
                    error_log='{"error": "quantity_exceeds_single_warehouse"}',
                ),
                Order(
                    account_id=accounts[1].id,
                    sku="GADGET-002",
                    ordered_quantity=200,
                    calculated_total=Decimal("2898.00"),
                    status=OrderStatus.PENDING,
                    error_log='{"error": "warehouse_B_out_of_stock"}',
                ),
                Order(
                    account_id=accounts[2].id,
                    sku="SPROCKET-003",
                    ordered_quantity=50,
                    calculated_total=Decimal("499.50"),
                    status=OrderStatus.PENDING,
                    error_log='{"error": "tier_pricing_mismatch"}',
                ),
            ]
            session.add_all(orders)

            logger.info("Seed data inserted: 3 accounts, 6 inventory rows, 3 orders.")
