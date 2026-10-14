"""The generation API: start, follow, review and edit, render, download; brand and logo."""

from __future__ import annotations

import copy
import io
import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.jobs.plan import run_plan
from app.jobs.render import run_render
from app.models import Job

from .api_support import new_client, signed_up
from .fakes import ScriptedModel
from .test_brand import CLIENT_BRAND, png
from .test_jobs import BOARD, fake_build

FIXTURES = Path(__file__).parent / "fixtures"
Sessions = sessionmaker[Session]


def deck_project(client: Any, kind: str = "deck", with_files: bool = True) -> str:
    project = str(
        client.post("/api/projects", json={"title": "Отчёт: Q3", "kind": kind}).json()["id"]
    )
    if with_files:
        for name in ("q3_metrics.xlsx", "manager_comments.docx"):
            resp = client.post(
                f"/api/projects/{project}/files",
                files={"file": (name, (FIXTURES / name).read_bytes())},
            )
            assert resp.status_code == 201, resp.text
    return project


def planned(client: Any, sessions: Sessions) -> str:
    project = deck_project(client)
    resp = client.post(f"/api/projects/{project}/plan")
    assert resp.status_code == 202, resp.text
    run_plan(uuid.UUID(resp.json()["id"]), sessions, lambda: ScriptedModel(BOARD))
    return project


def test_plan_review_render_download(app: Any, session_factory: Sessions, sent: list[Any]) -> None:
    client = signed_up(app)
    project = deck_project(client)

    resp = client.post(f"/api/projects/{project}/plan")
    assert resp.status_code == 202
    job = resp.json()
    assert (job["type"], job["status"], job["stage"]) == ("plan", "queued", "в очереди")
    assert sent == [("jobs.plan", [job["id"]], "ai")]

    run_plan(uuid.UUID(job["id"]), session_factory, lambda: ScriptedModel(BOARD))
    done = client.get(f"/api/jobs/{job['id']}").json()
    assert done["status"] == "succeeded" and done["progress"] == 100
    assert done["scenario_version"] == 1 and done["timings"]["llm_ms"] >= 0

    scenario = client.get(f"/api/projects/{project}/scenario").json()
    assert (scenario["version"], scenario["source"]) == (1, "model")
    assert scenario["report"]["preview"]["slides"]

    resp = client.post(f"/api/projects/{project}/render", json={})
    assert resp.status_code == 202
    render = resp.json()
    assert sent[-1] == ("jobs.render", [render["id"]], "render")
    run_render(uuid.UUID(render["id"]), session_factory, fake_build(slides=2))
    assert client.get(f"/api/jobs/{render['id']}").json()["scenario_version"] == 1

    artifacts = client.get(f"/api/projects/{project}/artifacts").json()
    assert [a["kind"] for a in artifacts] == ["pptx", "preview", "preview"]
    deck = client.get(artifacts[0]["url"])
    assert deck.status_code == 200 and deck.content == b"PK pptx"
    assert deck.headers["content-type"].startswith("application/vnd.openxmlformats")
    disposition = deck.headers["content-disposition"]
    assert disposition.startswith("attachment") and ".pptx" in disposition
    # "Отчёт: Q3" → "Отчёт Q3.pptx": no reserved characters, no doubled spaces.
    assert disposition.endswith("%D0%9E%D1%82%D1%87%D1%91%D1%82%20Q3.pptx")
    preview = client.get(artifacts[1]["url"])
    assert preview.headers["content-type"] == "image/png"
    assert preview.headers["content-disposition"].startswith("inline")

    history = client.get(f"/api/projects/{project}/jobs").json()
    assert [j["type"] for j in history] == ["render", "plan"]
    assert len(client.get("/api/jobs").json()) == 2


def test_nothing_to_plan_and_nothing_to_render(app: Any) -> None:
    client = signed_up(app)
    empty = deck_project(client, with_files=False)
    assert client.post(f"/api/projects/{empty}/plan").status_code == 409
    assert client.post(f"/api/projects/{empty}/render").status_code == 409
    video = deck_project(client, kind="video", with_files=False)
    resp = client.post(f"/api/projects/{video}/render")
    assert resp.status_code == 409 and "сценарий" in resp.json()["detail"]


