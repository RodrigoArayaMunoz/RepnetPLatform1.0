"""Retired task names retained only to acknowledge messages already in Redis.

Do not restore sales/webhook processing here. Removing these registrations before
old deliveries and retries have drained would leave unknown tasks in the broker.
Neither task makes external calls, publishes retries, or stores task results.
"""

from celery_app import celery_app


@celery_app.task(
    name="tasks.process_meli_notification",
    max_retries=0,
    ignore_result=True,
    store_errors_even_if_ignored=False,
    track_started=False,
    acks_late=False,
)
def process_meli_notification_task(*args, **kwargs) -> None:
    """Discard legacy notification messages, including scheduled retries."""


@celery_app.task(
    name="tasks.backfill_meli_sales",
    max_retries=0,
    ignore_result=True,
    store_errors_even_if_ignored=False,
    track_started=False,
    acks_late=False,
)
def backfill_meli_sales_task(*args, **kwargs) -> None:
    """Discard legacy manual sales-sync messages without contacting services."""
