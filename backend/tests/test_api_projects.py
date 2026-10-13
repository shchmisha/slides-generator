"""Projects and uploads: ownership on every resource, content checks, limits."""

import io
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import docx
import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session, sessionmaker

import app.api.projects as projects_api
from app.config import get_settings
from app.models import Project
from app.storage import write_file

from .api_support import new_client, signed_up

FIXTURES = Path(__file__).parent / "fixtures"


def make_docx(text: str = "Комментарий") -> bytes:
    document = docx.Document()
    document.add_paragraph(text)
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def create(client: Any, kind: str = "deck") -> str:
    resp = client.post("/api/projects", json={"title": "Отчёт за 3 квартал", "kind": kind})
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


def upload(client: Any, project: str, name: str, data: bytes) -> Any:
    return client.post(f"/api/projects/{project}/files", files={"file": (name, data)})


def test_project_lifecycle(app: Any) -> None:
    client = signed_up(app)
    project = create(client)
    assert client.get("/api/projects").json()[0]["id"] == project
    resp = client.patch(
        f"/api/projects/{project}", json={"title": "Новое имя", "settings": {"slides": 9}}
    )
    assert resp.json()["title"] == "Новое имя" and resp.json()["settings"]["slides"] == 9
    assert client.delete(f"/api/projects/{project}").status_code == 204
    assert client.get(f"/api/projects/{project}").status_code == 404


def test_settings_are_validated(app: Any) -> None:
    client = signed_up(app)
    project = create(client)
    resp = client.patch(f"/api/projects/{project}", json={"settings": {"slides": 99}})
    assert resp.status_code == 422
    assert "slides" in resp.json()["detail"]


def test_signed_out_requests_are_refused(app: Any) -> None:
    assert new_client(app).get("/api/projects").status_code == 401


def test_other_users_projects_and_files_are_invisible(app: Any) -> None:
    anna = signed_up(app, "anna@example.com")
    boris = signed_up(app, "boris@example.com")
    project = create(anna)
    file_id = upload(anna, project, "comments.docx", make_docx()).json()["id"]

    assert boris.get("/api/projects").json() == []
    assert boris.get(f"/api/projects/{project}").status_code == 404
    assert boris.patch(f"/api/projects/{project}", json={"title": "мой"}).status_code == 404
    assert upload(boris, project, "x.docx", make_docx()).status_code == 404
    assert boris.delete(f"/api/projects/{project}/files/{file_id}").status_code == 404
    assert boris.delete(f"/api/projects/{project}").status_code == 404
    assert anna.get(f"/api/projects/{project}").json()["files"][0]["id"] == file_id


def test_upload_is_stored_by_id_with_its_display_name(app: Any, data_dir: Path) -> None:
    client = signed_up(app)
    project = create(client)
    resp = upload(client, project, "../../etc/comments.docx", make_docx())
    assert resp.status_code == 201
    body = resp.json()
    assert body["filename"] == "comments.docx" and body["kind"] == "docx"
    stored = data_dir / "projects" / project / "files" / f"{body['id']}.docx"
    assert stored.exists() and stored.stat().st_size == body["size"]
    assert not (data_dir / "etc").exists()


def test_client_materials_upload(app: Any) -> None:
    client = signed_up(app)
    project = create(client)
    for name in ("q3_metrics.xlsx", "manager_comments.docx"):
        assert upload(client, project, name, (FIXTURES / name).read_bytes()).status_code == 201
    kinds = [f["kind"] for f in client.get(f"/api/projects/{project}").json()["files"]]
    assert kinds == ["xlsx", "docx"]


def test_exe_renamed_to_docx_is_rejected(app: Any) -> None:
    client = signed_up(app)
    project = create(client)
    resp = upload(client, project, "report.docx", b"MZ\x90\x00" + b"\x00" * 1000)
    assert resp.status_code == 422
    assert "не поддерживается" in resp.json()["detail"]


def test_oversized_file_is_rejected(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "max_upload_bytes", 1000)
    client = signed_up(app)
    project = create(client)
    resp = upload(client, project, "big.docx", make_docx("x" * 5000))
    assert resp.status_code == 413


def test_file_count_is_limited(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "max_files_per_project", 2)
    client = signed_up(app)
    project = create(client)
    assert upload(client, project, "a.docx", make_docx()).status_code == 201
    assert client.post(f"/api/projects/{project}/text", json={"text": "Текст"}).status_code == 201
    assert upload(client, project, "c.docx", make_docx()).status_code == 409


def test_text_is_stored_as_a_text_file(app: Any, data_dir: Path) -> None:
    client = signed_up(app)
    project = create(client, kind="video")
    text = (FIXTURES / "superservice_text.txt").read_text()
    resp = client.post(f"/api/projects/{project}/text", json={"title": "Суперсервис", "text": text})
    assert resp.status_code == 201 and resp.json()["kind"] == "text"
    stored = data_dir / "projects" / project / "files" / f"{resp.json()['id']}.txt"
    assert stored.read_text() == text


def test_wrong_extension_is_read_by_content_with_a_note(app: Any) -> None:
    attack = (FIXTURES / "attacks/t1_manager_comments.docx").read_bytes()  # a plain docx
    client = signed_up(app)
    project = create(client)
    assert upload(client, project, "comments.docx", attack).status_code == 201
    resp = upload(client, project, "comments.xlsx", attack)  # wrong extension: read by content
    assert resp.status_code == 201
    assert any("не совпадает" in n for n in resp.json()["notes"])