def test_one_generation_at_a_time_per_project(app: Any) -> None:
    client = signed_up(app)
    project = deck_project(client)
    assert client.post(f"/api/projects/{project}/plan").status_code == 202
    resp = client.post(f"/api/projects/{project}/plan")
    assert resp.status_code == 409 and "уже идёт" in resp.json()["detail"]


def test_active_generations_per_user_are_capped(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "max_active_jobs_per_user", 1)
    client = signed_up(app)
    first, second = deck_project(client), deck_project(client)
    assert client.post(f"/api/projects/{first}/plan").status_code == 202
    assert client.post(f"/api/projects/{second}/plan").status_code == 429


def test_jobs_scenarios_and_files_of_others_are_invisible(
    app: Any, session_factory: Sessions
) -> None:
    anna = signed_up(app, "anna@example.com")
    boris = signed_up(app, "boris@example.com")
    project = planned(anna, session_factory)
    job = anna.get(f"/api/projects/{project}/jobs").json()[0]["id"]
    render = anna.post(f"/api/projects/{project}/render").json()["id"]
    run_render(uuid.UUID(render), session_factory, fake_build())
    artifact = anna.get(f"/api/projects/{project}/artifacts").json()[0]["url"]

    assert boris.get(f"/api/jobs/{job}").status_code == 404
    assert boris.get("/api/jobs").json() == []
    assert boris.get(f"/api/projects/{project}/jobs").status_code == 404
    assert boris.get(f"/api/projects/{project}/scenario").status_code == 404
    assert (
        boris.put(
            f"/api/projects/{project}/scenario", json={"base_version": 1, "content": {}}
        ).status_code
        == 404
    )
    assert boris.post(f"/api/projects/{project}/plan").status_code == 404
    assert boris.post(f"/api/projects/{project}/render").status_code == 404
    assert boris.get(f"/api/projects/{project}/artifacts").status_code == 404
    assert boris.get(artifact).status_code == 404
    assert new_client(app).get(artifact).status_code == 401


def test_editing_creates_checked_versions(app: Any, session_factory: Sessions) -> None:
    client = signed_up(app)
    project = planned(client, session_factory)
    original = client.get(f"/api/projects/{project}/scenario").json()

    edited = copy.deepcopy(original["content"])
    edited["slides"][0]["title"] = "Итоги квартала"
    resp = client.put(
        f"/api/projects/{project}/scenario", json={"base_version": 1, "content": edited}
    )
    assert resp.status_code == 201, resp.text
    assert (resp.json()["version"], resp.json()["source"]) == (2, "user")
    assert resp.json()["report"]["preview"]["slides"][0]["title"] == "Итоги квартала"

    stale = client.put(
        f"/api/projects/{project}/scenario", json={"base_version": 1, "content": edited}
    )
    assert stale.status_code == 409 and "версия 2" in stale.json()["detail"]

    typed = copy.deepcopy(edited)
    typed["slides"][0]["takeaways"][0] = "Выручка выросла на 47 % за квартал."
    rejected = client.put(
        f"/api/projects/{project}/scenario", json={"base_version": 2, "content": typed}
    )
    assert rejected.status_code == 422
    assert any("47" in error for error in rejected.json()["errors"])

    versions = client.get(f"/api/projects/{project}/scenarios").json()
    assert [(v["version"], v["source"]) for v in versions] == [(2, "user"), (1, "model")]
    assert client.get(f"/api/projects/{project}/scenario?version=1").json() == original

    # Render an older version on purpose.
    render = client.post(f"/api/projects/{project}/render", json={"version": 1}).json()
    run_render(uuid.UUID(render["id"]), session_factory, fake_build())
    assert client.get(f"/api/jobs/{render['id']}").json()["scenario_version"] == 1


def test_polling_resends_a_job_lost_with_redis(
    app: Any, session_factory: Sessions, sent: list[Any]
) -> None:
    client = signed_up(app)
    project = deck_project(client)
    job = client.post(f"/api/projects/{project}/plan").json()["id"]
    with session_factory() as db:  # as if Redis restarted after the message was sent
        db.execute(update(Job).where(Job.id == uuid.UUID(job)).values(enqueued_epoch="old"))
        db.commit()
    client.get(f"/api/jobs/{job}")
    assert sent[-1] == ("jobs.plan", [job], "ai") and len(sent) == 2


