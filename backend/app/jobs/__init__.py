"""Background jobs: state in Postgres (store), messages in Redis (queue), the work itself (plan,
render). Celery tasks in app.workers.tasks are thin wrappers around these functions."""
