from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from hiob_platform import placement, storage

from .fakes import QueueClient


class StorageBucket:
    def __init__(self):
        self.uploads = []
        self.downloads: dict[str, bytes] = {}

    def upload(self, *args, **kwargs):
        self.uploads.append((args, kwargs))

    def download(self, key):
        return self.downloads[key]

    def get_public_url(self, key):
        return f"https://cdn.example/{key}"


class StorageClient(QueueClient):
    def __init__(self):
        super().__init__()
        self.bucket = StorageBucket()
        self.storage = SimpleNamespace(from_=lambda name: self.bucket)


def test_placement_primitives_and_resolver():
    client = QueueClient()
    client.queue("artifact", [])
    assert placement._next_artifact_version(client, "slot") == 1
    client.queue("artifact", [{"version": 4}])
    assert placement._next_artifact_version(client, "slot") == 5

    client.queue("slot", [{"id": "existing"}])
    assert placement._ensure_slot(
        client, run_id="r", track="visual", beat_index=0, start_ms=0, end_ms=10
    )["id"] == "existing"
    client.queue("slot", [])
    client.queue("slot", [{"id": "inserted"}], "insert")
    assert placement._ensure_slot(
        client, run_id="r", track="visual", beat_index=0, start_ms=0, end_ms=10
    )["id"] == "inserted"

    client.queue("artifact", [{"version": 1}])
    client.queue("artifact", [{"id": "artifact"}], "insert")
    artifact = placement._artifact_from_item(
        client,
        run_id="r",
        slot_id="s",
        item={"storage_key": "k", "sha256": "h", "mime": "video/mp4", "id": "i"},
        reuse="demo",
    )
    assert artifact["id"] == "artifact"
    assert client.executed[-1].payload["version"] == 2

    assert placement._resolve_placements([], {}, 10, 0, at_ms=7) == [
        {"beat": 0, "start_ms": 7, "duration_ms": 10}
    ]
    assert placement._resolve_placements([2], {}, 10, 0, at_ms=7)[0]["beat"] == 2
    assert placement._resolve_placements([2, 1], {}, 10, 30, mode="append") == [
        {"beat": 2, "start_ms": 30, "duration_ms": 10},
        {"beat": 1, "start_ms": 40, "duration_ms": 10},
    ]
    assert placement._resolve_placements([0, 1], {0: (5, 0)}, 10, 20) == [
        {"beat": 0, "start_ms": 5, "duration_ms": 10},
        {"beat": 1, "start_ms": 20, "duration_ms": 10},
    ]


def test_place_media_validation_and_happy_path(monkeypatch):
    with pytest.raises(ValueError, match="beats"):
        placement._place_media(
            QueueClient(), run_id="r", item={"storage_key": "k"}, beats=[],
            duration_ms=None, aspect="9:16", effects=None, reuse="x"
        )
    with pytest.raises(ValueError, match="storage_key"):
        placement._place_media(
            QueueClient(), run_id="r", item={}, beats=[0], duration_ms=None,
            aspect="9:16", effects=None, reuse="x"
        )

    client = QueueClient()
    monkeypatch.setattr(
        placement,
        "ensure_timeline",
        lambda *_a, **_k: {"id": "timeline", "duration_ms": 5},
    )
    monkeypatch.setattr(placement, "ensure_track", lambda *_a, **_k: {"id": "track"})
    created = []

    def create_clip(*_args, **kwargs):
        created.append(kwargs)
        return {"id": f"clip-{len(created)}"}

    monkeypatch.setattr(placement, "create_clip", create_clip)
    client.queue("clip", [{"start_ms": 3, "duration_ms": 4, "beat_index": 0}])
    for slot_id in ("slot-0", "slot-0", "slot-1"):
        client.queue("slot", [{"id": slot_id}])
    client.queue("artifact", [])
    client.queue("artifact", [{"id": "artifact"}], "insert")
    client.queue("slot", [], "update")
    client.queue("slot", [], "update")
    client.queue("timeline", [], "update")
    result = placement._place_media(
        client,
        run_id="r",
        item={"storage_key": "k", "sha256": "h", "mime": "video/mp4"},
        beats=[1, 0, 1],
        duration_ms=10,
        aspect="9:16",
        effects=[{"kind": "fx"}],
        reuse="reuse",
        attributes={"a": 1},
        mode="at_beat",
    )
    assert result["beats"] == [0, 1]
    assert result["clip_ids"] == ["clip-1", "clip-2"]
    assert result["end_ms"] == 17
    assert created[0]["effects"] == [{"kind": "fx"}]
    assert any(q.table_name == "timeline" and q.operation == "update" for q in client.executed)

    monkeypatch.setattr(
        placement,
        "ensure_timeline",
        lambda *_a, **_k: {"id": "timeline", "duration_ms": 999},
    )
    client = QueueClient()
    client.queue("clip", [])
    client.queue("slot", [{"id": "slot"}])
    client.queue("artifact", [])
    client.queue("artifact", [{"id": "artifact"}], "insert")
    client.queue("slot", [{"id": "slot"}])
    client.queue("slot", [], "update")
    placement._place_media(
        client,
        run_id="r",
        item={"storage_key": "k", "sha256": "h", "mime": "video/mp4"},
        beats=[0], duration_ms=10, aspect="9:16", effects=None, reuse="reuse",
    )
    assert not any(q.table_name == "timeline" and q.operation == "update" for q in client.executed)

    monkeypatch.setattr(placement, "_resolve_placements", lambda *_a, **_k: [])
    client = QueueClient()
    client.queue("clip", [])
    with pytest.raises(ValueError, match="no placements"):
        placement._place_media(
            client,
            run_id="r",
            item={"storage_key": "k", "sha256": "h", "mime": "video/mp4"},
            beats=[0],
            duration_ms=10,
            aspect="9:16",
            effects=None,
            reuse="reuse",
        )


