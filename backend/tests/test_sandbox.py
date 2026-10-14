"""The renderer's HTTP interface and the worker's client, with fake deck and video builds."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.config import get_settings
from app.render.skill import SkillError, build_deck, build_video
from app.sandbox import server
from app.sandbox.deck import DeckBuild, RenderError
from app.sandbox.video import VideoBuild


@pytest.fixture
def renderer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[server.Settings, Path]]:
    spool = tmp_path / "spool"
    spool.mkdir()
    settings = server.Settings()
    settings.spool = spool
    settings.slots = threading.BoundedSemaphore(1)
    httpd = server.make_server("127.0.0.1", 0, settings)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(get_settings(), "renderer_url", f"http://127.0.0.1:{httpd.server_port}")
    monkeypatch.setattr(get_settings(), "spool_dir", spool)
    yield settings, spool
    httpd.shutdown()
    httpd.server_close()


def fake_build(slides: int = 2) -> Any:
    def build(
        spec: str, theme: dict[str, Any], logo: bytes | None, workdir: Path, **_: Any
    ) -> DeckBuild:
        out = workdir / "out"
        out.mkdir(parents=True)
        (out / "deck.pptx").write_bytes(b"PK deck")
        previews = []
        for n in range(1, slides + 1):
            (out / f"slide-{n:02d}.png").write_bytes(b"png")
            previews.append(out / f"slide-{n:02d}.png")
        build.seen = (spec, theme, logo)  # type: ignore[attr-defined]
        return DeckBuild(out / "deck.pptx", previews, [], {"layout_ms": 5.0}, ["Шрифта нет"])

    return build


def job_dir(spool: Path) -> Path:
    return spool / f"{uuid.uuid4()}-1"


def test_deck_is_built_in_the_renderer_and_read_from_the_spool(
    renderer: tuple[server.Settings, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, spool = renderer
    build = fake_build()
    monkeypatch.setattr(server, "build_deck", build)
    workdir = job_dir(spool)
    result = build_deck('{"slides": []}', {"fonts": {}}, b"\x89PNG", workdir)
    assert result.pptx == workdir / "out" / "deck.pptx" and result.pptx.read_bytes() == b"PK deck"
    assert [p.name for p in result.previews] == ["slide-01.png", "slide-02.png"]
    assert result.font_warnings == ["Шрифта нет"] and result.timings == {"layout_ms": 5.0}
    assert build.seen == ('{"slides": []}', {"fonts": {}}, b"\x89PNG")


def test_render_failure_reaches_the_user_in_plain_words(
    renderer: tuple[server.Settings, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, spool = renderer

    def failing(*_: Any, **__: Any) -> DeckBuild:
        raise RenderError("Сборка презентации заняла слишком много времени")

    monkeypatch.setattr(server, "build_deck", failing)
    with pytest.raises(SkillError, match="слишком много времени"):
        build_deck("{}", {}, None, job_dir(spool))


def test_a_busy_renderer_says_so(
    renderer: tuple[server.Settings, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, spool = renderer
    monkeypatch.setattr(server, "build_deck", fake_build())
    assert settings.slots.acquire(blocking=False)  # the only slot is taken
    try:
        with pytest.raises(SkillError, match="занят"):
            build_deck("{}", {}, None, job_dir(spool))
    finally:
        settings.slots.release()


def test_bad_job_names_and_bodies_are_refused(
    renderer: tuple[server.Settings, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, spool = renderer
    monkeypatch.setattr(server, "build_deck", fake_build())
    for name in ("../etc", "x" * 10, f"{uuid.uuid4()}"):
        with pytest.raises(SkillError, match="Не удалось собрать"):
            build_deck("{}", {}, None, spool / name)
    assert sorted(p.name for p in spool.iterdir()) == []  # nothing written outside


def test_unexpected_file_names_from_the_renderer_are_not_trusted(
    renderer: tuple[server.Settings, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, spool = renderer

    def sneaky(*_: Any, workdir: Path | None = None, **__: Any) -> dict[str, Any]:
        return {"pptx": "../../data/other.pptx", "previews": [], "issues": [], "timings": {}}

    monkeypatch.setattr(server, "render_deck", lambda settings, body: sneaky())
    with pytest.raises(SkillError, match="Не удалось собрать"):
        build_deck("{}", {}, None, job_dir(spool))


def test_unreachable_renderer_is_a_readable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "renderer_url", "http://127.0.0.1:9")  # nothing listens
    with pytest.raises(SkillError, match="недоступен"):
        build_deck("{}", {}, None, tmp_path / f"{uuid.uuid4()}-1")


def test_video_is_recorded_in_the_renderer(
    renderer: tuple[server.Settings, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, spool = renderer

    def record(
        spec: str, theme: dict[str, Any], logo: bytes | None, workdir: Path, **kw: Any
    ) -> VideoBuild:
        out = workdir / "out"
        out.mkdir(parents=True)
        (out / "video.mp4").write_bytes(b"mp4")
        (out / "scene-01.png").write_bytes(b"png")
        record.seen = (spec, logo, kw["timeout_s"])  # type: ignore[attr-defined]
        return VideoBuild(out / "video.mp4", [out / "scene-01.png"], 12.5, {"record_ms": 9.0})

    monkeypatch.setattr(server, "build_video", record)
    workdir = job_dir(spool)
    result = build_video('{"scenes": []}', {}, None, workdir)
    assert result.video == workdir / "out" / "video.mp4" and result.duration == 12.5
    assert [p.name for p in result.posters] == ["scene-01.png"]
    assert record.seen == ('{"scenes": []}', None, 900)


def test_unexpected_video_names_are_not_trusted(
    renderer: tuple[server.Settings, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, spool = renderer
    answer = {"video": "out/video.mp4", "posters": ["../x/scene-01.png"], "duration": 1}
    monkeypatch.setattr(server, "render_video", lambda settings, body: answer)
    with pytest.raises(SkillError, match="Не удалось записать видео"):
        build_video("{}", {}, None, job_dir(spool))
