from __future__ import annotations

from hiob_platform import team
from tests.fakes import QueueClient


def test_group_scenes_terminal_states():
    no_timeline = QueueClient()
    no_timeline.queue("timeline", [])
    assert team.group_scenes_hold_image(no_timeline, "run")["skipped"] == "no timeline"

    no_track = QueueClient()
    no_track.queue("timeline", [{"id": "tl"}])
    no_track.queue("timeline_track", [])
    assert team.group_scenes_hold_image(no_track, "run")["skipped"] == "no video track"

    no_clips = QueueClient()
    no_clips.queue("timeline", [{"id": "tl"}])
    no_clips.queue("timeline_track", [{"id": "video"}])
    no_clips.queue("clip", [{"beat_index": None, "artifact_id": "a"}, {"beat_index": 0, "artifact_id": None}])
    assert team.group_scenes_hold_image(no_clips, "run")["skipped"] == "no video clips with images"


def test_group_scenes_holds_only_consecutive_persona_images():
    client = QueueClient()
    client.queue("timeline", [{"id": "tl"}])
    client.queue("timeline_track", [{"id": "video"}])
    client.queue(
        "clip",
        [
            {"id": "c0", "beat_index": 0, "artifact_id": "a"},
            {"id": "c1", "beat_index": 1, "artifact_id": "b"},
            {"id": "c2", "beat_index": 2, "artifact_id": "a"},
            {"id": "c4", "beat_index": 4, "artifact_id": "c"},
            {"id": "c5", "beat_index": 5, "artifact_id": "d"},
            {"id": "c6", "beat_index": 6, "artifact_id": "e"},
        ],
    )
    client.queue(
        "artifact",
        [
            {"id": "a", "attributes": {"persona_id": " person "}},
            {"id": "b", "attributes": {"persona_id": "person"}},
            {"id": "c", "attributes": {"persona_id": "person"}},
            {"id": "d", "attributes": None},
        ],
    )
    client.queue("clip", [{"id": "c1"}], "update")
    result = team.group_scenes_hold_image(client, "run")
    assert result == {"scenes": 4, "multi_beat_scenes": 1, "held_beats": 1, "total_beats": 6}
    update = next(q.payload for q in client.executed if q.operation == "update")
    assert update["artifact_id"] == "a"


def test_point_clips_to_own_image_terminal_states():
    no_timeline = QueueClient()
    no_timeline.queue("timeline", [])
    assert team.point_clips_to_own_beat_image(no_timeline, "run")["skipped"] == "no timeline"

    no_track = QueueClient()
    no_track.queue("timeline", [{"id": "tl"}])
    no_track.queue("timeline_track", [])
    assert team.point_clips_to_own_beat_image(no_track, "run")["skipped"] == "no video track"

    no_images = QueueClient()
    no_images.queue("timeline", [{"id": "tl"}])
    no_images.queue("timeline_track", [{"id": "video"}])
    no_images.queue("slot", [{"beat_index": None, "current_artifact_id": "a"}, {"beat_index": 0, "current_artifact_id": None}])
    assert team.point_clips_to_own_beat_image(no_images, "run")["skipped"] == "no visual slot images"


def test_point_clips_to_own_image_repoints_only_drifted_clips():
    client = QueueClient()
    client.queue("timeline", [{"id": "tl"}])
    client.queue("timeline_track", [{"id": "video-a"}, {"id": "video-b"}])
    client.queue(
        "slot",
        [
            {"beat_index": 0, "current_artifact_id": "a"},
            {"beat_index": 1, "current_artifact_id": "b"},
        ],
    )
    client.queue(
        "clip",
        [
            {"id": "none", "beat_index": None, "artifact_id": "x"},
            {"id": "missing", "beat_index": 9, "artifact_id": "x"},
            {"id": "same", "beat_index": 0, "artifact_id": "a"},
            {"id": "drifted", "beat_index": 1, "artifact_id": "a"},
        ],
    )
    client.queue("clip", [{"id": "drifted"}], "update")
    assert team.point_clips_to_own_beat_image(client, "run") == {
        "repointed": 1,
        "beats_with_image": 2,
        "total_video_clips": 4,
    }


def test_split_subshots_terminal_states():
    no_timeline = QueueClient()
    no_timeline.queue("timeline", [])
    assert team.split_long_beats_into_subshots(no_timeline, "run")["skipped"] == "no timeline"

    no_track = QueueClient()
    no_track.queue("timeline", [{"id": "tl"}])
    no_track.queue("timeline_track", [])
    assert team.split_long_beats_into_subshots(no_track, "run")["skipped"] == "no video track"


def test_split_subshots_regular_product_and_social_proof(monkeypatch):
    monkeypatch.setattr(team, "_SUBSHOT_MIN_SPLIT_MS", 1000)
    monkeypatch.setattr(team, "_SUBSHOT_TARGET_MS", 1000)
    monkeypatch.setattr(team, "_SUBSHOT_MIN_PIECE_MS", 100)
    monkeypatch.setattr(team, "_SUBSHOT_MAX", 2)

    client = QueueClient()
    client.queue("timeline", [{"id": "tl"}])
    client.queue("timeline_track", [{"id": "video"}])
    client.queue(
        "clip",
        [
            {"id": "follower", "track_id": "video", "beat_index": None},
            {"id": "done", "track_id": "video", "beat_index": 0, "attributes": {"subshot_count": 2}},
            {"id": "short", "track_id": "video", "beat_index": 1, "duration_ms": 500, "attributes": {}},
            {
                "id": "regular", "track_id": "video", "beat_index": 2, "start_ms": 0, "duration_ms": 2001,
                "artifact_id": "a", "in_ms": 5, "out_ms": 9, "effects": ["fade"], "keyframes": [1],
                "text_content": "text", "attributes": {"scene_type": "dialogue"},
            },
            {
                "id": "product", "track_id": "video", "beat_index": 3, "start_ms": 2001, "duration_ms": 2000,
                "artifact_id": "b", "attributes": {"scene_type": "proof"},
            },
            {
                "id": "social", "track_id": "video", "beat_index": 4, "start_ms": 4001, "duration_ms": 2000,
                "artifact_id": None, "attributes": {"render_mode": "social_proof"},
            },
        ],
    )
    for clip_id in ("regular", "product", "social"):
        client.queue("clip", [{"id": clip_id}], "update")
        client.queue("clip", [{"id": f"{clip_id}-sub"}], "insert")

    result = team.split_long_beats_into_subshots(client, "run")
    assert result == {"split_beats": 3, "inserted_subshots": 3, "total_video_clips": 6}
    inserts = [q.payload for q in client.executed if q.operation == "insert"]
    assert inserts[0]["in_ms"] == 5
    assert inserts[0]["effects"] == ["fade"]
    assert inserts[0]["keyframes"] == [1]
    assert inserts[1]["in_ms"] == 0
    assert inserts[1]["effects"] == []
    assert inserts[1]["keyframes"] == []
    assert inserts[1]["attributes"]["subshot_shot_size"] == "macro"


def test_sync_video_without_artifacts_uses_no_artifact_query():
    client = QueueClient()
    client.queue("timeline", [{"id": "tl", "markers": [{"beatIndex": 0, "timeMs": 0, "durationMs": 1000}]}])
    client.queue("timeline_track", [{"id": "video"}])
    client.queue("clip", [])
    assert team.sync_video_positions_from_markers(client, "run") == {"synced": 0, "total": 0}
    assert not any(q.table_name == "artifact" for q in client.executed)
