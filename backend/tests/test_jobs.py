"""Jobs: claiming and fencing, recovery of lost messages and dead workers, and the plan and
render jobs end to end (with a scripted model and a fake skill)."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.jobs import queue
from app.jobs.plan import run_plan
from app.jobs.render import run_render, video_spec
from app.jobs.store import JobLostError, claim, mark_failed, now, report_progress
from app.llm.client import LLMError
from app.models import Artifact, Job, Project, Scenario, StoredFile, User
from app.render.skill import DeckBuild, SkillError, VideoBuild
from app.storage import artifact_path, logo_path, project_dir, upload_path, write_file

from .api_support import FakeCounter
from .fakes import ScriptedModel

FIXTURES = Path(__file__).parent / "fixtures"
BOARD = (FIXTURES / "board_scenario.json").read_text()
SOURCE = (
    "Суперсервис — это 12 услуг в одном окне. Заказ оформляется за 5 минут, "
    "доставка работает в 1 200 городах."
)
VIDEO = json.dumps(
    {
        "kind": "video",
        "title": "Что такое Суперсервис",
        "scenes": [
            {"duration_s": 12, "narration": n, "visual": {"type": "statement"}}
            for n in (
                "Суперсервис — это 12 услуг в одном окне.",
                "Заказ оформляется за 5 минут.",
                "Доставка работает в 1 200 городах.",
                "Всё в одном месте.",
                "Без лишних звонков.",
                "Попробуйте сами.",
            )
        ],
    },
    ensure_ascii=False,
)

Sessions = sessionmaker[Session]


def project_with_files(
    sessions: Sessions, kind: str = "deck", files: tuple[tuple[str, str, bytes], ...] | None = None
) -> uuid.UUID:
    if files is None:
        files = tuple(
            (name, name.rsplit(".", 1)[1], (FIXTURES / name).read_bytes())
            for name in ("q3_metrics.xlsx", "manager_comments.docx")
        )
    with sessions() as db:
        user = User(email=f"{uuid.uuid4().hex[:8]}@example.com", password_hash="x")
        db.add(user)
        db.flush()
        project = Project(user_id=user.id, title="Отчёт", kind=kind, settings={})
        db.add(project)
        db.flush()
        for name, file_kind, data in files:
            stored = StoredFile(
                id=uuid.uuid4(),
                project_id=project.id,
                user_id=user.id,
                filename=name,
                kind=file_kind,
                size=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
            )
            write_file(upload_path(project.id, stored.id, file_kind), data)
            db.add(stored)
        db.commit()
        return project.id


def new_job(
    sessions: Sessions, project_id: uuid.UUID, kind: str = "plan", scenario: uuid.UUID | None = None
) -> uuid.UUID:
    with sessions() as db:
        project = db.get(Project, project_id)
        assert project is not None
        job = Job(
            id=uuid.uuid4(),
            project_id=project_id,
            user_id=project.user_id,
            type=kind,
            status="queued",
            progress=0,
            stage="в очереди",
            scenario_id=scenario,
        )
        db.add(job)
        db.commit()
        return job.id


def job_row(sessions: Sessions, job_id: uuid.UUID) -> Job:
    with sessions() as db:
        job = db.get(Job, job_id)
        assert job is not None
        return job


def planned(sessions: Sessions) -> tuple[uuid.UUID, uuid.UUID]:
    """A deck project with a model-written scenario version 1."""
    project_id = project_with_files(sessions)
    job_id = new_job(sessions, project_id)
    run_plan(job_id, sessions, lambda: ScriptedModel(BOARD))
    scenario_id = job_row(sessions, job_id).scenario_id
    assert scenario_id is not None
    return project_id, scenario_id


# ----------------------------------------------------------------------------- claim & fencing


def test_a_job_is_claimed_once(session_factory: Sessions, data_dir: Path) -> None:
    job_id = new_job(session_factory, project_with_files(session_factory))
    with session_factory() as db:
        first = claim(db, job_id, "старт")
        second = claim(db, job_id, "старт")
    assert first is not None and first.attempt == 1
    assert second is None
    assert job_row(session_factory, job_id).status == "running"


def test_a_worker_that_lost_its_job_cannot_write(session_factory: Sessions, data_dir: Path) -> None:
    job_id = new_job(session_factory, project_with_files(session_factory))
    with session_factory() as db:
        mine = claim(db, job_id, "старт")
        assert mine is not None
        # Presumed dead: the job went back to the queue and another worker took it.
        db.execute(update(Job).where(Job.id == job_id).values(status="queued"))
        db.commit()
        theirs = claim(db, job_id, "старт")
        assert theirs is not None and theirs.attempt == 2
        with pytest.raises(JobLostError):
            report_progress(db, mine, 50, "старый воркер")
        mark_failed(db, mine, "старый воркер упал")
    job = job_row(session_factory, job_id)
    assert job.status == "running" and job.attempts == 2 and job.error is None


# ------------------------------------------------------------------------------------ recovery


def test_jobs_lost_with_a_redis_restart_are_sent_again(
    session_factory: Sessions, data_dir: Path, sent: list[Any]
) -> None:
    redis = FakeCounter()
    epoch = queue.current_epoch(redis)
    project = project_with_files(session_factory)
    never_sent = new_job(session_factory, project)
    with session_factory() as db:
        other = project_with_files(session_factory)
        old = new_job(session_factory, other)
        fresh = new_job(session_factory, project_with_files(session_factory))
        db.execute(update(Job).where(Job.id == old).values(enqueued_epoch="before-restart"))
        db.execute(update(Job).where(Job.id == fresh).values(enqueued_epoch=epoch))
        db.commit()
        assert queue.recover(db, redis) == 2
    assert {args[0] for _, args, _ in sent} == {str(never_sent), str(old)}
    assert job_row(session_factory, old).enqueued_epoch == epoch
    with session_factory() as db:  # throttled: a second look within the interval does nothing
        assert queue.recover(db, redis) == 0


def test_jobs_of_dead_workers_are_requeued_then_failed(
    session_factory: Sessions, data_dir: Path, sent: list[Any]
) -> None:
    redis = FakeCounter()
    first, second, alive = (
        new_job(session_factory, project_with_files(session_factory)) for _ in range(3)
    )
    long_ago = now() - timedelta(minutes=10)
    with session_factory() as db:
        for job_id, attempts, beat in (
            (first, 1, long_ago),
            (second, 2, long_ago),
            (alive, 1, now()),
        ):
            db.execute(
                update(Job)
                .where(Job.id == job_id)
                .values(status="running", attempts=attempts, heartbeat_at=beat)
            )
        db.commit()
        queue.recover(db, redis, force=True)
    assert job_row(session_factory, first).status == "queued"
    assert [args[0] for _, args, _ in sent] == [str(first)]
    gave_up = job_row(session_factory, second)
    assert gave_up.status == "failed" and gave_up.error and "прерывалась" in gave_up.error
    assert job_row(session_factory, alive).status == "running"


def test_publish_records_the_epoch_and_survives_a_broker_outage(
    session_factory: Sessions, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = new_job(session_factory, project_with_files(session_factory))

    def down(*_: Any, **__: Any) -> None:
        raise ConnectionError("broker down")

    monkeypatch.setattr("app.jobs.queue.celery_app.send_task", down)
    with session_factory() as db:
        job = db.get(Job, job_id)
        assert job is not None
        assert queue.publish(db, FakeCounter(), job) is False
    assert job_row(session_factory, job_id).enqueued_epoch is None  # recover() will resend it


# ------------------------------------------------------------------------------------ plan job


def test_plan_job_writes_scenario_version_one(session_factory: Sessions, data_dir: Path) -> None:
    project_id = project_with_files(session_factory)
    job_id = new_job(session_factory, project_id)
    run_plan(job_id, session_factory, lambda: ScriptedModel(BOARD))
    job = job_row(session_factory, job_id)
    assert job.status == "succeeded" and job.progress == 100, job.error
    assert {"parse_ms", "facts_ms", "llm_ms", "total_ms"} <= set(job.timings)
    assert job.report["attempts"][0]["latency_ms"] == 10
    with session_factory() as db:
        scenario = db.scalar(select(Scenario).where(Scenario.id == job.scenario_id))
        assert scenario is not None
        assert (scenario.version, scenario.source) == (1, "model")
        assert scenario.basis["kind"] == "deck" and scenario.basis["tables"]
        assert scenario.report["preview"]["slides"]


def test_video_plan_keeps_the_source_text_as_its_basis(
    session_factory: Sessions, data_dir: Path
) -> None:
    project_id = project_with_files(
        session_factory, kind="video", files=(("Суперсервис", "text", SOURCE.encode()),)
    )
    job_id = new_job(session_factory, project_id)
    run_plan(job_id, session_factory, lambda: ScriptedModel(VIDEO))
    job = job_row(session_factory, job_id)
    assert job.status == "succeeded", job.error
    with session_factory() as db:
        scenario = db.scalar(select(Scenario).where(Scenario.id == job.scenario_id))
        assert scenario is not None
        assert scenario.basis["source_text"] == SOURCE
        assert scenario.report["seconds"] == 72


@pytest.mark.parametrize(
    ("replies", "message"),
    [
        (("не JSON",) * 3, "Модель не смогла составить корректный сценарий"),
        (LLMError("Модель не ответила вовремя: попробуйте ещё раз"), "не ответила вовремя"),
    ],
)
def test_plan_failures_are_explained(
    session_factory: Sessions, data_dir: Path, replies: Any, message: str
) -> None:
    class Failing:
        def complete(self, *_: Any, **__: Any) -> Any:
            raise replies

    model = Failing if isinstance(replies, Exception) else (lambda: ScriptedModel(*replies))
    job_id = new_job(session_factory, project_with_files(session_factory))
    run_plan(job_id, session_factory, model)
    job = job_row(session_factory, job_id)
    assert job.status == "failed" and job.error and message in job.error
    assert job.scenario_id is None


def test_unreadable_file_names_the_file(session_factory: Sessions, data_dir: Path) -> None:
    project_id = project_with_files(
        session_factory, files=(("broken.docx", "docx", b"PK\x03\x04 not really a zip"),)
    )
    job_id = new_job(session_factory, project_id)
    run_plan(job_id, session_factory, lambda: ScriptedModel(BOARD))
    job = job_row(session_factory, job_id)
    assert job.status == "failed" and job.error and "«broken.docx»" in job.error


def test_redelivered_message_does_not_run_the_job_twice(
    session_factory: Sessions, data_dir: Path
) -> None:
    job_id = new_job(session_factory, project_with_files(session_factory))
    run_plan(job_id, session_factory, lambda: ScriptedModel(BOARD))
    calls: list[int] = []
    run_plan(job_id, session_factory, lambda: calls.append(1) or ScriptedModel(BOARD))  # type: ignore[func-returns-value]
    assert calls == []
    with session_factory() as db:
        assert len(db.scalars(select(Scenario)).all()) == 1


def test_project_deleted_during_planning_stops_quietly(
    session_factory: Sessions, data_dir: Path
) -> None:
    project_id = project_with_files(session_factory)
    job_id = new_job(session_factory, project_id)

    class DeletesProject(ScriptedModel):
        def complete(self, *args: Any, **kwargs: Any) -> Any:
            with session_factory() as db:
                project = db.get(Project, project_id)
                db.delete(project)
                db.commit()
            return super().complete(*args, **kwargs)

    run_plan(job_id, session_factory, lambda: DeletesProject(BOARD))
    with session_factory() as db:
        assert db.get(Job, job_id) is None
        assert db.scalars(select(Scenario)).all() == []


# ---------------------------------------------------------------------------------- render job


def fake_build(slides: int = 3) -> Any:
    def build(spec: str, theme: dict[str, Any], logo: bytes | None, workdir: Path) -> DeckBuild:
        assert json.loads(spec)["slides"]  # the resolved deck, numbers filled in
        out = workdir / "out"
        out.mkdir(parents=True)
        (out / "deck.pptx").write_bytes(b"PK pptx")
        previews = []
        for number in range(1, slides + 1):
            preview = out / f"slide-{number:02d}.png"
            preview.write_bytes(b"png")
            previews.append(preview)
        build.calls.append((theme, logo))  # type: ignore[attr-defined]
        return DeckBuild(out / "deck.pptx", previews, [], {"layout_ms": 900.0, "pptx_ms": 80.0})

    build.calls = []  # type: ignore[attr-defined]
    return build


def test_render_job_stores_the_deck_and_previews(session_factory: Sessions, data_dir: Path) -> None:
    project_id, scenario_id = planned(session_factory)
    job_id = new_job(session_factory, project_id, "render", scenario_id)
    build = fake_build()
    run_render(job_id, session_factory, build)
    job = job_row(session_factory, job_id)
    assert job.status == "succeeded", job.error
    assert job.report["scenario_version"] == 1 and job.timings["layout_ms"] == 900
    with session_factory() as db:
        artifacts = db.scalars(select(Artifact).order_by(Artifact.kind, Artifact.position)).all()
    assert [(a.kind, a.position) for a in artifacts] == [
        ("pptx", 0),
        ("preview", 1),
        ("preview", 2),
        ("preview", 3),
    ]
    assert artifact_path(project_id, artifacts[0].id, "pptx").read_bytes() == b"PK pptx"
    assert not list((data_dir / "spool").glob("*"))
    theme, logo = build.calls[0]
    assert theme["fonts"]["body"] == "Arial" and logo is None  # default theme, no logo


def test_render_uses_the_project_brand_and_logo(session_factory: Sessions, data_dir: Path) -> None:
    project_id, scenario_id = planned(session_factory)
    theme = {"name": "x", "colors": {"primary": "#123456"}, "fonts": {"heading": "Georgia"}}
    write_file(logo_path(project_id), b"\x89PNG logo")
    with session_factory() as db:
        db.execute(
            update(Project)
            .where(Project.id == project_id)
            .values(brand=theme, logo_sha256=hashlib.sha256(b"\x89PNG logo").hexdigest())
        )
        db.commit()
    build = fake_build()
    run_render(new_job(session_factory, project_id, "render", scenario_id), session_factory, build)
    assert build.calls[0] == (theme, b"\x89PNG logo")


def test_render_failure_leaves_no_files(session_factory: Sessions, data_dir: Path) -> None:
    project_id, scenario_id = planned(session_factory)
    job_id = new_job(session_factory, project_id, "render", scenario_id)

    def broken(*_: Any) -> DeckBuild:
        raise SkillError("Не удалось собрать презентацию")

    run_render(job_id, session_factory, broken)
    job = job_row(session_factory, job_id)
    assert job.status == "failed" and job.error == "Не удалось собрать презентацию"
    with session_factory() as db:
        assert db.scalars(select(Artifact)).all() == []
    assert not list((data_dir / "spool").glob("*"))


def planned_video(sessions: Sessions) -> tuple[uuid.UUID, uuid.UUID]:
    project_id = project_with_files(
        sessions, kind="video", files=(("Суперсервис", "text", SOURCE.encode()),)
    )
    job_id = new_job(sessions, project_id)
    run_plan(job_id, sessions, lambda: ScriptedModel(VIDEO))
    scenario_id = job_row(sessions, job_id).scenario_id
    assert scenario_id is not None
    return project_id, scenario_id


def fake_record(progress: list[float] | None = None, hook: Any = None) -> Any:
    def record(spec: str, theme: dict[str, Any], logo: bytes | None, workdir: Path) -> VideoBuild:
        scenes = json.loads(spec)["scenes"]
        out = workdir / "out"
        out.mkdir(parents=True)
        for value in progress or []:
            (out / "progress.json").write_text(json.dumps({"progress": value, "stage": "x"}))
            time.sleep(0.12)
        (out / "video.mp4").write_bytes(b"mp4 video")
        posters = []
        for number in range(1, len(scenes) + 1):
            poster = out / f"scene-{number:02d}.png"
            poster.write_bytes(b"png")
            posters.append(poster)
        if hook:
            hook(out)
        record.specs.append(json.loads(spec))  # type: ignore[attr-defined]
        last = scenes[-1]
        return VideoBuild(out / "video.mp4", posters, last["start"] + last["duration"], {})

    record.specs = []  # type: ignore[attr-defined]
    return record


def test_video_spec_places_each_scene_on_the_timeline() -> None:
    spec = video_spec(json.loads(VIDEO) | {"scenes": json.loads(VIDEO)["scenes"][:3]})
    assert [(s["start"], s["duration"]) for s in spec["scenes"]] == [(0, 12), (12, 12), (24, 12)]
    assert spec["title"] == "Что такое Суперсервис"
    assert spec["scenes"][0]["visual"] == {"type": "statement"}


def test_render_job_records_the_video_and_posters(
    session_factory: Sessions, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.jobs.render.PROGRESS_EVERY_S", 0.05)
    seen: list[tuple[int, str]] = []
    real = report_progress

    def spy(db: Session, claim: Any, progress: int, stage: str) -> None:
        seen.append((progress, stage))
        real(db, claim, progress, stage)

    monkeypatch.setattr("app.jobs.render.report_progress", spy)
    project_id, scenario_id = planned_video(session_factory)
    job_id = new_job(session_factory, project_id, "render", scenario_id)
    record = fake_record(progress=[20, 60, 98])
    run_render(job_id, session_factory, fake_build(), record)
    job = job_row(session_factory, job_id)
    assert job.status == "succeeded", job.error
    assert job.report == {
        "scenes": 6,
        "seconds": 72.0,
        "starts": [0, 12, 24, 36, 48, 60],
        "warnings": [],
        "scenario_version": 1,
    }
    with session_factory() as db:
        artifacts = db.scalars(select(Artifact).order_by(Artifact.kind, Artifact.position)).all()
    assert [(a.kind, a.position) for a in artifacts][:2] == [("mp4", 0), ("preview", 1)]
    assert len(artifacts) == 7
    assert artifact_path(project_id, artifacts[0].id, "mp4").read_bytes() == b"mp4 video"
    assert record.specs[0]["scenes"][-1]["start"] == 60
    # The script's progress, mapped into the job's range, and only ever forward.
    percents = [p for p, _ in seen]
    assert percents == sorted(percents) and 22 in percents and 56 in percents
    assert (88, "кадры для предпросмотра") in seen
    assert not list((data_dir / "spool").glob("*"))


def test_links_left_by_the_renderer_are_not_followed(
    session_factory: Sessions, data_dir: Path
) -> None:
    """A compromised renderer could leave a link to another project's file in the spool."""
    victim = data_dir / "projects" / "someone-else.mp4"
    victim.parent.mkdir(parents=True, exist_ok=True)
    victim.write_bytes(b"private")

    def swap(out: Path) -> None:
        (out / "video.mp4").unlink()
        (out / "video.mp4").symlink_to(victim)

    project_id, scenario_id = planned_video(session_factory)
    job_id = new_job(session_factory, project_id, "render", scenario_id)
    run_render(job_id, session_factory, fake_build(), fake_record(hook=swap))
    job = job_row(session_factory, job_id)
    assert job.status == "failed"
    with session_factory() as db:
        assert db.scalars(select(Artifact)).all() == []
    assert victim.read_bytes() == b"private"


