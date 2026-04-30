from celery import Celery
from config import settings

celery_app = Celery(
    "compatibilidades",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "tasks.import_tasks",
        "tasks.product_resolution_tasks",
        "tasks.compatibility_batch_tasks",
        "tasks.compatibility_exception_tasks",
        "tasks.price_stock_tasks",
        "tasks.process_queue_tasks",
    ],
)

celery_app.conf.update(
    task_ignore_result=False,
    task_track_started=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    result_expires=7 * 24 * 60 * 60,
    broker_connection_retry_on_startup=True,
    task_default_queue="compat_dispatch",
    task_routes={
        "tasks.process_excel_job": {"queue": "compat_dispatch"},
        "tasks.process_excel_chunk": {"queue": "compat_chunks"},
        "tasks.finalize_excel_job": {"queue": "compat_chunks"},
        "tasks.resolve_products_job": {"queue": "compat_dispatch"},
        "tasks.add_compatibilities_batch_job": {"queue": "compat_chunks"},
        "tasks.process_compatibility_exceptions_job": {"queue": "compat_chunks"},
        "tasks.process_price_stock_job": {"queue": "compat_dispatch"},
        "tasks.run_process_queue_job": {"queue": "compat_dispatch"},
    },
)

# Import explicito para asegurar el registro de tareas cuando el worker inicia.
# Celery no recarga automaticamente tareas nuevas en workers ya levantados.
import tasks.import_tasks  # noqa: E402,F401
import tasks.product_resolution_tasks  # noqa: E402,F401
import tasks.compatibility_batch_tasks  # noqa: E402,F401
import tasks.compatibility_exception_tasks  # noqa: E402,F401
import tasks.price_stock_tasks  # noqa: E402,F401
import tasks.process_queue_tasks  # noqa: E402,F401