def test_placement_public_helpers(monkeypatch):
    with pytest.raises(ValueError, match="expected video"):
        placement.place_library_video(
            QueueClient(), run_id="r", item={"kind": "image"}, beats=[0]
        )
    captured = []

    def place(*_args, **kwargs):
        captured.append(kwargs)
        return {
            "timeline_id": "t", "start_ms": 0, "per_clip_ms": 10,
            "artifact_id": "a", "clip_ids": ["c"],
        }

    monkeypatch.setattr(placement, "_place_media", place)
    placement.place_library_video(
        QueueClient(), run_id="r", item={"kind": "video"}, at_ms=12
    )
    assert captured[-1]["mode"] == "at_beat" and captured[-1]["beats"] == []

    assert placement._is_image_item({"kind": "image"})
    assert placement._is_image_item({"mime": "image/png"})
    assert not placement._is_image_item({"kind": "video", "mime": "video/mp4"})

    monkeypatch.setattr(placement, "ensure_track", lambda *_a, **_k: {"id": "overlay"})
    monkeypatch.setattr(placement, "create_clip", lambda *_a, **_k: {"id": "quote"})
    result = placement.place_social_proof(
        QueueClient(),
        run_id="r",
        item={"kind": "image"},
        quote="proof",
    )
    assert result["proof_frame"] is True and result["quote_clip_id"] == "quote"
    result = placement.place_social_proof(
        QueueClient(), run_id="r", item={"kind": "video"}
    )
    assert result["treatment"] == "full-bleed video"


def test_demo_broll_errors_and_success(monkeypatch):
    client = QueueClient()
    client.queue("timeline", [])
    with pytest.raises(ValueError, match="no timeline"):
        placement.place_demo_broll(client, run_id="r", item={}, beat=0)

    client = QueueClient()
    client.queue("timeline", [{"id": "t"}])
    client.queue("timeline_track", [])
    with pytest.raises(ValueError, match="no video track"):
        placement.place_demo_broll(client, run_id="r", item={}, beat=0)

    client = QueueClient()
    client.queue("timeline", [{"id": "t"}])
    client.queue("timeline_track", [{"id": "vt"}])
    client.queue("clip", [])
    with pytest.raises(ValueError, match="no video clip"):
        placement.place_demo_broll(client, run_id="r", item={}, beat=2)

    client = QueueClient()
    client.queue("timeline", [{"id": "t"}])
    client.queue("timeline_track", [{"id": "vt"}])
    client.queue("clip", [{"id": "c", "start_ms": 4, "duration_ms": None}])
    monkeypatch.setattr(placement, "_ensure_slot", lambda *_a, **_k: {"id": "s"})
    monkeypatch.setattr(placement, "_artifact_from_item", lambda *_a, **_k: {"id": "a"})
    client.queue("slot", [], "update")
    client.queue("clip", [], "update")
    result = placement.place_demo_broll(
        client,
        run_id="r",
        item={"storage_key": "key"},
        beat=2,
    )
    assert result == {
        "run_id": "r", "beat": 2, "artifact_id": "a", "clip_id": "c", "storage_key": "key"
    }


