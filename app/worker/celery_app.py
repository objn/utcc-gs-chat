from celery import Celery

celery = Celery("gs_chat")
celery.config_from_object({
    "broker_url": "redis://redis:6379/0",
    "result_backend": "redis://redis:6379/0",
})
