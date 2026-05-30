"""
Celery application factory.
"""

from __future__ import annotations

import logging

from celery import Celery

from backend.app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

celery_app = Celery(
    "b2b_exception_engine",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)

celery_app.autodiscover_tasks(["backend.app.tasks"])

# Explicitly import worker so @celery_app.task is registered
import backend.app.tasks.worker

logger.info("Celery app initialized | broker=%s", settings.CELERY_BROKER_URL)
