"""
Phase 5 — Custom Agent Control Loop (with Observability + Audit Logging)
==========================================================================
Manual while-loop agent using ChatGoogleGenerativeAI (gemini-2.5-flash).
No pre-built black-box agents.  Explicit conversation state management
with a hard cap of 3 tool-calling iterations.

Production hardening:
  • LangSmith tracing: every prompt, tool call, and token count is
    streamed to the LangSmith dashboard when LANGCHAIN_TRACING_V2=true.
  • AgentAuditLog: every tool execution is persisted to a dedicated
    immutable audit table for enterprise compliance.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_engine.tools.invoice_tools import recalculate_invoice_tier
from ai_engine.tools.order_tools import split_order_batch
from ai_engine.tools.warehouse_tools import inspect_warehouse_stock
from backend.app.config import get_settings
from backend.app.models.db import AgentAuditLog, Order, OrderStatus

logger = logging.getLogger(__name__)
settings = get_settings()

# ── Tool registry ────────────────────────────────────────────────────────────
TOOLS = [
    inspect_warehouse_stock,
    split_order_batch,
    recalculate_invoice_tier,
]

TOOL_MAP: dict[str, Any] = {t.name: t for t in TOOLS}

# ── System prompt ────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are a B2B distribution exception-resolution agent.
You receive error telemetry about broken orders and must resolve them
using ONLY the tools provided.

Rules:
1. First inspect warehouse stock for the order's SKU.
2. If stock is split across warehouses, use split_order_batch to allocate.
3. If there is a pricing mismatch, use recalculate_invoice_tier.
4. Never fabricate data.  Only use tool outputs for decisions.
5. You have a MAXIMUM of 3 tool calls.  Be efficient.
6. After resolving, respond with a brief summary of actions taken.
"""


def _configure_langsmith_tracing() -> None:
    """
    Push LangSmith env vars into os.environ so the LangChain runtime
    picks them up automatically.  No-op if tracing is disabled.
    """
    if settings.LANGCHAIN_TRACING_V2.lower() == "true":
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_API_KEY"] = settings.LANGCHAIN_API_KEY
        os.environ["LANGCHAIN_PROJECT"] = settings.LANGCHAIN_PROJECT
        os.environ["LANGCHAIN_ENDPOINT"] = settings.LANGCHAIN_ENDPOINT
        logger.info(
            "[Agent] LangSmith tracing ENABLED → project='%s'",
            settings.LANGCHAIN_PROJECT,
        )
    else:
        logger.info("[Agent] LangSmith tracing disabled.")


def _write_audit_log(
    db_session: Session,
    order_id: int,
    tool_name: str,
    arguments: dict,
    output: str,
) -> None:
    """
    Persist an immutable audit row for a single tool execution.
    This row is never updated or deleted — append-only by design.
    """
    try:
        audit_entry = AgentAuditLog(
            order_id=order_id,
            tool_executed=tool_name,
            arguments_passed=json.dumps(arguments, default=str),
            tool_output=output,
        )
        db_session.add(audit_entry)
        db_session.flush()
        logger.info(
            "[Audit] Logged tool execution | order_id=%s | tool=%s | audit_id=%s",
            order_id, tool_name, audit_entry.id[:8],
        )
    except Exception as exc:
        # Audit logging must never break the main execution flow
        logger.error(
            "[Audit] FAILED to write audit log | order_id=%s | tool=%s | err=%s",
            order_id, tool_name, exc,
        )


