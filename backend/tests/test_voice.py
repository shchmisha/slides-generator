"""Narration: clips per scene with the user's key, cached; scene lengths fitted to the voice."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.jobs.render import run_render
from app.models import Project, VoiceSettings
from app.security.vault import seal
from app.voice import elevenlabs
from app.voice.narration import LEAD_S, TAIL_S, fit_timeline, narrate

from .fake_elevenlabs import KEY, FakeElevenLabs
from .test_jobs import fake_build, fake_record, job_row, new_job, planned_video

Sessions = sessionmaker[Session]


@pytest.fixture
def eleven(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeElevenLabs]:
    fake = FakeElevenLabs()
    monkeypatch.setattr(elevenlabs, "transport", fake.transport())
    yield fake


def with_key(sessions: Sessions, project_id: uuid.UUID, sounds: bool = True) -> uuid.UUID:
    with sessions() as db:
        project = db.get(Project, project_id)
        assert project is not None
        db.add(
            VoiceSettings(
                user_id=project.user_id,
                api_key=seal(project.user_id, KEY),
                voice_id="JBFqnCBsd6RMkjVDRZzb",
                voice_name="George",
                sounds=sounds,
            )
        )
        db.commit()
        return project.user_id


# ------------------------------------------------------------------------------ fitting


def test_scenes_keep_their_length_when_the_voice_fits() -> None:
    lengths, warning = fit_timeline([12, 12, 12], [6.0, 8.2, 11.0], 75)
    assert lengths == [12, 12, 12] and warning is None


def test_long_clips_borrow_time_from_scenes_with_room() -> None:
    # Measured on the Superservice video: ElevenLabs v4, voice George.
    planned = [10, 12, 15, 12, 13, 13]
    spoken = [9.44, 10.40, 12.88, 12.08, 11.92, 12.88]
    lengths, warning = fit_timeline(planned, spoken, 75)
    assert sum(lengths) == 75 and warning is None
    for length, voice in zip(lengths, spoken, strict=True):
        assert length >= LEAD_S + voice + TAIL_S - 1e-9


def test_a_voice_longer_than_the_maximum_is_reported() -> None:
    lengths, warning = fit_timeline([10, 10], [11.0, 12.0], 20)
    assert lengths == [11.8, 12.8]
    assert warning == "С голосом ролик длиннее плана: 24.6 с при максимуме 20 с"


# ---------------------------------------------------------------------------- narration


def test_clips_are_cached_and_only_changed_scenes_are_voiced_again(
    data_dir: Path, eleven: FakeElevenLabs
) -> None:
    project_id, user_id = uuid.uuid4(), uuid.uuid4()
    texts = ["Первая сцена.", "Вторая сцена.", "Третья сцена."]
    first = narrate(KEY, "voiceA", texts, project_id=project_id, user_id=user_id, with_sounds=True)
    assert first.synthesized == 3 and first.characters == sum(map(len, texts))
    assert [c.file for c in first.clips] == ["voice-01.mp3", "voice-02.mp3", "voice-03.mp3"]
    assert first.clips[0].alignment is not None and set(first.sounds) == {
        "bed.mp3",
        "transition.mp3",
    }
    again = narrate(KEY, "voiceA", texts, project_id=project_id, user_id=user_id, with_sounds=True)
    assert again.synthesized == 0 and again.characters == 0
    assert eleven.count("sound-generation") == 2  # made once per user
    edited = ["Первая сцена.", "Вторая сцена, но иначе.", "Третья сцена."]
    third = narrate(KEY, "voiceA", edited, project_id=project_id, user_id=user_id, with_sounds=True)
    assert third.synthesized == 1 and eleven.count("text-to-speech") == 4
    # Another voice is another clip.
    other = narrate(
        KEY, "voiceB", edited, project_id=project_id, user_id=user_id, with_sounds=False
    )
    assert other.synthesized == 3 and other.sounds == {}


def test_without_timing_the_length_comes_from_the_mp3(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeElevenLabs(timestamps=False)
    monkeypatch.setattr(elevenlabs, "transport", fake.transport())
    text = "Модель без разметки времени."
    made = narrate(
        KEY, "v1", [text], project_id=uuid.uuid4(), user_id=uuid.uuid4(), with_sounds=False
    )
    clip = made.clips[0]
    assert clip.alignment is None and clip.duration == pytest.approx(len(text) * 0.06, abs=0.01)
    assert fake.count("/with-timestamps") == 1 and fake.count("text-to-speech") == 2


# ------------------------------------------------------------------------------ render job


def test_a_user_with_a_key_gets_a_voiced_video(
    session_factory: Sessions, data_dir: Path, eleven: FakeElevenLabs
) -> None:
    project_id, scenario_id = planned_video(session_factory)
    with_key(session_factory, project_id)
    record = fake_record()
    job_id = new_job(session_factory, project_id, "render", scenario_id)
    run_render(job_id, session_factory, fake_build(), record)
    job = job_row(session_factory, job_id)
    assert job.status == "succeeded", job.error
    assert job.report["audio"] is True and job.report["voice"] == {
        "name": "George",
        "model": "eleven_v4",
        "clips": 6,
        "new_clips": 6,
        "characters": job.report["voice"]["characters"],
        "sounds": True,
    }
    assert "voice_ms" in job.timings
    audio = record.audio[0]
    assert set(audio) == {f"voice-{n:02d}.mp3" for n in range(1, 7)} | {"bed.mp3", "transition.mp3"}
    spec: dict[str, Any] = record.specs[0]
    first = spec["scenes"][0]
    assert first["voice"]["file"] == "voice-01.mp3" and first["voice"]["offset"] == LEAD_S
    assert first["voice"]["alignment"]["chars"][0] == first["narration"][0]
    assert spec["sounds"]["bed"] == {"file": "bed.mp3", "seconds": 20.0}

    # The same scenario again: nothing is voiced twice.
    record_again = fake_record()
    job2 = new_job(session_factory, project_id, "render", scenario_id)
    run_render(job2, session_factory, fake_build(), record_again)
    assert job_row(session_factory, job2).report["voice"]["new_clips"] == 0
    assert eleven.count("text-to-speech") == 6


def test_out_of_credits_fails_the_render_with_a_reason(
    session_factory: Sessions, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeElevenLabs(credits=False)
    monkeypatch.setattr(elevenlabs, "transport", fake.transport())
    project_id, scenario_id = planned_video(session_factory)
    with_key(session_factory, project_id)
    job_id = new_job(session_factory, project_id, "render", scenario_id)
    record = fake_record()
    run_render(job_id, session_factory, fake_build(), record)
    job = job_row(session_factory, job_id)
    assert job.status == "failed"
    assert job.error == "Озвучка не удалась: На счёте ElevenLabs не хватает кредитов для озвучки"
    assert record.specs == []  # nothing was recorded


def test_sounds_are_optional_and_their_failure_only_warns(
    session_factory: Sessions, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeElevenLabs(sound_effects=False)
    monkeypatch.setattr(elevenlabs, "transport", fake.transport())
    project_id, scenario_id = planned_video(session_factory)
    with_key(session_factory, project_id)
    record = fake_record()
    job_id = new_job(session_factory, project_id, "render", scenario_id)
    run_render(job_id, session_factory, fake_build(), record)
    job = job_row(session_factory, job_id)
    assert job.status == "succeeded" and job.report["voice"]["sounds"] is False
    assert any("Фоновая музыка" in w for w in job.report["warnings"])
    assert "bed.mp3" not in record.audio[0] and "sounds" not in record.specs[0]

    quiet_project, quiet_scenario = planned_video(session_factory)
    with_key(session_factory, quiet_project, sounds=False)
    calls = fake.count("sound-generation")
    run_render(
        new_job(session_factory, quiet_project, "render", quiet_scenario),
        session_factory,
        fake_build(),
        fake_record(),
    )
    assert fake.count("sound-generation") == calls  # switched off: not even asked