def test_tampered_logo_is_refused(session_factory: Sessions, data_dir: Path) -> None:
    project_id, scenario_id = planned(session_factory)
    write_file(logo_path(project_id), b"something else")
    with session_factory() as db:
        db.execute(update(Project).where(Project.id == project_id).values(logo_sha256="0" * 64))
        db.commit()
    job_id = new_job(session_factory, project_id, "render", scenario_id)
    run_render(job_id, session_factory, fake_build())
    error = job_row(session_factory, job_id).error
    assert error is not None and "логотип" in error.lower()


# ------------------------------------------------------------------ review findings, regressions


def test_tasks_retry_instead_of_dropping_a_job_when_the_database_is_down() -> None:
    from sqlalchemy.exc import OperationalError

    from app.workers.tasks import plan, render

    for task in (plan, render):
        assert OperationalError in task.autoretry_for
        assert task.max_retries >= 10


def test_a_job_queued_for_too_long_is_sent_again_in_the_same_epoch(
    session_factory: Sessions, data_dir: Path, sent: list[Any]
) -> None:
    redis = FakeCounter()
    epoch = queue.current_epoch(redis)
    job_id = new_job(session_factory, project_with_files(session_factory))
    with session_factory() as db:
        long_ago = now() - timedelta(seconds=queue.RESEND_AFTER_S + 60)
        db.execute(
            update(Job).where(Job.id == job_id).values(enqueued_epoch=epoch, enqueued_at=long_ago)
        )
        db.commit()
        assert queue.recover(db, redis, force=True) == 1
    assert [args[0] for _, args, _ in sent] == [str(job_id)]


