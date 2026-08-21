from __future__ import annotations

from hiob_platform import team
from tests.fakes import QueueClient


def test_verify_slots_coverage_terminal_states():
    empty = QueueClient()
    empty.queue("slot", [])
    assert team.verify_slots_coverage(empty, "run") == {
        "ok": False,
        "violations": ["no slots with artifacts found for this run"],
        "warnings": [],
    }

    unindexed = QueueClient()
    unindexed.queue(
        "slot",
        [
            {"track": "voiceover", "beat_index": None, "start_ms": 10},
            {"track": "sfx", "beat_index": None, "start_ms": 20},
            {"track": "visual", "beat_index": None, "start_ms": 30},
        ],
    )
    result = team.verify_slots_coverage(unindexed, "run")
    assert result["ok"] is False
    assert result["violations"] == ["no beat-indexed voice or SFX slots found"]
    assert len(result["warnings"]) == 2


def test_verify_slots_coverage_gap_sfx_only_and_success():
    client = QueueClient()
    client.queue(
        "slot",
        [
            {"track": "voiceover", "beat_index": 0, "start_ms": 0},
            {"track": "sfx", "beat_index": 2, "start_ms": 2000},
        ],
    )
    result = team.verify_slots_coverage(client, "run")
    assert result["ok"] is False
    assert "beat_1: no voice or SFX slots" in result["violations"][0]
    assert "beat_2: SFX-only" in result["violations"][1]

    ok = QueueClient()
    ok.queue(
        "slot",
        [
            {"track": "voiceover", "beat_index": 0, "start_ms": 0},
            {"track": "voiceover", "beat_index": 1, "start_ms": 1000},
            {"track": "sfx", "beat_index": 1, "start_ms": 1000},
        ],
    )
    assert team.verify_slots_coverage(ok, "run")["ok"] is True


def test_sync_clips_terminal_paths():
    no_slots = QueueClient()
    no_slots.queue("slot", [])
    assert team.sync_clips_from_slots(no_slots, "run") == {"updated": 0, "run_id": "run"}

    no_timeline = QueueClient()
    no_timeline.queue("slot", [{"track": "voiceover", "current_artifact_id": "a"}])
    no_timeline.queue("timeline", [])
    assert team.sync_clips_from_slots(no_timeline, "run")["updated"] == 0

    no_tracks = QueueClient()
    no_tracks.queue("slot", [{"track": "voiceover", "current_artifact_id": "a"}])
    no_tracks.queue("timeline", [{"id": "tl", "markers": []}])
    no_tracks.queue("timeline_track", [])
    assert team.sync_clips_from_slots(no_tracks, "run")["updated"] == 0

    no_clips = QueueClient()
    no_clips.queue("slot", [{"track": "voiceover", "current_artifact_id": None}])
    no_clips.queue(
        "timeline",
        [{"id": "tl", "markers": [{"beatIndex": None}, {"beatIndex": 0, "durationMs": None}]}],
    )
    no_clips.queue("timeline_track", [{"id": "audio", "kind": "audio", "ord": 0}])
    no_clips.queue("clip", [])
    assert team.sync_clips_from_slots(no_clips, "run")["updated"] == 0


def test_sync_clips_lossless_binding_and_duration(capsys):
    client = QueueClient()
    client.queue(
        "slot",
        [
            {"track": "unknown", "beat_index": 0, "start_ms": 0, "current_artifact_id": "unknown"},
            {"track": "voiceover", "beat_index": 0, "start_ms": 0, "current_artifact_id": "voice"},
            {"track": "voiceover", "beat_index": 2, "start_ms": 2000, "current_artifact_id": "missing"},
            {"track": "sfx", "beat_index": None, "start_ms": 1500, "current_artifact_id": "sfx"},
            {"track": "visual", "beat_index": 1, "start_ms": 1000, "current_artifact_id": "visual"},
        ],
    )
    client.queue(
        "timeline",
        [{
            "id": "tl",
            "markers": [
                {"beatIndex": 0, "durationMs": 1900},
                {"beatIndex": None, "durationMs": 10},
                {"beatIndex": 1, "durationMs": None},
            ],
        }],
    )
    client.queue(
        "timeline_track",
        [
            {"id": "audio-a", "kind": "audio", "ord": 0},
            {"id": "audio-b", "kind": "audio", "ord": 1},
            {"id": "sfx", "kind": "sfx", "ord": 2},
            {"id": "video", "kind": "video", "ord": 3},
        ],
    )
    client.queue("artifact", [{"id": "voice", "duration_ms": 2100}, {"id": "sfx", "duration_ms": 50}])
    client.queue(
        "clip",
        [
            {"id": "orphan-track", "track_id": "absent", "beat_index": 0, "start_ms": 0},
            {"id": "voice-a", "track_id": "audio-a", "beat_index": 0, "start_ms": 0},
            {"id": "voice-b", "track_id": "audio-b", "beat_index": 0, "start_ms": 0},
            {"id": "voice-missing", "track_id": "audio-a", "beat_index": 2, "start_ms": 2000},
            {"id": "sfx-first", "track_id": "sfx", "beat_index": None, "start_ms": 1500},
            {"id": "sfx-duplicate", "track_id": "sfx", "beat_index": None, "start_ms": 1500},
            {"id": "visual", "track_id": "video", "beat_index": 1, "start_ms": 1000},
        ],
    )
    for _ in range(5):
        client.queue("clip", [{"id": "updated"}], "update")

    result = team.sync_clips_from_slots(client, "run")
    assert result == {"updated": 5, "run_id": "run"}
    assert "updated=5" in capsys.readouterr().out

    updates = [q for q in client.executed if q.table_name == "clip" and q.operation == "update"]
    voice_updates = [q.payload for q in updates if q.payload.get("artifact_id") == "voice"]
    assert len(voice_updates) == 2
    assert all(payload["duration_ms"] == 2100 for payload in voice_updates)
    missing_update = next(q.payload for q in updates if q.payload.get("artifact_id") == "missing")
    assert "duration_ms" not in missing_update
    assert sum(q.payload.get("artifact_id") == "sfx" for q in updates) == 1
