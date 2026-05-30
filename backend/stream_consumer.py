"""
Stream Consumer CLI
====================
Entry point to run the Redis stream consumer that dispatches
entries to the Celery task queue.

Usage:
    python -m backend.stream_consumer
"""

from __future__ import annotations

import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    stream=sys.stdout,
)

if __name__ == "__main__":
    from backend.app.tasks.worker import consume_redis_stream
    consume_redis_stream()
