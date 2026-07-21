"""SEC-C2: assert_editor_approved mirrors Studio editorGate.js contract."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hiob_platform.runs import (  # noqa: E402
    assert_editor_approved,
    is_editor_gate_enabled,
    is_editor_gate_legacy_allow,
)


class _FakeResult:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        return _FakeResult(self._rows)


class _FakeClient:
    def __init__(self, attributes=None, missing=False):
        self.missing = missing
        self.attributes = attributes if attributes is not None else {}

    def table(self, name):
        assert name == "run"
        if self.missing:
            return _FakeQuery([])
        return _FakeQuery([{"id": "run-1", "attributes": self.attributes}])


def test_gate_enabled_default():
    assert is_editor_gate_enabled({}) is True
    assert is_editor_gate_enabled({"HIOB_EDITOR_GATE": "0"}) is False
    assert is_editor_gate_legacy_allow({}) is False
    assert is_editor_gate_legacy_allow({"HIOB_EDITOR_GATE_LEGACY_ALLOW": "1"}) is True


def test_missing_approval_raises():
    with pytest.raises(RuntimeError, match="EDITOR_APPROVAL_REQUIRED"):
        assert_editor_approved(_FakeClient({}), "run-1", env={})


def test_approved_returns_timestamp():
    at = "2026-07-22T00:00:00Z"
    out = assert_editor_approved(
        _FakeClient({"editor_approved_at": at, "editor_approved_by": "studio"}),
        "run-1",
        env={},
    )
    assert out == at


def test_bypass_when_gate_off():
    out = assert_editor_approved(
        _FakeClient({}),
        "run-1",
        env={"HIOB_EDITOR_GATE": "0"},
    )
    assert out == "bypassed"


def test_legacy_allow():
    out = assert_editor_approved(
        _FakeClient({}),
        "run-1",
        env={"HIOB_EDITOR_GATE_LEGACY_ALLOW": "1"},
    )
    assert out == "legacy"


def test_missing_run_row_treated_as_unapproved():
    with pytest.raises(RuntimeError, match="EDITOR_APPROVAL_REQUIRED"):
        assert_editor_approved(_FakeClient(missing=True), "run-missing", env={})
