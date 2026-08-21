from __future__ import annotations

import pytest

from hiob_platform import team
from tests.fakes import QueueClient


def _last_payload(client: QueueClient, operation: str):
    return next(query.payload for query in reversed(client.executed) if query.operation == operation)


def test_team_and_call_crud_payloads():
    client = QueueClient()
    client.queue("agent_team", [{"id": "team"}], "insert")
    assert team.create_team(client, run_id="run", keyword="fog") == {"id": "team"}
    assert _last_payload(client, "insert")["status"] == "assembling"

    client.queue("agent_team", [{"status": "ready"}], "update")
    assert team.update_team(client, "team", status="ready") == {"status": "ready"}
    client.queue("agent_team", [], "update")
    assert team.update_team(client, "team", status="ready") == {}

    client.queue("agent_call", [{"id": "call"}], "insert")
    assert team.create_call(
        client,
        team_id="team",
        role_code="pd",
        step_index=1,
        parent_call_id="parent",
        input=None,
        model="model",
    ) == {"id": "call"}
    payload = _last_payload(client, "insert")
    assert payload["input"] == {}
    assert payload["status"] == "queued"

    client.queue("agent_call", [{"status": "running"}], "update")
    assert team.start_call(client, "call") == {"status": "running"}
    client.queue("agent_call", [], "update")
    assert team.start_call(client, "call") == {}


def test_finish_call_success_error_and_empty_result():
    client = QueueClient()
    client.queue("agent_call", [{"status": "ok"}], "update")
    assert team.finish_call(
        client,
        "call",
        output={"answer": 1},
        tokens_in=2,
        tokens_out=3,
        cost_usd=0.4,
    ) == {"status": "ok"}
    payload = _last_payload(client, "update")
    assert payload["output"] == {"answer": 1}
    assert "error" not in payload

    client.queue("agent_call", [], "update")
    assert team.finish_call(client, "call", error={"code": "bad"}) == {}
    payload = _last_payload(client, "update")
    assert payload["status"] == "error"
    assert payload["error"] == {"code": "bad"}
    assert "output" not in payload


def test_meeting_note_current_and_legacy_schema():
    client = QueueClient()
    client.queue("agent_meeting", [{"id": "meeting"}], "insert")
    result = team.create_meeting_note(
        client,
        team_id="team",
        run_id="run",
        step_index=1,
        meeting_type="review",
        speaker_role=None,
        summary="summary",
    )
    assert result == {"id": "meeting"}
    payload = _last_payload(client, "insert")
    assert payload["audience_roles"] == []
    assert payload["decisions"] == []
    assert payload["refs"] == []

    legacy = QueueClient()
    legacy.queue("agent_meeting", RuntimeError("missing refs"), "insert")
    legacy.queue("agent_meeting", [], "insert")
    assert team.create_meeting_note(
        legacy,
        team_id="team",
        run_id="run",
        step_index=2,
        meeting_type="handoff",
        speaker_role="pd",
        audience_roles=["qa"],
        source_call_id="call",
        summary="summary",
        decisions=["go"],
        open_questions=["why"],
        next_actions=["ship"],
        refs=[{"id": "artifact"}],
    ) == {}
    assert "refs" not in _last_payload(legacy, "insert")


def test_load_role_and_timeline_materialization():
    client = QueueClient()
    client.queue("agent_role", {"code": "pd"})
    assert team.load_role(client, "pd") == {"code": "pd"}

    existing = QueueClient()
    existing.queue("timeline", [{"id": "timeline"}])
    assert team.ensure_timeline(existing, run_id="run", duration_ms=1000) == {"id": "timeline"}

    for aspect, expected in (("9:16", (1080, 1920)), ("16:9", (1920, 1080)), ("1:1", (1080, 1080))):
        created = QueueClient()
        created.queue("timeline", [])
        created.queue("timeline", [{"id": aspect}], "insert")
        assert team.ensure_timeline(created, run_id="run", duration_ms=1000, aspect=aspect) == {"id": aspect}
        payload = _last_payload(created, "insert")
        assert (payload["width"], payload["height"]) == expected

    track = QueueClient()
    track.queue("timeline_track", [{"id": "track"}])
    assert team.ensure_track(track, timeline_id="tl", kind="video", label="Video", ord=0) == {"id": "track"}

    track = QueueClient()
    track.queue("timeline_track", [])
    track.queue("timeline_track", [{"id": "new"}], "insert")
    assert team.ensure_track(track, timeline_id="tl", kind="video", label="Video", ord=0, z_index=3) == {"id": "new"}
    assert _last_payload(track, "insert")["z_index"] == 3


