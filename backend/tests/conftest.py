import os

# Settings are read at import time by the Celery app; give tests harmless values
# before any `app.*` module is imported. Nothing here connects to a real service.
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://test:test@localhost:1/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:1/0")
