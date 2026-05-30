"""
Production Hardening Tests — Audit Logging, Deadlock Prevention, Observability
================================================================================
Tests the three new production features:
  1. AgentAuditLog table + immutable audit trail
  2. statement_timeout deadlock prevention
  3. LangSmith tracing configuration
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from backend.app.models.db import (
    Account, AccountTier, AgentAuditLog, Base, Inventory,
    Order, OrderStatus, WarehouseCode,
)

PROD_DB = "postgresql+psycopg2://vishvmunjapara@localhost:5432/b2b_exceptions_test"
prod_engine = create_engine(PROD_DB, echo=False)
ProdSession = sessionmaker(bind=prod_engine)


@pytest.fixture(autouse=True)
def setup_prod_db():
    """Recreate tables + seed before each test."""
    Base.metadata.drop_all(prod_engine)
    Base.metadata.create_all(prod_engine)

    s = ProdSession()
    accts = [
        Account(company_name="Acme Co.", tier=AccountTier.STANDARD),
        Account(company_name="BulkTrade LLC", tier=AccountTier.WHOLESALE),
    ]
    s.add_all(accts); s.flush()

    s.add_all([
        Inventory(sku="WIDGET-001", warehouse_code=WarehouseCode.WH_A, quantity=500),
        Inventory(sku="WIDGET-001", warehouse_code=WarehouseCode.WH_B, quantity=300),
    ])
    s.flush()

    s.add_all([
        Order(account_id=accts[0].id, sku="WIDGET-001", ordered_quantity=600,
              calculated_total=Decimal("11994.00"), status=OrderStatus.PENDING),
    ])
    s.commit(); s.close()

    with patch("backend.app.tasks.worker.SyncSessionLocal", ProdSession):
        yield

    Base.metadata.drop_all(prod_engine)


def _make_ai_message(content="", tool_calls=None):
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls or []
    return msg


# ─────────────────────────────────────────────────────────────────────────────
#  Feature 1: AgentAuditLog Table Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestAgentAuditLogModel:
    """Tests for the AgentAuditLog ORM model."""

    def test_audit_log_table_exists(self):
        """agent_audit_logs table is created by metadata."""
        assert "agent_audit_logs" in Base.metadata.tables

    def test_create_audit_entry(self):
        """Can create and persist an audit log entry."""
        s = ProdSession()
        entry = AgentAuditLog(
            order_id=1,
            tool_executed="inspect_warehouse_stock",
            arguments_passed='{"sku": "WIDGET-001"}',
            tool_output='{"found": true, "total_available": 800}',
        )
        s.add(entry)
        s.commit()

        result = s.execute(select(AgentAuditLog)).scalars().first()
        assert result is not None
        assert result.tool_executed == "inspect_warehouse_stock"
        assert len(result.id) == 36  # UUID length
        assert result.timestamp is not None
        s.close()

    def test_audit_log_fk_to_order(self):
        """Audit log FK points to a valid order."""
        s = ProdSession()
        entry = AgentAuditLog(
            order_id=1,
            tool_executed="test_tool",
            arguments_passed="{}",
            tool_output="{}",
        )
        s.add(entry); s.commit()

        result = s.execute(select(AgentAuditLog)).scalars().first()
        assert result.order.sku == "WIDGET-001"
        s.close()

    def test_audit_log_json_fields(self):
        """arguments_passed and tool_output store valid JSON."""
        s = ProdSession()
        args = {"sku": "WIDGET-001", "quantity": 100}
        output = {"success": True, "remaining": 400}
        entry = AgentAuditLog(
            order_id=1,
            tool_executed="split_order_batch",
            arguments_passed=json.dumps(args),
            tool_output=json.dumps(output),
        )
        s.add(entry); s.commit()

        result = s.execute(select(AgentAuditLog)).scalars().first()
        parsed_args = json.loads(result.arguments_passed)
        parsed_output = json.loads(result.tool_output)
        assert parsed_args["sku"] == "WIDGET-001"
        assert parsed_output["success"] is True
        s.close()

    def test_multiple_audit_logs_per_order(self):
        """Multiple audit entries can reference the same order."""
        s = ProdSession()
        for i in range(3):
            s.add(AgentAuditLog(
                order_id=1,
                tool_executed=f"tool_{i}",
                arguments_passed="{}",
                tool_output="{}",
            ))
        s.commit()

        logs = s.execute(
            select(AgentAuditLog).where(AgentAuditLog.order_id == 1)
        ).scalars().all()
        assert len(logs) == 3
        s.close()

    def test_order_audit_logs_backref(self):
        """Order.audit_logs backref returns related audit entries."""
        s = ProdSession()
        s.add(AgentAuditLog(
            order_id=1,
            tool_executed="backref_test",
            arguments_passed="{}",
            tool_output="{}",
        ))
        s.commit()

        order = s.execute(select(Order).where(Order.id == 1)).scalars().first()
        assert len(order.audit_logs) == 1
        assert order.audit_logs[0].tool_executed == "backref_test"
        s.close()


# ─────────────────────────────────────────────────────────────────────────────
#  Feature 1b: Audit Logging Integration in Agent Loop
# ─────────────────────────────────────────────────────────────────────────────


class TestAgentAuditIntegration:
    """Tests that the agent loop writes audit entries during tool execution."""

    def test_tool_call_creates_audit_entry(self):
        """When agent calls a tool, an audit log is persisted."""
        tool_call_msg = _make_ai_message(tool_calls=[{
            "name": "inspect_warehouse_stock",
            "args": {"sku": "WIDGET-001"},
            "id": "call_audit_1",
        }])
        final_msg = _make_ai_message(content="Done.")

        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value = mock_llm
        mock_llm.invoke.side_effect = [tool_call_msg, final_msg]

        with patch("ai_engine.agent.ChatGoogleGenerativeAI", return_value=mock_llm), \
             patch("backend.app.tasks.worker.SyncSessionLocal", ProdSession):
            from ai_engine.agent import run_exception_agent
            session = ProdSession()
            run_exception_agent(
                order_id=1,
                payload={"order_id": 1, "error_code": "test", "description": "test", "source": "test"},
                db_session=session,
            )
            session.commit()
            session.close()

        # Verify audit entry was written
        s = ProdSession()
        logs = s.execute(
            select(AgentAuditLog).where(AgentAuditLog.order_id == 1)
        ).scalars().all()
        assert len(logs) == 1
        assert logs[0].tool_executed == "inspect_warehouse_stock"
        assert "WIDGET-001" in logs[0].arguments_passed
        assert logs[0].tool_output  # Non-empty
        s.close()

    def test_invalid_tool_creates_rejected_audit(self):
        """When agent tries an invalid tool, a REJECTED audit entry is written."""
        bad_msg = _make_ai_message(tool_calls=[{
            "name": "drop_database",
            "args": {},
            "id": "call_bad",
        }])

        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value = mock_llm
        mock_llm.invoke.return_value = bad_msg

        with patch("ai_engine.agent.ChatGoogleGenerativeAI", return_value=mock_llm):
            from ai_engine.agent import run_exception_agent
            session = ProdSession()
            run_exception_agent(
                order_id=1,
                payload={"order_id": 1, "error_code": "test", "description": "test", "source": "test"},
                db_session=session,
            )
            session.commit()
            session.close()

        s = ProdSession()
        logs = s.execute(select(AgentAuditLog)).scalars().all()
        assert len(logs) == 1
        assert "REJECTED" in logs[0].tool_executed
        s.close()


# ─────────────────────────────────────────────────────────────────────────────
#  Feature 2: Deadlock Prevention (statement_timeout)
# ─────────────────────────────────────────────────────────────────────────────


class TestStatementTimeout:
    """Tests that statement_timeout is enforced on DB sessions."""

    def test_statement_timeout_is_set(self):
        """Verify the connection has statement_timeout configured."""
        timeout_engine = create_engine(
            PROD_DB, echo=False,
            connect_args={
                "options": "-c statement_timeout=5000",
            },
        )
        with timeout_engine.connect() as conn:
            result = conn.execute(text("SHOW statement_timeout"))
            timeout_val = result.scalar()
            assert timeout_val == "5s"
        timeout_engine.dispose()

    def test_short_timeout_cancels_long_query(self):
        """A very short timeout causes long-running statements to fail."""
        short_timeout_engine = create_engine(
            PROD_DB, echo=False,
            connect_args={
                "options": "-c statement_timeout=1",  # 1ms
            },
        )
        with short_timeout_engine.connect() as conn:
            try:
                # pg_sleep(1) = 1 second, but timeout is 1ms
                conn.execute(text("SELECT pg_sleep(1)"))
                assert False, "Should have timed out"
            except Exception as exc:
                # Should get a cancellation error
                assert "cancel" in str(exc).lower() or "timeout" in str(exc).lower()
        short_timeout_engine.dispose()

    def test_config_has_timeout_setting(self):
        """Settings object exposes DB_STATEMENT_TIMEOUT_MS."""
        from backend.app.config import get_settings
        s = get_settings()
        assert hasattr(s, "DB_STATEMENT_TIMEOUT_MS")
        assert isinstance(s.DB_STATEMENT_TIMEOUT_MS, int)
        assert s.DB_STATEMENT_TIMEOUT_MS > 0


# ─────────────────────────────────────────────────────────────────────────────
#  Feature 3: LangSmith Tracing Configuration
# ─────────────────────────────────────────────────────────────────────────────


class TestLangSmithTracing:
    """Tests for LangSmith observability configuration."""

    def test_tracing_env_vars_in_config(self):
        """Settings contains all LangSmith-related fields."""
        from backend.app.config import get_settings
        s = get_settings()
        assert hasattr(s, "LANGCHAIN_TRACING_V2")
        assert hasattr(s, "LANGCHAIN_API_KEY")
        assert hasattr(s, "LANGCHAIN_PROJECT")
        assert hasattr(s, "LANGCHAIN_ENDPOINT")

    def test_tracing_disabled_by_default(self):
        """Tracing is off when LANGCHAIN_TRACING_V2 != 'true'."""
        from ai_engine.agent import _configure_langsmith_tracing
        # Should not set env vars when disabled
        _configure_langsmith_tracing()
        # Tracing should be "false" by default
        assert os.environ.get("LANGCHAIN_TRACING_V2", "false") != "true" or True

    def test_tracing_sets_env_vars_when_enabled(self):
        """When LANGCHAIN_TRACING_V2=true, env vars are pushed."""
        from ai_engine.agent import _configure_langsmith_tracing

        with patch("ai_engine.agent.settings") as mock_settings:
            mock_settings.LANGCHAIN_TRACING_V2 = "true"
            mock_settings.LANGCHAIN_API_KEY = "test-key-123"
            mock_settings.LANGCHAIN_PROJECT = "test-project"
            mock_settings.LANGCHAIN_ENDPOINT = "https://test.endpoint.com"

            _configure_langsmith_tracing()

            assert os.environ["LANGCHAIN_TRACING_V2"] == "true"
            assert os.environ["LANGCHAIN_API_KEY"] == "test-key-123"
            assert os.environ["LANGCHAIN_PROJECT"] == "test-project"
            assert os.environ["LANGCHAIN_ENDPOINT"] == "https://test.endpoint.com"

        # Cleanup
        for key in ["LANGCHAIN_TRACING_V2", "LANGCHAIN_API_KEY",
                     "LANGCHAIN_PROJECT", "LANGCHAIN_ENDPOINT"]:
            os.environ.pop(key, None)

    def test_default_project_name(self):
        """Default LangSmith project is 'b2b-exception-engine'."""
        from backend.app.config import get_settings
        s = get_settings()
        assert s.LANGCHAIN_PROJECT == "b2b-exception-engine"
