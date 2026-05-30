"""
Phase 3 & 5 Tests — Worker + Agent Control Loop
=================================================
Tests the Celery worker logic and the custom agent while-loop.
Uses mocked LLM responses to avoid real Gemini API calls.
"""

from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.models.db import (
    Account, AccountTier, Base, Inventory,
    Order, OrderStatus, WarehouseCode,
)

AGENT_DB = "postgresql+psycopg2://vishvmunjapara@localhost:5432/b2b_exceptions_test"
agent_engine = create_engine(AGENT_DB, echo=False)
AgentSession = sessionmaker(bind=agent_engine)


@pytest.fixture(autouse=True)
def setup_agent_db():
    """Recreate tables + seed before each test."""
    Base.metadata.drop_all(agent_engine)
    Base.metadata.create_all(agent_engine)

    s = AgentSession()
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
    ])
    s.flush()

    s.add_all([
        Order(account_id=accts[0].id, sku="WIDGET-001", ordered_quantity=600,
              calculated_total=Decimal("11994.00"), status=OrderStatus.PENDING),
        Order(account_id=accts[1].id, sku="GADGET-002", ordered_quantity=200,
              calculated_total=Decimal("2898.00"), status=OrderStatus.PENDING),
    ])
    s.commit(); s.close()

    with patch("backend.app.tasks.worker.SyncSessionLocal", AgentSession):
        yield

    Base.metadata.drop_all(agent_engine)


def _make_ai_message(content="", tool_calls=None):
    """Helper to create a mock AIMessage."""
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls or []
    return msg


class TestAgentControlLoop:
    """Tests for the custom while-loop agent (Phase 5)."""

    def test_agent_returns_resolved_on_no_tool_calls(self):
        """If model replies without tool calls, agent returns 'resolved'."""
        mock_response = _make_ai_message(
            content="Order looks fine, no action needed."
        )
        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value = mock_llm
        mock_llm.invoke.return_value = mock_response

        with patch("ai_engine.agent.ChatGoogleGenerativeAI", return_value=mock_llm):
            from ai_engine.agent import run_exception_agent
            session = AgentSession()
            result = run_exception_agent(
                order_id=1,
                payload={"order_id": 1, "error_code": "test", "description": "test", "source": "test"},
                db_session=session,
            )
            session.close()

        assert result["status"] == "resolved"
        assert result["iterations"] == 1

    def test_agent_calls_tool_and_resolves(self):
        """Agent calls a tool once then resolves."""
        # First response: call inspect_warehouse_stock
        tool_call_msg = _make_ai_message(tool_calls=[{
            "name": "inspect_warehouse_stock",
            "args": {"sku": "WIDGET-001"},
            "id": "call_123",
        }])
        # Second response: final answer
        final_msg = _make_ai_message(content="Stock checked, order can be split.")

        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value = mock_llm
        mock_llm.invoke.side_effect = [tool_call_msg, final_msg]

        with patch("ai_engine.agent.ChatGoogleGenerativeAI", return_value=mock_llm), \
             patch("backend.app.tasks.worker.SyncSessionLocal", AgentSession):
            from ai_engine.agent import run_exception_agent
            session = AgentSession()
            result = run_exception_agent(
                order_id=1,
                payload={"order_id": 1, "error_code": "test", "description": "test", "source": "test"},
                db_session=session,
            )
            session.close()

        assert result["status"] == "resolved"
        assert len(result["actions"]) == 1
        assert "inspect_warehouse_stock" in result["actions"][0]

    def test_agent_rejects_invalid_tool(self):
        """If model calls a non-existent tool, agent flags EXCEPTIONAL_HOLD."""
        bad_tool_msg = _make_ai_message(tool_calls=[{
            "name": "delete_all_orders",
            "args": {},
            "id": "call_bad",
        }])

        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value = mock_llm
        mock_llm.invoke.return_value = bad_tool_msg

        with patch("ai_engine.agent.ChatGoogleGenerativeAI", return_value=mock_llm):
            from ai_engine.agent import run_exception_agent
            session = AgentSession()
            result = run_exception_agent(
                order_id=1,
                payload={"order_id": 1, "error_code": "test", "description": "test", "source": "test"},
                db_session=session,
            )
            session.close()

        assert result["status"] == "exceptional_hold"
        assert "invalid" in result["reason"].lower()

    def test_agent_max_iterations_enforced(self):
        """Agent stops after max_iterations and flags order."""
        # Every response tries to call a tool
        tool_msg = _make_ai_message(tool_calls=[{
            "name": "inspect_warehouse_stock",
            "args": {"sku": "WIDGET-001"},
            "id": "call_loop",
        }])

        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value = mock_llm
        mock_llm.invoke.return_value = tool_msg

        with patch("ai_engine.agent.ChatGoogleGenerativeAI", return_value=mock_llm), \
             patch("backend.app.tasks.worker.SyncSessionLocal", AgentSession), \
             patch("backend.app.config.get_settings") as mock_settings:
            mock_settings.return_value.AGENT_MAX_ITERATIONS = 3
            mock_settings.return_value.GEMINI_MODEL = "gemini-2.5-flash"
            mock_settings.return_value.GOOGLE_API_KEY = "fake"
            mock_settings.return_value.TIER_PRICING = {"STANDARD": "19.99", "WHOLESALE": "14.49", "VIP": "9.99"}

            from ai_engine.agent import run_exception_agent
            session = AgentSession()
            result = run_exception_agent(
                order_id=1,
                payload={"order_id": 1, "error_code": "test", "description": "test", "source": "test"},
                db_session=session,
            )
            session.close()

        assert result["status"] == "exceptional_hold"
        assert result["iterations"] == 3


