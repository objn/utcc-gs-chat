from celery import Celery

from app.core.env import load_env

load_env()

from app.core.redis import get_redis_url

celery = Celery("gs_chat")
celery.config_from_object({
    "broker_url": get_redis_url(),
    "result_backend": get_redis_url(),
    "imports": ("app.worker.tasks",),
})
