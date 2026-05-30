"""
Phase 2 Tests — FastAPI Webhook & Redis Stream
================================================
Tests the POST /api/webhooks/ endpoint with a real Redis backend.
Validates schema enforcement, 202 response, tracking IDs, and
that payloads actually land in the Redis Stream.
"""

from __future__ import annotations

import json

import pytest
import redis
from fastapi.testclient import TestClient

from backend.app.config import get_settings
from backend.main import app

settings = get_settings()

client = TestClient(app)

# ── Sync Redis client for verifying stream contents ─────────────────────────
redis_client = redis.Redis(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    db=settings.REDIS_DB,
    decode_responses=True,
)


@pytest.fixture(autouse=True)
def clean_redis_stream():
    """Flush the test stream before and after each test."""
    redis_client.delete(settings.REDIS_STREAM_NAME)
    yield
    redis_client.delete(settings.REDIS_STREAM_NAME)


class TestWebhookEndpoint:
    """Tests for POST /api/webhooks/"""

    VALID_PAYLOAD = {
        "order_id": 1,
        "error_code": "quantity_exceeds_single_warehouse",
        "description": "Order 1 requires 600 WIDGET-001 but WH_A only has 500",
        "source": "distribution_logger_v2",
    }

    def test_valid_payload_returns_202(self):
        """A valid payload gets a 202 Accepted response."""
        resp = client.post("/api/webhooks/", json=self.VALID_PAYLOAD)
        assert resp.status_code == 202

    def test_response_contains_tracking_id(self):
        """Response includes a UUID tracking_id."""
        resp = client.post("/api/webhooks/", json=self.VALID_PAYLOAD)
        body = resp.json()
        assert "tracking_id" in body
        assert len(body["tracking_id"]) == 36  # UUID length
        assert body["status"] == "accepted"

    def test_response_contains_message(self):
        """Response includes the queued message."""
        resp = client.post("/api/webhooks/", json=self.VALID_PAYLOAD)
        body = resp.json()
        assert "queued" in body["message"].lower() or "processing" in body["message"].lower()

    def test_payload_lands_in_redis_stream(self):
        """After POST, the payload exists in the Redis Stream."""
        resp = client.post("/api/webhooks/", json=self.VALID_PAYLOAD)
        tracking_id = resp.json()["tracking_id"]

        # Read from the stream
        entries = redis_client.xrange(settings.REDIS_STREAM_NAME)
        assert len(entries) >= 1

        # Find our entry
        latest = entries[-1]
        msg_id, fields = latest
        assert fields["tracking_id"] == tracking_id

        # Verify the payload is valid JSON
        payload_data = json.loads(fields["payload"])
        assert payload_data["order_id"] == 1
        assert payload_data["error_code"] == "quantity_exceeds_single_warehouse"

    def test_multiple_payloads_create_multiple_stream_entries(self):
        """Each POST creates a separate stream entry."""
        for i in range(3):
            payload = {**self.VALID_PAYLOAD, "order_id": i + 1}
            client.post("/api/webhooks/", json=payload)

        entries = redis_client.xrange(settings.REDIS_STREAM_NAME)
        assert len(entries) == 3

    def test_each_request_gets_unique_tracking_id(self):
        """Every request gets a distinct tracking_id."""
        ids = set()
        for _ in range(5):
            resp = client.post("/api/webhooks/", json=self.VALID_PAYLOAD)
            ids.add(resp.json()["tracking_id"])
        assert len(ids) == 5

    def test_payload_with_metadata(self):
        """Payload with optional metadata field is accepted."""
        payload = {
            **self.VALID_PAYLOAD,
            "metadata": {"warehouse": "WH_A", "retry_count": 2},
        }
        resp = client.post("/api/webhooks/", json=payload)
        assert resp.status_code == 202

        entries = redis_client.xrange(settings.REDIS_STREAM_NAME)
        payload_data = json.loads(entries[-1][1]["payload"])
        assert payload_data["metadata"]["warehouse"] == "WH_A"


class TestWebhookValidation:
    """Tests for Pydantic schema validation on the webhook endpoint."""

    def test_missing_order_id_returns_422(self):
        """Missing required field order_id → 422."""
        payload = {
            "error_code": "test_error",
            "description": "Something broke",
            "source": "test_logger",
        }
        resp = client.post("/api/webhooks/", json=payload)
        assert resp.status_code == 422

    def test_invalid_order_id_zero_returns_422(self):
        """order_id=0 violates gt=0 constraint → 422."""
        payload = {
            "order_id": 0,
            "error_code": "test_error",
            "description": "Something broke",
            "source": "test_logger",
        }
        resp = client.post("/api/webhooks/", json=payload)
        assert resp.status_code == 422

    def test_negative_order_id_returns_422(self):
        """Negative order_id → 422."""
        payload = {
            "order_id": -5,
            "error_code": "test_error",
            "description": "Something broke",
            "source": "test_logger",
        }
        resp = client.post("/api/webhooks/", json=payload)
        assert resp.status_code == 422

    def test_missing_error_code_returns_422(self):
        """Missing error_code → 422."""
        payload = {
            "order_id": 1,
            "description": "Something broke",
            "source": "test_logger",
        }
        resp = client.post("/api/webhooks/", json=payload)
        assert resp.status_code == 422

    def test_empty_description_returns_422(self):
        """Empty description violates min_length=1 → 422."""
        payload = {
            "order_id": 1,
            "error_code": "test",
            "description": "",
            "source": "test_logger",
        }
        resp = client.post("/api/webhooks/", json=payload)
        assert resp.status_code == 422

    def test_missing_source_returns_422(self):
        """Missing source → 422."""
        payload = {
            "order_id": 1,
            "error_code": "test",
            "description": "Something broke",
        }
        resp = client.post("/api/webhooks/", json=payload)
        assert resp.status_code == 422

    def test_empty_body_returns_422(self):
        """Completely empty body → 422."""
        resp = client.post("/api/webhooks/", json={})
        assert resp.status_code == 422

    def test_non_json_body_returns_422(self):
        """Non-JSON body → 422."""
        resp = client.post(
            "/api/webhooks/",
            content="not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 422

    def test_no_payload_in_stream_on_validation_failure(self):
        """Failed validation must NOT produce a stream entry."""
        client.post("/api/webhooks/", json={"order_id": -1})
        entries = redis_client.xrange(settings.REDIS_STREAM_NAME)
        assert len(entries) == 0


class TestHealthEndpoint:
    """Tests for the /health liveness probe."""

    def test_health_returns_200(self):
        """Health endpoint returns 200."""
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_health_response_body(self):
        """Health endpoint returns correct JSON."""
        resp = client.get("/health")
        body = resp.json()
        assert body["status"] == "healthy"
        assert body["service"] == "b2b-exception-engine"