def test_failure_is_recorded_through_a_fresh_session_and_never_raises(
    session_factory: Sessions, data_dir: Path
) -> None:
    from app.jobs.store import fail

    job_id = new_job(session_factory, project_with_files(session_factory))
    with session_factory() as db:
        claimed = claim(db, job_id, "старт")
    assert claimed is not None
    fail(session_factory, claimed, "Сломалось")
    assert job_row(session_factory, job_id).error == "Сломалось"

    def broken() -> Session:
        raise ConnectionError("database down")

    fail(broken, claimed, "не записать")  # type: ignore[arg-type]  # logged, not raised


def test_scratch_of_dead_renders_is_swept_on_startup(
    session_factory: Sessions, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.storage import scratch_dir
    from app.workers import tasks

    first, second = project_with_files(session_factory), project_with_files(session_factory)
    dead, alive = (
        new_job(session_factory, first, "render"),
        new_job(session_factory, second, "render"),
    )
    with session_factory() as db:
        db.execute(update(Job).where(Job.id == dead).values(status="failed"))
        db.execute(update(Job).where(Job.id == alive).values(status="running", heartbeat_at=now()))
        db.commit()
    scratch_dir(dead, 1).mkdir(parents=True)
    scratch_dir(alive, 1).mkdir(parents=True)
    monkeypatch.setattr(tasks, "get_session_factory", lambda: session_factory)
    monkeypatch.setattr(tasks, "get_redis", lambda: FakeCounter())
    tasks._recover_jobs()
    assert not scratch_dir(dead, 1).exists()
    assert scratch_dir(alive, 1).exists()


def test_only_the_last_renders_keep_their_files(session_factory: Sessions, data_dir: Path) -> None:
    from app.jobs.render import KEEP_RENDERS

    project_id, scenario_id = planned(session_factory)
    for _ in range(KEEP_RENDERS + 2):
        job_id = new_job(session_factory, project_id, "render", scenario_id)
        run_render(job_id, session_factory, fake_build(slides=1))
    with session_factory() as db:
        kept = {a.job_id for a in db.scalars(select(Artifact))}
        files = list((project_dir(project_id) / "artifacts").iterdir())
    assert len(kept) == KEEP_RENDERS and len(files) == KEEP_RENDERS * 2


def test_stored_scenario_with_an_aliased_value_still_renders(
    session_factory: Sessions, data_dir: Path
) -> None:
    # Postgres JSONB sorts object keys: "q" comes back before "rev_fact" it refers to.
    board = json.loads(BOARD)
    board["values"] = {"q": {"name": "rev_fact"}, **board["values"]}
    board["slides"][0]["title"] = "Выручка {q}"
    project_id = project_with_files(session_factory)
    job_id = new_job(session_factory, project_id)
    run_plan(job_id, session_factory, lambda: ScriptedModel(json.dumps(board, ensure_ascii=False)))
    plan_job = job_row(session_factory, job_id)
    assert plan_job.status == "succeeded", plan_job.error
    render_id = new_job(session_factory, project_id, "render", plan_job.scenario_id)
    run_render(render_id, session_factory, fake_build())
    assert job_row(session_factory, render_id).status == "succeeded"


@pytest.mark.skipif(
    not __import__("os").environ.get("TEST_DATABASE_URL"), reason="row locks need Postgres"
)
def test_upload_during_plan_save_does_not_deadlock(
    session_factory: Sessions, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    import app.api.projects as projects_api

    project_id = project_with_files(session_factory)
    job_id = new_job(session_factory, project_id)
    upload_locked = threading.Event()
    plan_done = threading.Event()

    class Gated(ScriptedModel):
        def complete(self, *args: Any, **kwargs: Any) -> Any:
            upload_locked.wait(10)  # the reply arrives while the upload holds its locks
            return super().complete(*args, **kwargs)

    real_write = projects_api.write_file

    def slow_write(path: Path, data: bytes) -> None:
        upload_locked.set()
        plan_done.wait(5)  # the plan saves while this upload is mid-transaction
        real_write(path, data)

    monkeypatch.setattr(projects_api, "write_file", slow_write)

    def plan() -> None:
        run_plan(job_id, session_factory, lambda: Gated(BOARD))
        plan_done.set()

    worker = threading.Thread(target=plan)
    worker.start()
    with session_factory() as db:
        project = db.get(Project, project_id)
        assert project is not None
        user = db.get(User, project.user_id)
        assert user is not None
        projects_api._store(db, user, project, b"more text", "note.txt", "text")
    worker.join(30)
    assert job_row(session_factory, job_id).status == "succeeded"
