from __future__ import annotations

import hashlib

import pytest

from hiob_platform import runs

from .fakes import QueueClient


def _clean_jobs():
    return [
        {"id": kind, "kind": kind, "status": "succeeded", "attributes": {}}
        for kind in runs.REQUIRED_PRODUCTION_WORK_KINDS
    ]


def test_status_and_script_gate_helpers(monkeypatch):
    client = QueueClient()
    client.queue("run", [])
    assert runs.get_run_script_status(client, "r") is None
    client.queue("run", [{"script_status": None}])
    assert runs.get_run_script_status(client, "r") is None
    client.queue("run", [{"script_status": 3}])
    assert runs.get_run_script_status(client, "r") == "3"

    client.queue("run", [{"script_status": "approved"}])
    assert runs.assert_run_script_gate(client, "r", operation="render") == "approved"
    client.queue("run", [])
    with pytest.raises(RuntimeError, match="script approval"):
        runs.assert_run_script_gate(client, "r", operation="render", allowed={"approved"})

    client.queue("run", [{"id": "r"}], "update")
    assert runs.set_run_script_status(client, "r", "produced") == {"id": "r"}
    client.queue("run", [], "update")
    assert runs.set_run_script_status(client, "r", "produced") == {}

    assert runs._env_flag({"X": " TRUE "}, "X") == "true"
    monkeypatch.setenv("X", "ON")
    assert runs._env_flag(None, "X") == "on"


def test_editor_approval_attribute_shapes():
    client = QueueClient()
    client.queue("run", [{"attributes": "bad"}])
    with pytest.raises(RuntimeError, match="EDITOR_APPROVAL_REQUIRED"):
        runs.assert_editor_approved(client, "r", env={})
    client.queue("run", [{"attributes": {"editor_approved_at": 123}}])
    assert runs.assert_editor_approved(client, "r", env={}) == "123"


def test_production_job_writes_all_payload_shapes():
    assert runs.update_production_job(QueueClient(), None, status="running") == {}

    client = QueueClient()
    client.queue("production_jobs", [{"id": "j"}], "update")
    out = runs.update_production_job(
        client,
        "j",
        status="running",
        span_id="s",
        modal_call_id="m",
        error={"message": "x"},
        attributes={"a": 1},
    )
    assert out == {"id": "j"}
    payload = client.executed[-1].payload
    assert payload["started_at"] == "now()" and "ended_at" not in payload
    assert payload["span_id"] == "s" and payload["attributes"] == {"a": 1}

    client.queue("production_jobs", [], "update")
    assert runs.update_production_job(client, "j", status="failed") == {}
    assert client.executed[-1].payload["ended_at"] == "now()"
    client.queue("production_jobs", [], "update")
    runs.update_production_job(client, "j", status="queued")
    assert "started_at" not in client.executed[-1].payload and "ended_at" not in client.executed[-1].payload

    client = QueueClient()
    client.queue("production_jobs", [{"id": "j"}], "insert")
    assert runs.create_production_job(client, run_id="r", kind="visual")["id"] == "j"
    assert client.executed[-1].payload["target"] == {}
    client.queue("production_jobs", [{"id": "k"}], "insert")
    runs.create_production_job(
        client, run_id="r", kind="music", script_candidate_id="c", target={"beat": 1}
    )
    assert client.executed[-1].payload["script_candidate_id"] == "c"


def test_render_patch_and_scalar_predicates():
    assert runs.render_job_patch_after_compose({"mp4_url": " https://x ", "duration_s": None}) == {
        "status": "done", "output_url": "https://x", "completed_at": "now()"
    }
    pending = runs.render_job_patch_after_compose(None)
    assert pending["status"] == "processing" and "duration_s" not in pending
    pending = runs.render_job_patch_after_compose({"duration_s": 4})
    assert pending["duration_s"] == 4

    assert runs._as_nonneg_int(-1) == 0
    assert runs._as_nonneg_int("3") == 3
    assert runs._as_nonneg_int(object()) == 0

    assert not runs._is_skip_reason_soft_fail(None)
    assert not runs._is_skip_reason_soft_fail(False)
    assert not runs._is_skip_reason_soft_fail(2)
    assert not runs._is_skip_reason_soft_fail(2.2)
    assert not runs._is_skip_reason_soft_fail([])
    assert not runs._is_skip_reason_soft_fail(())
    assert not runs._is_skip_reason_soft_fail({"x": 1})
    assert runs._is_skip_reason_soft_fail(True)
    assert runs._is_skip_reason_soft_fail(" reason ")
    assert not runs._is_skip_reason_soft_fail(" ")

    assert not runs.is_visual_worker_success(None)
    assert not runs.is_visual_worker_success({"error": "x"})
    assert not runs.is_visual_worker_success({"visuals": "bad", "reused": -1})
    assert runs.is_visual_worker_success({})
    assert runs._first_work_count({}) == 0
    assert not runs._sfx_result_is_success({"failed": ["x"]})


@pytest.mark.parametrize(
    ("out", "kind", "expected"),
    [
        (None, "", False),
        ({"error": "x"}, "", False),
        ({"skip_reason": "empty"}, "music", False),
        ({"status": "empty_pool"}, "music", False),
        ({"status": "no_candidates"}, "music", False),
        ({"skipped": True}, "music", False),
        ({"skipped": {"reason": "odd"}}, "music", False),
        ({"failed": ["x"], "voiceovers": 0}, "voiceover", False),
        ({"failed": ["x"], "visuals": 1}, "visual", True),
        ({"failed": ["x"], "created": 1}, "sfx", True),
        ({}, "visual", True),
        ({}, "voiceover", True),
        ({"voiceovers": 0, "skipped": [1]}, "voiceover", True),
        ({}, "sfx", True),
        ({"created": 1}, "sfx", True),
        ({"created": 0, "failed": ["x"]}, "sfx", False),
        ({"created": 0, "skipped": [1]}, "sfx", True),
        ({"created": 0}, "sfx", False),
        ({"artifact_id": "a"}, "music", True),
        ({"ok": True}, "music", True),
        ({}, "music", True),
        ({"created": 0}, "music", False),
        ({"anything": 1}, "unknown", True),
    ],
)
def test_media_worker_predicate_matrix(out, kind, expected):
    assert runs.media_worker_result_is_success(out, kind=kind) is expected


