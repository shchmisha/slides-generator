# Генератор презентаций и роликов

Веб-сервис, который с помощью ИИ превращает текст и документы (.docx, .xlsx, .pptx) в презентацию PPTX или анимационный ролик MP4 (HyperFrames).

> Статус: модуль 0 — каркас и инфраструктура. Документация дополняется по мере готовности модулей.

## Запуск

```bash
cp .env.example .env        # впишите LLM_API_KEY и LLM_MODEL
docker compose up --build
```

Откройте http://localhost:8080 (порт меняется переменной `WEB_PORT`).

## Сервисы

| Сервис | Что делает |
|---|---|
| `web` | nginx: статический Vue-интерфейс и прокси `/api` на тот же origin. Единственный опубликованный порт |
| `api` | FastAPI: авторизация, загрузки, сценарии, задания |
| `worker-ai` | «ИИ-модуль»: разбор файлов и обращения к LLM (очередь `ai`) |
| `worker-render` | задания сборки PPTX и записи MP4 (очередь `render`): готовит сценарий, отдаёт его `renderer` и сохраняет файлы |
| `renderer` | песочница: headless Chromium, FFmpeg и навыки `pptx-builder` / `video-builder`. Без секретов, в сети без выхода наружу |
| `migrate` | применяет миграции Alembic и завершается до старта API |
| `postgres` | данные, история запусков, статусы заданий |
| `redis` | брокер очереди Celery |

## Разработка

```bash
cd backend && uv sync && uv run ruff check . && uv run mypy && uv run pytest
cd frontend && npm ci && npm run lint && npm run typecheck && npm test
```

Документы: [ASSUMPTIONS.md](ASSUMPTIONS.md), [журнал решений](docs/decisions.md).
