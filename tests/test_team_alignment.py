from __future__ import annotations

import pytest

from hiob_platform import team
from tests.fakes import QueueClient


@pytest.mark.parametrize(
    ("timeline", "tracks", "expected"),
    [
        ([], None, "no_timeline"),
        ([{"id": "tl", "markers": []}], None, "no_markers"),
        ([{"id": "tl", "markers": [{"beatIndex": None, "durationMs": 1}]}], None, "no_valid_markers"),
        ([{"id": "tl", "markers": [{"beatIndex": 0, "durationMs": 1}]}], [], "no_video_tracks"),
    ],
)
def test_sync_video_terminal_states(timeline, tracks, expected):
    client = QueueClient()
    client.queue("timeline", timeline)
    if tracks is not None:
        client.queue("timeline_track", tracks)
    assert team.sync_video_positions_from_markers(client, "run")["skipped"] == expected


def test_sync_video_positions_and_sub_images():
    client = QueueClient()
    client.queue(
        "timeline",
        [{
            "id": "tl",
            "markers": [
                {"beatIndex": 0, "timeMs": 0, "durationMs": 5000},
                {"beatIndex": 1, "timeMs": 5000, "durationMs": 1000},
                {"beatIndex": None, "timeMs": 0, "durationMs": 1},
            ],
        }],
    )
    client.queue("timeline_track", [{"id": "video"}])
    client.queue(
        "clip",
        [
            {"id": "none", "beat_index": None},
            {"id": "absent", "beat_index": 9},
            {"id": "long", "beat_index": 0, "start_ms": 1, "duration_ms": 2, "artifact_id": "art", "attributes": {}},
            {"id": "no-key", "beat_index": 0, "start_ms": 0, "duration_ms": 5000, "artifact_id": "empty", "attributes": {}},
            {"id": "no-art", "beat_index": 0, "start_ms": 0, "duration_ms": 5000, "attributes": {}},
            {"id": "enough", "beat_index": 0, "start_ms": 0, "duration_ms": 5000, "artifact_id": "art", "attributes": {"sub_images": ["a", "b"]}},
            {"id": "same", "beat_index": 1, "start_ms": 5000, "duration_ms": 1000, "attributes": {}},
        ],
    )
    client.queue("artifact", [{"id": "art", "storage_key": "images/a.png"}, {"id": "empty", "storage_key": None}])
    client.queue("clip", [{"id": "long"}], "update")

    result = team.sync_video_positions_from_markers(client, "run")
    assert result == {"synced": 1, "total": 7}
    update = next(q for q in client.executed if q.operation == "update")
    assert update.payload["start_ms"] == 0
    assert update.payload["duration_ms"] == 5000
    assert update.payload["attributes"]["sub_images"] == ["images/a.png", "images/a.png"]


@pytest.mark.parametrize(
    ("timeline", "tracks", "video", "music", "expected"),
    [
        ([], None, None, None, "no_timeline"),
        ([{"id": "tl"}], [], None, None, "no_tracks"),
        ([{"id": "tl"}], [{"id": "v", "kind": "video"}, {"id": "m", "kind": "music"}], [], [], "no_music_or_video"),
        ([{"id": "tl"}], [{"id": "v", "kind": "video"}, {"id": "m", "kind": "music"}], [{"start_ms": 0, "duration_ms": 0}], [{"id": "m1"}], "no_music_or_video"),
    ],
)
def test_extend_music_terminal_states(timeline, tracks, video, music, expected):
    client = QueueClient()
    client.queue("timeline", timeline)
    if tracks is not None:
        client.queue("timeline_track", tracks)
    if video is not None:
        client.queue("clip", video)
    if music is not None:
        client.queue("clip", music)
    assert team.extend_music_to_cover(client, "run")["skipped"] == expected


def test_extend_music_loops_and_caps_at_eight():
    client = QueueClient()
    client.queue("timeline", [{"id": "tl"}])
    client.queue("timeline_track", [{"id": "v", "kind": "video"}, {"id": "m", "kind": "music"}])
    client.queue("clip", [{"start_ms": 0, "duration_ms": 12_000}])
    client.queue(
        "clip",
        [{
            "id": "music",
            "track_id": "m",
            "artifact_id": "artifact",
            "start_ms": 0,
            "duration_ms": 0,
            "transforms": None,
            "effects": None,
        }],
    )
    for index in range(8):
        client.queue("clip", [{"id": f"loop-{index}"}], "insert")
    result = team.extend_music_to_cover(client, "run")
    assert result == {"looped": 8, "video_end": 12_000, "music_end": 8_000}
    inserts = [q.payload for q in client.executed if q.operation == "insert"]
    assert inserts[0]["transforms"]["scale"] == 1
    assert inserts[0]["effects"] == []

    partial = QueueClient()
    partial.queue("timeline", [{"id": "tl"}])
    partial.queue("timeline_track", [{"id": "v", "kind": "video"}, {"id": "m", "kind": "music"}])
    partial.queue("clip", [{"start_ms": 0, "duration_ms": 4500}])
    partial.queue(
        "clip",
        [{
            "id": "music",
            "track_id": "m",
            "artifact_id": "artifact",
            "start_ms": 0,
            "duration_ms": 2000,
            "transforms": {"scale": 2},
            "effects": ["fade"],
        }],
    )
    partial.queue("clip", [{"id": "loop"}], "insert")
    assert team.extend_music_to_cover(partial, "run")["music_end"] == 4000