def test_media_row_predicate_matrix():
    assert not runs.media_job_row_is_clean_success(None)
    assert not runs.media_job_row_is_clean_success({"status": "running"})
    assert runs.media_job_row_is_clean_success(
        {"status": "succeeded", "kind": "music", "attributes": []}
    )
    assert not runs.media_job_row_is_clean_success(
        {"status": "succeeded", "attributes": {"skipped": "reason"}}
    )
    assert not runs.media_job_row_is_clean_success(
        {"status": "succeeded", "attributes": {"skip_reason": "reason"}}
    )
    assert not runs.media_job_row_is_clean_success(
        {"status": "succeeded", "kind": "sfx", "attributes": {"created": 0}}
    )


def test_run_failure_and_produced_terminal_paths(monkeypatch):
    client = QueueClient()
    client.queue("run", [], "update")
    out = runs.mark_run_media_failed(client, "r", reason="x" * 600)
    assert out["status"] == "failed" and len(out["attributes"]["produce_error"]) == 500
    assert len(client.rpc_calls) == 3

    client = QueueClient()
    client.queue("run", [{"id": "r", "attributes": "bad"}], "update")
    out = runs.mark_run_media_failed(client, "r", reason="bad")
    assert out["attributes"]["fail_loud"] is True

    client = QueueClient()
    client.queue("production_jobs", [])
    assert runs.maybe_mark_run_produced(client, "r") == {}

    client = QueueClient()
    client.queue("production_jobs", [{"kind": "unknown", "status": "succeeded"}])
    assert runs.maybe_mark_run_produced(client, "r") == {}

    client = QueueClient()
    queued = _clean_jobs()
    queued[0]["status"] = "running"
    client.queue("production_jobs", queued)
    assert runs.maybe_mark_run_produced(client, "r") == {}

    client = QueueClient()
    failed = _clean_jobs()
    failed[0]["status"] = "failed"
    client.queue("production_jobs", failed)
    monkeypatch.setattr(runs, "mark_run_media_failed", lambda *_a, **kw: {"reason": kw["reason"]})
    assert "required media jobs failed" in runs.maybe_mark_run_produced(client, "r")["reason"]

    client = QueueClient()
    unclean = _clean_jobs()
    unclean[0]["attributes"] = {"error": "soft"}
    client.queue("production_jobs", unclean)
    assert runs.maybe_mark_run_produced(client, "r") == {}

    client = QueueClient()
    rows = _clean_jobs() + [
        {"kind": "caption", "status": "succeeded", "attributes": {}},
        {"kind": "visual", "status": "failed", "attributes": {}},
    ]
    client.queue("production_jobs", rows)
    monkeypatch.setattr(runs, "mark_preview_produced", lambda *_a: {"produced": True})
    assert runs.maybe_mark_run_produced(client, "r") == {"produced": True}


def test_run_span_slot_and_text_writers():
    client = QueueClient()
    client.queue("run", [{"id": "r"}], "update")
    assert runs.end_run(client, "r", note="done") == {"id": "r"}
    client.queue("run", [], "update")
    assert runs.end_run(client, "r") == {}

    client.queue("span", [{"id": "s"}], "insert")
    assert runs.start_span(
        client, run_id="r", name="n", kind="k", service="svc"
    ) == {"id": "s"}
    assert client.executed[-1].payload["attributes"] == {}

    client.queue("span", [{"attributes": {"old": 1}}])
    client.queue("span", [{"id": "s", "attributes": {"old": 1, "new": 2}}], "update")
    out = runs.end_span(
        client,
        "s",
        status="error",
        output_preview="preview",
        error={"message": "x"},
        attributes_patch={"new": 2},
    )
    assert out["attributes"]["old"] == 1
    payload = client.executed[-1].payload
    assert payload["attributes"] == {"old": 1, "new": 2}

    client.queue("span", [])
    client.queue("span", [], "update")
    assert runs.end_span(client, "s", attributes_patch={"new": 2}) == {}
    client.queue("span", [], "update")
    runs.end_span(client, "s")
    assert "attributes" not in client.executed[-1].payload

    client.queue("slot", [{"id": "slot"}], "insert")
    assert runs.create_slot(
        client, run_id="r", track="visual", start_ms=0, end_ms=1
    )["id"] == "slot"

    client.queue("artifact", [{"id": "a"}], "insert")
    client.queue("slot", [], "update")
    art = runs.create_text_artifact(
        client,
        run_id="r",
        slot_id="slot",
        text="hello",
        role_code="writer",
        category="script",
        attributes={"x": 1},
    )
    assert art["id"] == "a"
    payload = next(q.payload for q in client.executed if q.table_name == "artifact")
    assert payload["sha256"] == hashlib.sha256(b"hello").hexdigest()
    assert payload["role_code"] == "writer"

    client = QueueClient()
    client.queue("artifact", RuntimeError("old columns"), "insert")
    client.queue("artifact", [{"id": "legacy"}], "insert")
    client.queue("slot", [], "update")
    runs.create_text_artifact(
        client,
        run_id="r",
        slot_id="slot",
        text="hello",
        sha256="f" * 64,
    )
    assert "role_code" not in client.executed[-2].payload