def run_exception_agent(
    order_id: int,
    payload: dict[str, Any],
    db_session: Session,
) -> dict[str, Any]:
    """
    Execute the custom agent loop for a single order exception.

    Args:
        order_id:   The broken order's database ID.
        payload:    The raw error telemetry dict.
        db_session: An active SQLAlchemy session (inside a transaction).

    Returns:
        A dict summarizing the agent's resolution actions.
    """
    max_iterations: int = settings.AGENT_MAX_ITERATIONS

    logger.info(
        "[Agent] Initializing | order_id=%s | max_iterations=%d",
        order_id, max_iterations,
    )

    # ── Enable LangSmith if configured ───────────────────────────────
    _configure_langsmith_tracing()

    # Initialize LLM with strict tool binding
    llm = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        google_api_key=settings.GOOGLE_API_KEY,
        temperature=0.0,  # deterministic triage
        max_retries=1,
        convert_system_message_to_human=True,
    )
    llm_with_tools = llm.bind_tools(TOOLS)

    # ── Build initial message array ──────────────────────────────────
    user_prompt = (
        f"Resolve the following order exception:\n"
        f"Order ID: {order_id}\n"
        f"Error Code: {payload.get('error_code', 'unknown')}\n"
        f"Description: {payload.get('description', 'No description')}\n"
        f"Source: {payload.get('source', 'unknown')}\n"
        f"Metadata: {json.dumps(payload.get('metadata', {}))}"
    )

    messages: list = [
        HumanMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ]

    iteration: int = 0
    actions_taken: list[str] = []

    # ── Manual while loop — NO pre-built agents ─────────────────────
    while iteration < max_iterations:
        iteration += 1
        logger.info("[Agent] Iteration %d/%d", iteration, max_iterations)

        # Call the model
        response: AIMessage = llm_with_tools.invoke(messages)
        messages.append(response)

        # Check if the model wants to call tools
        if not response.tool_calls:
            logger.info(
                "[Agent] Model returned final response (no tool calls) "
                "at iteration %d",
                iteration,
            )
            break

        # Process each tool call in this turn
        for tool_call in response.tool_calls:
            tool_name: str = tool_call["name"]
            tool_args: dict = tool_call["args"]
            tool_call_id: str = tool_call["id"]

            logger.info(
                "[Agent] Tool call: %s(%s) | call_id=%s",
                tool_name, tool_args, tool_call_id,
            )

            # ── Safety: reject unknown tools ─────────────────────
            if tool_name not in TOOL_MAP:
                logger.error(
                    "[Agent] INVALID TOOL '%s' requested by model. "
                    "Breaking execution chain.",
                    tool_name,
                )
                # Audit the rejected call
                _write_audit_log(
                    db_session, order_id, f"REJECTED:{tool_name}",
                    tool_args, '{"error": "invalid_tool_rejected"}',
                )
                _flag_order_hold(order_id, db_session, f"invalid_tool:{tool_name}")
                return {
                    "status": "exceptional_hold",
                    "reason": f"Model attempted invalid tool: {tool_name}",
                    "actions": actions_taken,
                }

            # ── Execute the tool ─────────────────────────────────
            try:
                tool_fn = TOOL_MAP[tool_name]
                tool_result = tool_fn.invoke(tool_args)
                result_str = json.dumps(tool_result, default=str)
            except Exception as exc:
                logger.exception(
                    "[Agent] Tool execution failed: %s | err=%s",
                    tool_name, exc,
                )
                result_str = json.dumps({"error": str(exc)})

            # ── Write immutable audit log ────────────────────────
            _write_audit_log(
                db_session, order_id, tool_name, tool_args, result_str,
            )

            actions_taken.append(f"{tool_name}({tool_args}) → {result_str}")

            # Append the tool result to the conversation
            messages.append(
                ToolMessage(
                    content=result_str,
                    tool_call_id=tool_call_id,
                )
            )

            logger.info(
                "[Agent] Tool result for %s: %s",
                tool_name, result_str[:500],
            )

    # ── Check if we exhausted iterations (possible infinite loop) ────
    if iteration >= max_iterations and response.tool_calls:
        logger.warning(
            "[Agent] Max iterations (%d) reached with pending tool calls. "
            "Flagging order as EXCEPTIONAL_HOLD.",
            max_iterations,
        )
        _flag_order_hold(order_id, db_session, "max_iterations_exceeded")
        return {
            "status": "exceptional_hold",
            "reason": "Agent exceeded max iterations",
            "iterations": iteration,
            "actions": actions_taken,
        }

    # ── Extract final model summary ──────────────────────────────────
    final_text = ""
    if messages and hasattr(messages[-1], "content"):
        final_text = messages[-1].content or ""

    logger.info(
        "[Agent] Completed | order_id=%s | iterations=%d | actions=%d | summary=%s",
        order_id, iteration, len(actions_taken), final_text[:300],
    )

    return {
        "status": "resolved",
        "iterations": iteration,
        "actions": actions_taken,
        "model_summary": final_text,
    }


def _flag_order_hold(
    order_id: int,
    session: Session,
    reason: str,
) -> None:
    """
    Flag an order as EXCEPTIONAL_HOLD and log the reason.
    Uses the existing session (within the transactional block).
    """
    order = session.execute(
        select(Order).where(Order.id == order_id)
    ).scalars().first()

    if order:
        order.status = OrderStatus.EXCEPTIONAL_HOLD
        order.error_log = json.dumps({
            "hold_reason": reason,
            "flagged_by": "agent_control_loop",
        })
        logger.warning(
            "[Agent] Order %s flagged as EXCEPTIONAL_HOLD | reason=%s",
            order_id, reason,
        )