def test_sticker_errors_and_success(monkeypatch):
    with pytest.raises(ValueError, match="beats required"):
        placement.place_sticker(QueueClient(), run_id="r", item={}, beats=[])

    client = QueueClient()
    client.queue("timeline", [])
    with pytest.raises(ValueError, match="no timeline"):
        placement.place_sticker(client, run_id="r", item={}, beats=[0])

    client = QueueClient()
    client.queue("timeline", [{"id": "t"}])
    client.queue("timeline_track", [])
    with pytest.raises(ValueError, match="no video track"):
        placement.place_sticker(client, run_id="r", item={}, beats=[0])

    monkeypatch.setattr(placement, "ensure_track", lambda *_a, **_k: {"id": "stickers"})
    client = QueueClient()
    client.queue("timeline", [{"id": "t"}])
    client.queue("timeline_track", [{"id": "v"}])
    client.queue("clip", [])
    with pytest.raises(ValueError, match="none of"):
        placement.place_sticker(client, run_id="r", item={}, beats=[0])

    client = QueueClient()
    client.queue("timeline", [{"id": "t"}])
    client.queue("timeline_track", [{"id": "v"}])
    client.queue(
        "clip",
        [
            {"beat_index": 0, "start_ms": 10, "duration_ms": 20},
            {"beat_index": None, "start_ms": 0, "duration_ms": 1},
        ],
    )
    monkeypatch.setattr(placement, "_ensure_slot", lambda *_a, **_k: {"id": "s"})
    monkeypatch.setattr(placement, "_artifact_from_item", lambda *_a, **_k: {"id": "a"})
    monkeypatch.setattr(placement, "create_clip", lambda *_a, **_k: {"id": "c"})
    result = placement.place_sticker(client, run_id="r", item={}, beats=[0, 9, 0])
    assert result["beats"] == [0] and result["clip_ids"] == ["c"]


def test_storage_slug_tags_and_asset_kind():
    assert storage.canonical_brand_slug(" 뷰오케이 ") == "viewok"
    assert storage.canonical_brand_slug("Hello World") == "hello-world"
    assert storage.canonical_brand_slug("미등록") == "미등록"
    assert storage.canonicalize_brand_tags(None) == []
    assert storage.canonicalize_brand_tags(
        ["brand:뷰오케이", "brand:viewok", "product:Hello World", "plain", "plain"]
    ) == ["brand:viewok", "product:hello-world", "plain"]
    assert [storage._asset_kind(m) for m in ("image/png", "video/mp4", "audio/wav", "text/plain", "x/y")] == [
        "image", "video", "audio", "text", "template"
    ]


def test_upload_artifact_and_public_url(monkeypatch):
    client = StorageClient()
    client.queue("artifact", [])
    client.queue("artifact", [{"id": "a", "storage_key": "k"}], "insert")
    client.queue("slot", [], "update")
    registered = []
    monkeypatch.setattr(storage, "register_asset_library_item", lambda *_a, **kwargs: registered.append(kwargs))
    art = storage.upload_artifact(
        client,
        run_id="r",
        slot_id="s",
        bytes_data=b"data",
        mime="image/png",
        extension="png",
        attributes={"source": "upload", "tags": ["brand:뷰오케이"]},
    )
    assert art["id"] == "a" and client.bucket.uploads
    assert registered[0]["source"] == "upload"

    client = StorageClient()
    client.queue("artifact", [{"version": 2}])
    client.queue("artifact", [{"id": "b"}], "insert")
    monkeypatch.setattr(storage, "register_asset_library_item", lambda *_a, **_k: None)
    storage.upload_artifact(
        client,
        run_id="r",
        slot_id="s",
        bytes_data=b"data",
        mime="text/plain",
        extension="txt",
        set_current=False,
    )
    assert not any(q.table_name == "slot" for q in client.executed)
    assert storage.public_url(client, "key") == "https://cdn.example/key"