def test_create_clip_current_and_legacy_paths():
    current = QueueClient()
    current.queue("clip", [{"id": "clip"}], "insert")
    assert team.create_clip(
        current,
        track_id="track",
        artifact_id="artifact",
        start_ms=1,
        duration_ms=2,
        beat_index=3,
        transforms={"scale": 1},
        effects=["fade"],
        attributes={"scene": "proof"},
    ) == {"id": "clip"}
    payload = _last_payload(current, "insert")
    assert payload["transforms"] == {"scale": 1}
    assert payload["effects"] == ["fade"]
    assert payload["attributes"] == {"scene": "proof"}

    unsupported = QueueClient()
    unsupported.queue("clip", RuntimeError("insert failed"), "insert")
    with pytest.raises(RuntimeError, match="insert failed"):
        team.create_clip(unsupported, track_id="track", artifact_id=None, start_ms=0, duration_ms=1)

    existing = QueueClient()
    existing.queue("clip", RuntimeError("duplicate"), "insert")
    existing.queue("clip", [{"id": "existing"}])
    existing.queue("clip", [{"id": "existing"}], "update")
    assert team.create_clip(
        existing,
        track_id="track",
        artifact_id=None,
        start_ms=0,
        duration_ms=10,
        beat_index=1,
    ) == {"id": "existing"}
    assert "artifact_id" not in _last_payload(existing, "update")

    no_existing = QueueClient()
    no_existing.queue("clip", RuntimeError("new schema rejected"), "insert")
    no_existing.queue("clip", [])
    no_existing.queue("clip", [{"id": "legacy"}], "insert")
    assert team.create_clip(
        no_existing,
        track_id="track",
        artifact_id="artifact",
        start_ms=0,
        duration_ms=10,
        beat_index=1,
    ) == {"id": "legacy"}
    assert "beat_index" not in _last_payload(no_existing, "insert")

    failed_lookup = QueueClient()
    failed_lookup.queue("clip", RuntimeError("new schema rejected"), "insert")
    failed_lookup.queue("clip", RuntimeError("legacy lookup rejected"))
    failed_lookup.queue("clip", [{"id": "legacy"}], "insert")
    assert team.create_clip(
        failed_lookup,
        track_id="track",
        artifact_id="artifact",
        start_ms=0,
        duration_ms=10,
        beat_index=1,
    ) == {"id": "legacy"}


def test_marker_and_pure_timing_helpers(monkeypatch):
    client = QueueClient()
    client.queue("timeline", [{"id": "tl"}], "update")
    assert team.update_timeline_markers(client, "tl", [{"id": "beat-1"}]) == {"id": "tl"}
    client.queue("timeline", [], "update")
    assert team.update_timeline_markers(client, "tl", []) == {}

    assert team._scene_breath_ms("proof", voiced=False) == 0
    assert team._scene_breath_ms("unknown") == team._SCENE_BREATH_DEFAULT_MS
    assert team._beat_duration_ms(0) == team._REPACK_MIN_MS
    assert team._beat_duration_ms(1000) == 1000 + team._BEAT_PAD_MS

    assert team._subshot_count(team._SUBSHOT_MIN_SPLIT_MS - 1) == 1
    monkeypatch.setattr(team, "_SUBSHOT_MIN_PIECE_MS", 5000)
    assert team._subshot_count(6000) == 1
    assert team._plan_subshots(0, 1000) == []

    monkeypatch.setattr(team, "_SUBSHOT_MIN_PIECE_MS", 100)
    monkeypatch.setattr(team, "_SUBSHOT_MIN_SPLIT_MS", 1000)
    monkeypatch.setattr(team, "_SUBSHOT_TARGET_MS", 1000)
    monkeypatch.setattr(team, "_SUBSHOT_MAX", 4)
    regular = team._plan_subshots(100, 4501)
    product = team._plan_subshots(0, 4000, product=True)
    assert len(regular) == 4
    assert regular[-1]["start_ms"] + regular[-1]["duration_ms"] == 4601
    assert regular[0]["shot_size"] == "wide"
    assert product[0]["shot_size"] == "medium"