def test_brand_and_logo(app: Any) -> None:
    client = signed_up(app)
    project = deck_project(client, with_files=False)

    resp = client.put(f"/api/projects/{project}/brand", json=CLIENT_BRAND)
    assert resp.status_code == 200
    assert resp.json()["theme"]["colors"]["primary"] == "#1F3A5F"
    assert client.get(f"/api/projects/{project}").json()["brand"]["fonts"]["heading"] == "Georgia"
    bad = client.put(f"/api/projects/{project}/brand", json={"colors": {"primary": "red;"}})
    assert bad.status_code == 422
    assert client.delete(f"/api/projects/{project}/brand").status_code == 204
    assert client.get(f"/api/projects/{project}").json()["brand"] is None

    logo = png(64, 32)
    put = client.put(f"/api/projects/{project}/logo", files={"file": ("logo.png", logo)})
    assert put.status_code == 204
    assert client.get(f"/api/projects/{project}").json()["has_logo"] is True
    stored = client.get(f"/api/projects/{project}/logo").content
    with Image.open(io.BytesIO(stored)) as image:  # re-encoded, same picture
        assert (image.format, image.size) == ("PNG", (64, 32))
    gif = client.put(f"/api/projects/{project}/logo", files={"file": ("logo.gif", b"GIF89a")})
    assert gif.status_code == 422 and "PNG" in gif.json()["detail"]
    assert client.delete(f"/api/projects/{project}/logo").status_code == 204
    assert client.get(f"/api/projects/{project}/logo").status_code == 404

    boris = signed_up(app, "boris@example.com")
    assert boris.put(f"/api/projects/{project}/brand", json=CLIENT_BRAND).status_code == 404
    assert boris.get(f"/api/projects/{project}/logo").status_code == 404


def test_version_numbers_beyond_the_column_are_refused(app: Any, session_factory: Sessions) -> None:
    client = signed_up(app)
    project = planned(client, session_factory)
    too_big = 2**31
    assert client.get(f"/api/projects/{project}/scenario?version={too_big}").status_code == 422
    assert (
        client.post(f"/api/projects/{project}/render", json={"version": too_big}).status_code == 422
    )
    resp = client.put(
        f"/api/projects/{project}/scenario", json={"base_version": too_big, "content": {}}
    )
    assert resp.status_code == 422


def test_edit_limit_counts_edits_since_the_last_plan(
    app: Any, session_factory: Sessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "max_scenario_versions", 2)
    client = signed_up(app)
    project = planned(client, session_factory)
    content = client.get(f"/api/projects/{project}/scenario").json()["content"]
    for base in (1, 2):
        put = client.put(
            f"/api/projects/{project}/scenario", json={"base_version": base, "content": content}
        )
        assert put.status_code == 201
    third = client.put(
        f"/api/projects/{project}/scenario", json={"base_version": 3, "content": content}
    )
    assert third.status_code == 409 and "заново" in third.json()["detail"]
    job = client.post(f"/api/projects/{project}/plan").json()["id"]
    run_plan(uuid.UUID(job), session_factory, lambda: ScriptedModel(BOARD))
    again = client.put(
        f"/api/projects/{project}/scenario", json={"base_version": 4, "content": content}
    )
    assert again.status_code == 201  # a new plan starts the count again


def test_lone_surrogate_in_an_edit_is_a_422(app: Any, session_factory: Sessions) -> None:
    client = signed_up(app)
    project = planned(client, session_factory)
    content = client.get(f"/api/projects/{project}/scenario").json()["content"]
    body = json.dumps({"base_version": 1, "content": content}).replace(
        '"title": "', '"title": "\\ud800', 1
    )
    resp = client.put(
        f"/api/projects/{project}/scenario",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 422


def test_render_is_refused_when_storage_is_full(
    app: Any, session_factory: Sessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = signed_up(app)
    project = planned(client, session_factory)
    monkeypatch.setattr(get_settings(), "max_bytes_per_user", 1)
    resp = client.post(f"/api/projects/{project}/render")
    assert resp.status_code == 409 and "Место" in resp.json()["detail"]