def test_resolve_pool_hash_paths():
    valid = "a" * 64
    assert storage.resolve_pool_asset_sha256(StorageClient(), {"attributes": {"sha256": valid}}) == valid
    with pytest.raises(ValueError, match="no storage_key"):
        storage.resolve_pool_asset_sha256(StorageClient(), {"id": "x"})

    client = StorageClient()
    client.bucket.downloads["key"] = b"bytes"
    client.queue("asset_pool", [], "update")
    asset = {"id": "x", "storage_key": "key", "attributes": {"other": 1}}
    expected = hashlib.sha256(b"bytes").hexdigest()
    assert storage.resolve_pool_asset_sha256(client, asset) == expected
    assert asset["attributes"]["sha256"] == expected

    class BrokenClient(StorageClient):
        def table(self, name):
            if name == "asset_pool":
                raise RuntimeError("write failed")
            return super().table(name)

    client = BrokenClient()
    client.bucket.downloads["key"] = b"bytes"
    assert storage.resolve_pool_asset_sha256(client, {"id": "x", "storage_key": "key"}) == expected

    client = StorageClient()
    client.bucket.downloads["key"] = b"bytes"
    assert storage.resolve_pool_asset_sha256(client, {"storage_key": "key"}) == expected


def test_register_library_policy_validation_and_failures(monkeypatch):
    artifact = {
        "id": "a", "storage_key": "key", "sha256": "h", "mime": "image/png",
        "bytes": 10,
    }
    client = QueueClient()
    client.queue("asset_library_item", [{"id": "library"}], "upsert")
    monkeypatch.setattr(storage, "_brand_sibling_sha256s", lambda *_a: {"h"})
    result = storage.register_asset_library_item(
        client,
        run_id="r",
        artifact=artifact,
        kind="image",
        source="ai",
        reuse_scope="public",
        tags=["brand:viewok", "role:social-proof", "scope:global", "source:ai"],
        attributes={"scope": "global"},
        category="proof",
        role_code="social-proof",
    )
    assert result == {"id": "library"}
    payload = client.executed[-1].payload
    assert payload["reuse_scope"] == "project"
    assert "scope:global" not in payload["tags"]
    assert payload["attributes"]["scope"] == "project"
    assert payload["attributes"]["needs_human_code"] == "proof_is_brand_asset"
    assert payload["category"] == "proof" and payload["role_code"] == "social-proof"

    client = QueueClient()
    client.queue("asset_library_item", [], "upsert")
    assert storage.register_asset_library_item(
        client, run_id=None, artifact=artifact, kind="text", source="upload"
    ) is None

    client = QueueClient()
    client.queue("asset_library_item", [{"id": "generated"}], "upsert")
    assert storage.register_asset_library_item(
        client,
        run_id=None,
        artifact=artifact,
        kind="image",
        source="generated",
        reuse_scope="project",
        attributes={},
    ) == {"id": "generated"}

    class BrokenClient(QueueClient):
        def table(self, _name):
            raise RuntimeError("db")

    assert storage.register_asset_library_item(
        BrokenClient(), run_id=None, artifact=artifact, kind="audio", attributes={"source": "upload"}
    ) is None


def test_validate_brand_asset_and_sibling_hashes():
    assert storage.validate_brand_asset(role=None, kind="video", bytes_size=1)["code"] == "placeholder_video"
    assert storage.validate_brand_asset(
        role=None, kind="video", bytes_size=100_000, duration_ms=1
    )["code"] == "placeholder_video"
    assert storage.validate_brand_asset(
        role=None, kind="video", bytes_size=100_000, duration_ms=1_000
    )["ok"] is True
    assert storage.validate_brand_asset(
        role="social_proof", kind="image", bytes_size=1, sha256="h", sibling_asset_sha256s={"h"}
    )["code"] == "proof_is_brand_asset"
    assert storage.validate_brand_asset(
        role="logo", kind="image", bytes_size=None
    ) == {"ok": True, "code": None, "reason": None}

    assert storage._brand_sibling_sha256s(QueueClient(), "") == set()
    client = QueueClient()
    client.queue(
        "asset_library_item",
        [
            {"sha256": "logo", "tags": ["role:logo"]},
            {"sha256": "product", "tags": ["role:product"]},
            {"sha256": "other", "tags": ["role:proof"]},
            {"sha256": None, "tags": ["role:logo"]},
        ],
    )
    assert storage._brand_sibling_sha256s(client, "viewok") == {"logo", "product"}

    class BrokenClient(QueueClient):
        def table(self, _name):
            raise RuntimeError("db")

    assert storage._brand_sibling_sha256s(BrokenClient(), "viewok") == set()