@pytest.mark.parametrize(
    ("timeline", "tracks", "expected"),
    [
        ([], None, "no_timeline"),
        ([{"id": "tl", "markers": []}], None, "no_markers"),
        ([{"id": "tl", "markers": [{"beatIndex": 0, "durationMs": 1000}]}], [], "no_audio_tracks"),
    ],
)
def test_sync_audio_terminal_states(timeline, tracks, expected):
    client = QueueClient()
    client.queue("timeline", timeline)
    if tracks is not None:
        client.queue("timeline_track", tracks)
    assert team.sync_audio_positions_from_markers(client, "run")["skipped"] == expected


def test_sync_audio_positions_clamps_and_preserves():
    client = QueueClient()
    client.queue(
        "timeline",
        [{
            "id": "tl",
            "markers": [
                {"beatIndex": 0, "timeMs": 0, "durationMs": 1000},
                {"beatIndex": 1, "timeMs": 1000, "durationMs": 2000},
                {"beatIndex": None, "durationMs": 1},
            ],
        }],
    )
    client.queue("timeline_track", [{"id": "audio", "kind": "audio"}, {"id": "sfx", "kind": "sfx"}])
    client.queue(
        "clip",
        [
            {"id": "absent", "beat_index": 9, "start_ms": 0, "duration_ms": 1},
            {"id": "same", "beat_index": 0, "start_ms": 0, "duration_ms": 1000},
            {"id": "clamp", "beat_index": 1, "start_ms": 0, "duration_ms": 3000},
        ],
    )
    client.queue("clip", [{"id": "zero", "beat_index": 1, "start_ms": 5, "duration_ms": 0}])
    client.queue("clip", [{"id": "clamp"}], "update")
    client.queue("clip", [{"id": "zero"}], "update")
    assert team.sync_audio_positions_from_markers(client, "run") == {"synced": 2}
    updates = [q.payload for q in client.executed if q.operation == "update"]
    assert updates == [
        {"start_ms": 1000, "duration_ms": 2000, "updated_at": "now()"},
        {"start_ms": 1000, "duration_ms": 2000, "updated_at": "now()"},
    ]


@pytest.mark.parametrize(
    ("timeline", "tracks", "expected"),
    [
        ([], None, "no_timeline"),
        ([{"id": "tl", "markers": []}], None, "no_markers"),
        ([{"id": "tl", "markers": [{"beatIndex": None, "durationMs": 1}]}], None, "no_valid_markers"),
        ([{"id": "tl", "markers": [{"beatIndex": 0, "durationMs": 1}]}], [], "no_caption_tracks"),
    ],
)
def test_sync_caption_terminal_states(timeline, tracks, expected):
    client = QueueClient()
    client.queue("timeline", timeline)
    if tracks is not None:
        client.queue("timeline_track", tracks)
    assert team.sync_caption_positions_from_markers(client, "run")["skipped"] == expected


def test_sync_caption_positions():
    client = QueueClient()
    client.queue(
        "timeline",
        [{
            "id": "tl",
            "markers": [
                {"beatIndex": 0, "timeMs": 0, "durationMs": 1000},
                {"beatIndex": 1, "timeMs": 1000, "durationMs": 2000},
            ],
        }],
    )
    client.queue("timeline_track", [{"id": "caption"}])
    client.queue(
        "clip",
        [
            {"id": "none", "beat_index": None},
            {"id": "absent", "beat_index": 9},
            {"id": "same", "beat_index": 0, "start_ms": 0, "duration_ms": 1000},
            {"id": "move", "beat_index": 1, "start_ms": 5, "duration_ms": 6},
        ],
    )
    client.queue("clip", [{"id": "move"}], "update")
    assert team.sync_caption_positions_from_markers(client, "run") == {"synced": 1, "total": 4}
