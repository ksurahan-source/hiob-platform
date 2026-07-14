"""Produce-path honesty: visual soft-fail, maybe_mark fail-loud, render_job URL gate.

Drives shipped helpers in hiob_platform.runs — no reimplementation of rules in tests.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hiob_platform.runs import (  # noqa: E402
    is_visual_worker_success,
    mark_preview_produced,
    media_job_row_is_clean_success,
    media_worker_result_is_success,
    maybe_mark_run_produced,
    render_job_patch_after_compose,
)
from hiob_platform.render_gate import check_run_render_ready  # noqa: E402


class _FakeQuery:
    def __init__(self, client, table):
        self.client = client
        self.table_name = table
        self._filters = {}
        self._in_kind = None
        self._payload = None
        self._op = "select"
        self._select = "*"

    def select(self, cols):
        self._select = cols
        return self

    def eq(self, k, v):
        self._filters[k] = v
        return self

    def in_(self, k, vals):
        self._in_kind = (k, list(vals))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, n):
        return self

    def update(self, payload):
        self._op = "update"
        self._payload = payload
        return self

    def execute(self):
        if self.table_name == "production_jobs" and self._op == "select":
            run_id = self._filters.get("run_id")
            rows = [r for r in self.client.jobs if r.get("run_id") == run_id]
            if self._in_kind:
                key, vals = self._in_kind
                rows = [r for r in rows if r.get(key) in vals]
            # newest first already stored that way
            return type("R", (), {"data": rows})()
        if self.table_name == "run" and self._op == "update":
            run_id = self._filters.get("id")
            for r in self.client.runs:
                if r["id"] == run_id:
                    r.update(self._payload)
                    self.client.last_run_update = dict(r)
                    return type("R", (), {"data": [r]})()
            return type("R", (), {"data": []})()
        if self.table_name == "slot" and self._op == "select":
            run_id = self._filters.get("run_id")
            rows = [s for s in self.client.slots if s.get("run_id") == run_id]
            return type("R", (), {"data": rows})()
        return type("R", (), {"data": []})()


class FakeClient:
    def __init__(self):
        self.jobs = []
        self.runs = []
        self.slots = []
        self.last_run_update = None

    def table(self, name):
        return _FakeQuery(self, name)


def test_is_visual_worker_success_rejects_error_dict():
    assert is_visual_worker_success({"error": "cost abort", "visuals": 0}) is False
    assert is_visual_worker_success({"visuals": 0}) is False
    assert is_visual_worker_success({"visuals": 3}) is True
    assert is_visual_worker_success({"ok": True}) is True


def test_media_worker_result_is_success_rejects_skipped_and_total_fail():
    assert media_worker_result_is_success({"ok": True}, kind="music") is True
    assert media_worker_result_is_success({"skipped": "empty_music_pool"}, kind="music") is False
    assert media_worker_result_is_success({"error": "x"}, kind="sfx") is False
    assert media_worker_result_is_success(
        {"failed": ["cue0"], "created": 0}, kind="sfx"
    ) is False
    assert media_worker_result_is_success(
        {"failed": ["cue0"], "created": 2}, kind="sfx"
    ) is True
    assert media_worker_result_is_success({"visuals": 0}, kind="visual") is False


def test_media_job_row_is_clean_success():
    assert media_job_row_is_clean_success({"status": "succeeded", "kind": "music", "attributes": {}})
    assert not media_job_row_is_clean_success({"status": "failed", "kind": "visual"})
    assert not media_job_row_is_clean_success(
        {"status": "succeeded", "kind": "visual", "attributes": {"error": "x", "visuals": 0}}
    )
    assert not media_job_row_is_clean_success(
        {"status": "succeeded", "kind": "visual", "attributes": {"visuals": 0}}
    )
    assert not media_job_row_is_clean_success(
        {"status": "succeeded", "kind": "music", "attributes": {"skipped": "empty_music_pool"}}
    )


def test_maybe_mark_run_produced_blocks_soft_visual_success():
    c = FakeClient()
    c.runs = [{"id": "r1", "script_status": "approved", "status": "running"}]
    c.jobs = [
        {"id": "j1", "run_id": "r1", "kind": "visual", "status": "succeeded", "attributes": {"error": "abort", "visuals": 0}},
        {"id": "j2", "run_id": "r1", "kind": "voiceover", "status": "succeeded", "attributes": {}},
        {"id": "j3", "run_id": "r1", "kind": "music", "status": "succeeded", "attributes": {}},
        {"id": "j4", "run_id": "r1", "kind": "sfx", "status": "succeeded", "attributes": {}},
    ]
    out = maybe_mark_run_produced(c, "r1")
    assert out == {}
    assert c.runs[0].get("script_status") == "approved"  # unchanged


def test_maybe_mark_run_produced_fails_run_on_required_job_failed():
    c = FakeClient()
    c.runs = [{"id": "r1", "script_status": "queued", "status": "running", "attributes": {}}]
    c.jobs = [
        {"id": "j1", "run_id": "r1", "kind": "visual", "status": "failed", "attributes": {}},
        {"id": "j2", "run_id": "r1", "kind": "voiceover", "status": "succeeded", "attributes": {}},
        {"id": "j3", "run_id": "r1", "kind": "music", "status": "succeeded", "attributes": {}},
        {"id": "j4", "run_id": "r1", "kind": "sfx", "status": "succeeded", "attributes": {}},
    ]
    out = maybe_mark_run_produced(c, "r1")
    assert out.get("status") == "failed"
    assert out.get("script_status") == "failed"
    assert "visual" in str(out.get("attributes", {}).get("produce_error", ""))


def test_maybe_mark_run_produced_marks_preview_only_on_clean_success():
    c = FakeClient()
    c.runs = [{"id": "r1", "script_status": "approved", "status": "running"}]
    c.jobs = [
        {"id": "j1", "run_id": "r1", "kind": "visual", "status": "succeeded", "attributes": {"visuals": 4}},
        {"id": "j2", "run_id": "r1", "kind": "voiceover", "status": "succeeded", "attributes": {}},
        {"id": "j3", "run_id": "r1", "kind": "music", "status": "succeeded", "attributes": {}},
        {"id": "j4", "run_id": "r1", "kind": "sfx", "status": "succeeded", "attributes": {}},
    ]
    out = maybe_mark_run_produced(c, "r1")
    assert out.get("script_status") == "produced"
    # mark_preview_produced only touches script_status
    assert c.runs[0].get("script_status") == "produced"


def test_render_job_patch_requires_url_for_done():
    done = render_job_patch_after_compose({"output_url": "https://cdn.example/a.mp4", "duration_s": 12})
    assert done["status"] == "done"
    assert done["output_url"].startswith("https://")

    pending = render_job_patch_after_compose({"snapshot_id": "s1", "render_dispatched": True})
    assert pending["status"] == "processing"
    assert pending.get("output_url") is None
    assert pending["metadata"]["awaiting_output_url"] is True


def test_check_run_render_ready_blocks_missing_voice_and_visual():
    c = FakeClient()
    c.slots = [
        {"run_id": "r1", "track": "visual", "beat_index": 0, "current_artifact_id": "a1"},
        {"run_id": "r1", "track": "visual", "beat_index": 1, "current_artifact_id": None},
        {"run_id": "r1", "track": "voiceover", "beat_index": 0, "current_artifact_id": "v1"},
        # beat 1 missing voice
    ]
    r = check_run_render_ready(c, "r1", require_voice_per_beat=True)
    assert r.ok is False
    assert any("보이스" in v or "P1" in v for v in r.violations)
    assert any("비주얼" in v for v in r.violations)


def test_check_run_render_ready_ok_when_bound():
    c = FakeClient()
    c.slots = [
        {"run_id": "r1", "track": "visual", "beat_index": 0, "current_artifact_id": "a1"},
        {"run_id": "r1", "track": "voiceover", "beat_index": 0, "current_artifact_id": "v1"},
        {"run_id": "r1", "track": "music", "beat_index": None, "current_artifact_id": "m1"},
    ]
    r = check_run_render_ready(c, "r1", require_voice_per_beat=True)
    assert r.ok is True
    assert r.violations == ()


def test_mark_preview_produced_does_not_set_run_succeeded():
    c = FakeClient()
    c.runs = [{"id": "r1", "script_status": "approved", "status": "running"}]
    out = mark_preview_produced(c, "r1")
    assert out.get("script_status") == "produced"
    assert c.runs[0].get("status") == "running"