def test_deleting_a_project_removes_its_files(app: Any, data_dir: Path) -> None:
    client = signed_up(app)
    project = create(client)
    upload(client, project, "a.docx", make_docx())
    assert (data_dir / "projects" / project).exists()
    client.delete(f"/api/projects/{project}")
    assert not (data_dir / "projects" / project).exists()


def test_blank_titles_are_rejected(app: Any) -> None:
    client = signed_up(app)
    assert client.post("/api/projects", json={"title": "   ", "kind": "deck"}).status_code == 422
    project = create(client)
    assert client.patch(f"/api/projects/{project}", json={"title": " \t "}).status_code == 422
    resp = client.post("/api/projects", json={"title": "  Отчёт  ", "kind": "deck"})
    assert resp.json()["title"] == "Отчёт"


def test_video_length_range_must_be_ordered(app: Any) -> None:
    client = signed_up(app)
    project = create(client, kind="video")
    resp = client.patch(
        f"/api/projects/{project}", json={"settings": {"min_seconds": 90, "max_seconds": 60}}
    )
    assert resp.status_code == 422
    assert "min_seconds" in resp.json()["detail"]


def test_long_file_names_keep_their_extension(app: Any) -> None:
    client = signed_up(app)
    project = create(client)
    name = "отчёт " * 60 + ".docx"
    resp = upload(client, project, name, make_docx())
    shown = resp.json()["filename"]
    assert len(shown) <= 200 and shown.endswith("….docx")


def test_projects_per_user_are_capped(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "max_projects_per_user", 2)
    client = signed_up(app)
    create(client)
    create(client)
    resp = client.post("/api/projects", json={"title": "Третий", "kind": "deck"})
    assert resp.status_code == 409
    # Another user has their own allowance.
    create(signed_up(app, "boris@example.com"))


def test_storage_per_user_is_capped(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    first = make_docx()
    monkeypatch.setattr(get_settings(), "max_bytes_per_user", len(first) + 10)
    client = signed_up(app)
    project = create(client)
    assert upload(client, project, "a.docx", first).status_code == 201
    other = create(client)
    resp = upload(client, other, "b.docx", make_docx())
    assert resp.status_code == 409 and "Место" in resp.json()["detail"]
    assert client.post(f"/api/projects/{other}/text", json={"text": "x" * 100}).status_code == 409


def test_text_counts_against_the_upload_rate_limit(app: Any) -> None:
    client = signed_up(app)
    project = create(client)
    codes = [
        client.post(f"/api/projects/{project}/text", json={"text": "Текст"}).status_code
        for _ in range(61)
    ]
    assert 429 in codes


def test_failed_save_leaves_no_file_behind(
    app: Any, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = signed_up(app)
    project = create(client)
    real_commit = Session.commit

    def failing_commit(self: Session) -> None:
        if self.new:
            raise RuntimeError("database went away")
        real_commit(self)

    monkeypatch.setattr(Session, "commit", failing_commit)
    with pytest.raises(RuntimeError):
        upload(client, project, "a.docx", make_docx())
    files = data_dir / "projects" / project / "files"
    assert not files.exists() or list(files.iterdir()) == []


@pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"), reason="row locks need a real Postgres"
)
def test_parallel_uploads_cannot_overrun_the_file_limit(
    app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "max_files_per_project", 3)
    owner = signed_up(app)
    project = create(owner)
    cookie = owner.cookies.get("sid") or ""
    data = make_docx()

    def one(i: int) -> int:
        client = new_client(app)
        client.cookies.set("sid", cookie)
        return int(upload(client, project, f"{i}.docx", data).status_code)

    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(one, range(8)))
    assert sorted(codes) == [201] * 3 + [409] * 5
    assert len(owner.get(f"/api/projects/{project}").json()["files"]) == 3


def test_unencodable_characters_are_a_422_not_a_500(app: Any) -> None:
    client = signed_up(app)
    assert (
        client.post("/api/projects", json={"title": "Отчёт\u0000", "kind": "deck"}).status_code
        == 422
    )
    project = create(client)
    # A lone surrogate is legal JSON but cannot be written as UTF-8.
    body = '{"text": "abc \\ud800 def"}'
    resp = client.post(
        f"/api/projects/{project}/text",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 422
    long_key = "k" * 300
    resp = client.patch(f"/api/projects/{project}", json={"settings": {long_key: 1}})
    assert resp.status_code == 422 and long_key not in resp.json()["detail"]


def test_text_title_keeps_its_slashes(app: Any) -> None:
    client = signed_up(app)
    project = create(client)
    resp = client.post(
        f"/api/projects/{project}/text", json={"title": "Итоги 2025/2026", "text": "Текст"}
    )
    assert resp.json()["filename"] == "Итоги 2025/2026"


def test_upload_into_a_project_deleted_meanwhile_is_404(
    app: Any,
    data_dir: Path,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = signed_up(app)
    project = create(client)
    real_lock = projects_api.lock_user

    def other_tab_deletes_first(db: Session, user: Any) -> None:
        with session_factory() as other:
            other.execute(delete(Project).where(Project.id == uuid.UUID(project)))
            other.commit()
        real_lock(db, user)

    monkeypatch.setattr(projects_api, "lock_user", other_tab_deletes_first)
    assert upload(client, project, "a.docx", make_docx()).status_code == 404
    files = data_dir / "projects" / project / "files"
    assert not files.exists() or list(files.iterdir()) == []


def test_failed_disk_write_leaves_no_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "files" / "a.docx"

    def disk_full(self: Path, other: Path) -> Path:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "replace", disk_full)
    with pytest.raises(OSError):
        write_file(target, b"data")
    assert list((tmp_path / "files").iterdir()) == []
