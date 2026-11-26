"""Celery app and RabbitMQ topology.

`ingest` is a quorum queue whose dead-letter exchange routes rejected messages to
`ingest.dlq` (declared on worker start, never consumed by workers). Tasks ack late,
so a worker crash redelivers; a poison message is rejected without requeue and lands
in the DLQ. `x-delivery-limit` caps redeliveries of a message that keeps killing
workers (it is dead-lettered after that).
"""

from __future__ import annotations

from celery import Celery
from celery.signals import worker_ready
from kombu import Exchange, Queue  # type: ignore[attr-defined]

from apps.api.settings import Settings, get_settings

INGEST_QUEUE = "ingest"
DLQ = "ingest.dlq"
DLX = "ingest.dlx"
DELIVERY_LIMIT = 5


def ingest_queue() -> Queue:
    return Queue(
        INGEST_QUEUE,
        Exchange(INGEST_QUEUE, type="direct", durable=True),
        routing_key=INGEST_QUEUE,
        durable=True,
        queue_arguments={
            "x-queue-type": "quorum",
            "x-dead-letter-exchange": DLX,
            "x-dead-letter-routing-key": DLQ,
            "x-delivery-limit": DELIVERY_LIMIT,
        },
    )


def dead_letter_queue() -> Queue:
    return Queue(
        DLQ,
        Exchange(DLX, type="direct", durable=True),
        routing_key=DLQ,
        durable=True,
        queue_arguments={"x-queue-type": "quorum"},
    )


def declare_topology(app: Celery) -> None:
    """Declare both queues. The DLQ is not in task_queues so no worker ever consumes it."""
    with app.connection_for_write() as conn:
        for q in (ingest_queue(), dead_letter_queue()):
            q.bind(conn.default_channel).declare()


def make_app(settings: Settings | None = None) -> Celery:
    s = settings or get_settings()
    app = Celery("pap-ingest", broker=s.broker_url.get_secret_value())
    app.conf.update(
        task_queues=[ingest_queue()],
        task_default_queue=INGEST_QUEUE,
        task_routes={"pap.ingest_document": {"queue": INGEST_QUEUE}},
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        task_serializer="json",
        accept_content=["json"],
        result_backend=None,
        task_ignore_result=True,
        broker_connection_retry_on_startup=True,
        # Quorum queues do not support global QoS; Celery detects and switches to per-consumer.
        worker_detect_quorum_queues=True,
    )
    app.conf.task_create_missing_queues = False

    @worker_ready.connect(weak=False)
    def _declare(sender: object = None, **_: object) -> None:
        if getattr(sender, "app", None) is app:  # signal is global; only our own worker
            declare_topology(app)

    return app


app = make_app()

import apps.ingest.tasks  # noqa: E402,F401  (registers shared tasks)