class TestWorkerFlagExceptionalHold:
    """Tests for the _flag_exceptional_hold helper (Phase 3)."""

    def test_flag_sets_status(self):
        """_flag_exceptional_hold marks order as EXCEPTIONAL_HOLD."""
        with patch("backend.app.tasks.worker.SyncSessionLocal", AgentSession):
            from backend.app.tasks.worker import _flag_exceptional_hold
            _flag_exceptional_hold(1, "test_reason")

        s = AgentSession()
        order = s.execute(select(Order).where(Order.id == 1)).scalars().first()
        assert order.status == OrderStatus.EXCEPTIONAL_HOLD
        log = json.loads(order.error_log)
        assert log["hold_reason"] == "test_reason"
        s.close()

    def test_flag_nonexistent_order_no_crash(self):
        """Flagging a non-existent order does not raise."""
        with patch("backend.app.tasks.worker.SyncSessionLocal", AgentSession):
            from backend.app.tasks.worker import _flag_exceptional_hold
            # Should not raise
            _flag_exceptional_hold(9999, "ghost_order")


class TestWorkerTaskValidation:
    """Tests for the process_order_exception task input validation."""

    def test_malformed_json_returns_failed(self):
        """Malformed JSON payload returns failed status."""
        with patch("backend.app.tasks.worker.SyncSessionLocal", AgentSession):
            from backend.app.tasks.worker import process_order_exception
            result = process_order_exception(
                {"tracking_id": "test-123", "payload": "not-valid-json{{{"}
            )
        assert result["status"] == "failed"
        assert result["reason"] == "malformed_json"

    def test_invalid_order_id_returns_failed(self):
        """order_id <= 0 returns failed status."""
        with patch("backend.app.tasks.worker.SyncSessionLocal", AgentSession):
            from backend.app.tasks.worker import process_order_exception
            result = process_order_exception(
                {"tracking_id": "test-456", "payload": '{"order_id": 0}'}
            )
        assert result["status"] == "failed"
        assert result["reason"] == "invalid_order_id"

    def test_nonexistent_order_returns_failed(self):
        """Non-existent order_id returns order_not_found."""
        with patch("backend.app.tasks.worker.SyncSessionLocal", AgentSession):
            from backend.app.tasks.worker import process_order_exception
            result = process_order_exception(
                {"tracking_id": "test-789", "payload": '{"order_id": 9999}'}
            )
        assert result["status"] == "failed"
        assert result["reason"] == "order_not_found"

