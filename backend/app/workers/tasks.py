from app.workers.celery_app import QUEUE_AI, QUEUE_RENDER, celery_app


@celery_app.task(name="system.ping_ai", queue=QUEUE_AI)
def ping_ai() -> str:
    return "pong"


@celery_app.task(name="system.ping_render", queue=QUEUE_RENDER)
def ping_render() -> str:
    return "pong"
