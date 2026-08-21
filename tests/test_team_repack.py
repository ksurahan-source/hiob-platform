from __future__ import annotations

from hiob_platform import team
from tests.fakes import QueueClient


def _queue_repack_prefix(
    client: QueueClient,
    *,
    timeline=None,
    sample_tracks=None,
    sample=None,
    tracks=None,
    slots=None,
):
    client.queue("timeline", [{"id": "tl", "duration_ms": 1000}] if timeline is None else timeline)
    if timeline == []:
        return
    client.queue("timeline_track", [] if sample_tracks is None else sample_tracks)
    client.queue("clip", [] if sample is None else sample)
    client.queue("timeline_track", [] if tracks is None else tracks)
    client.queue("slot", [] if slots is None else slots)


def test_repack_terminal_states():
    no_timeline = QueueClient()
    no_timeline.queue("timeline", [])
    assert team.repack_timeline_to_audio(no_timeline, "run") == {"updated_clips": 0, "skipped": "no timeline"}

    no_slots = QueueClient()
    _queue_repack_prefix(no_slots)
    assert team.repack_timeline_to_audio(no_slots, "run") == {"updated_clips": 0, "skipped": "no voiceover slots"}

    no_beats = QueueClient()
    _queue_repack_prefix(
        no_beats,
        slots=[{"beat_index": None, "current_artifact_id": None, "start_ms": 0}],
    )
    assert team.repack_timeline_to_audio(no_beats, "run") == {"updated_clips": 0, "skipped": "no beats"}


def test_repack_updates_all_lanes_slots_music_and_markers():
    client = QueueClient()
    sample_tracks = [{"id": "video", "kind": "video"}, {"id": "audio", "kind": "audio"}, {"id": "caption", "kind": "caption"}]
    tracks = [
        {"id": "audio", "kind": "audio"},
        {"id": "video", "kind": "video"},
        {"id": "caption", "kind": "caption"},
        {"id": "music", "kind": "music"},
        {"id": "sfx", "kind": "sfx"},
    ]
    slots = [
        {"beat_index": None, "current_artifact_id": None, "start_ms": 0},
        {"beat_index": 0, "current_artifact_id": "a0", "start_ms": 0},
        {"beat_index": 1, "current_artifact_id": "a1", "start_ms": 1000},
        {"beat_index": 2, "current_artifact_id": None, "start_ms": None},
    ]
    _queue_repack_prefix(
        client,
        sample_tracks=sample_tracks,
        sample=[
            {"beat_index": None, "start_ms": 0},
            {"beat_index": 0, "start_ms": 0},
            {"beat_index": 1, "start_ms": 1000},
        ],
        tracks=tracks,
        slots=slots,
    )
    client.queue("artifact", [{"id": "a0", "duration_ms": 5000}, {"id": "a1", "duration_ms": 1000}, {"id": "unused", "duration_ms": None}])
    client.queue(
        "clip",
        [
            {"beat_index": None, "attributes": {"scene_type": "ignored"}},
            {"beat_index": 0, "attributes": {"scene_type": "HOOK"}},
            {"beat_index": 1, "attributes": None},
        ],
    )

    # Nine beat-aligned writes. First exercises exception fallback, second
    # exercises empty-result fallback, and the remaining writes land directly.
    client.queue("clip", RuntimeError("beat_index unavailable"), "update")
    client.queue("clip", [{"id": "fallback-exception"}], "update")
    client.queue("clip", [], "update")
    client.queue("clip", [{"id": "fallback-empty"}], "update")
    for index in range(7):
        client.queue("clip", [{"id": f"direct-{index}"}], "update")

    # A null-beat subshot extends past the voice plan, proving tail accounting.
    client.queue(
        "clip",
        [
            {"start_ms": 0, "duration_ms": 1},
            {"start_ms": 10_000, "duration_ms": 500},
        ],
    )
    client.queue("clip", [{"id": "music"}], "update")
    client.queue("timeline", [{"id": "tl"}], "update")
    client.queue("timeline", [{"id": "tl"}], "update")

    result = team.repack_timeline_to_audio(client, "run")
    assert result == {"updated_clips": 9, "new_duration_ms": 10_500, "beats": 3}
    marker_update = [q.payload for q in client.executed if q.table_name == "timeline" and q.operation == "update"][-1]
    assert [marker["beatIndex"] for marker in marker_update["markers"]] == [0, 1, 2]
    slot_updates = [q for q in client.executed if q.table_name == "slot" and q.operation == "update"]
    assert len(slot_updates) == 9


def test_repack_preserves_duration_when_marker_update_fails():
    client = QueueClient()
    _queue_repack_prefix(
        client,
        slots=[{"beat_index": 0, "current_artifact_id": None, "start_ms": 0}],
    )
    client.queue("timeline", [], "update")
    client.queue("timeline", RuntimeError("jsonb rejected"), "update")
    result = team.repack_timeline_to_audio(client, "run")
    assert result["beats"] == 1
    assert result["updated_clips"] == 0


def test_repack_already_packed_resyncs_from_latest_parallel_end():
    client = QueueClient()
    tracks = [
        {"id": "audio", "kind": "audio"},
        {"id": "video", "kind": "video"},
        {"id": "caption", "kind": "caption"},
        {"id": "music", "kind": "music"},
    ]
    _queue_repack_prefix(
        client,
        sample_tracks=tracks[:3],
        sample=[{"beat_index": 1, "start_ms": 1500}],
        tracks=tracks,
        slots=[{"beat_index": 1, "current_artifact_id": "a", "start_ms": 1500}],
    )
    client.queue("artifact", [{"id": "a", "duration_ms": 1000}])
    client.queue("clip", [{"beat_index": 1, "attributes": {"scene_type": "product"}}])
    client.queue("clip", [{"start_ms": 1000, "duration_ms": 2000}])
    client.queue("clip", [])
    client.queue("clip", [{"start_ms": 0, "duration_ms": 4000}])
    client.queue("clip", [{"id": "music"}], "update")
    client.queue("timeline", [{"id": "tl"}], "update")

    result = team.repack_timeline_to_audio(client, "run")
    assert result == {
        "updated_clips": 0,
        "new_duration_ms": 4000,
        "beats": 1,
        "skipped": "clips already packed; timeline duration re-synced",
    }


def test_repack_already_packed_without_content_or_music_keeps_plan_end():
    client = QueueClient()
    tracks = [{"id": "video", "kind": "video"}]
    _queue_repack_prefix(
        client,
        sample_tracks=tracks,
        sample=[{"beat_index": 1, "start_ms": 1500}],
        tracks=tracks,
        slots=[{"beat_index": 1, "current_artifact_id": None, "start_ms": 1500}],
    )
    client.queue("clip", [])
    client.queue("timeline", [{"id": "tl"}], "update")
    result = team.repack_timeline_to_audio(client, "run")
    assert result["skipped"].startswith("clips already packed")
    assert result["new_duration_ms"] > 0


def test_create_clip_existing_row_keeps_non_null_artifact():
    client = QueueClient()
    client.queue("clip", RuntimeError("duplicate"), "insert")
    client.queue("clip", [{"id": "existing"}])
    client.queue("clip", [{"id": "existing"}], "update")
    team.create_clip(
        client,
        track_id="track",
        artifact_id="artifact",
        start_ms=0,
        duration_ms=10,
        beat_index=1,
    )
    update = next(q.payload for q in client.executed if q.operation == "update")
    assert update["artifact_id"] == "artifact"
