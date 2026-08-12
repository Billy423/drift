"""The Celery application: broker, result backend, queues and routing."""

from __future__ import annotations

import os

from celery import Celery

celery_app = Celery(
    "drift",
    broker=os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0"),
    backend=os.environ.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/1"),
    # A live worker imports these so their tasks register.
    include=["drift.tasks.scan", "drift.tasks.cells"],
)

# A worker without -Q consumes only the default queue, so both Makefile targets pass one.
celery_app.conf.task_default_queue = "celery"
celery_app.conf.task_routes = {
    # The frame stays on the default queue: on the cells worker it would hold the
    # only slot and wait on cells that can never start.
    "drift.cells.run_cell": {"queue": "drift.cells"},
}
