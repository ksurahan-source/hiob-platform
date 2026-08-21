from __future__ import annotations

import builtins

import pytest

from hiob_platform import role_artifacts as roles

from .fakes import QueueClient


def test_value_helpers():
    assert roles._as_list(None) == []
    assert roles._as_list([1]) == [1]
    assert roles._as_list(1) == [1]
    assert roles._stringify(None) == ""
    assert roles._stringify(" x ") == "x"
    assert roles._stringify(True) == "True"
    assert roles._stringify({"x": 1}).startswith("{")

    class Bad:
        def __str__(self):
            return "bad"

    original = roles.json.dumps
    roles.json.dumps = lambda *_a, **_k: (_ for _ in ()).throw(TypeError("bad"))
    try:
        assert roles._stringify(Bad()) == "bad"
    finally:
        roles.json.dumps = original
    assert roles._truncate_title("a  b", 4) == "a b"
    assert roles._truncate_title("abcdef", 4) == "abc…"


@pytest.mark.parametrize("extractor", roles.EXTRACTORS.values())
def test_all_extractors_accept_empty_output(extractor):
    assert extractor({}) == []


def test_pd_research_marketer_extractors():
    pd = roles.extract_pd(
        {"goal": "sell", "audience": "swimmers", "constraints": ["safe", {"x": 1}]}
    )
    assert pd[0].category == roles.CATEGORY_BRIEF and "constraints" in pd[0].text
    assert roles.extract_pd({"unknown": "value"})[0].text.startswith("{")

    research = roles.extract_researcher(
        {
            "key_facts": ["fact", None],
            "references": ["ref"],
            "hooks": ["hook"],
            "risks": ["risk"],
        }
    )
    assert {item.category for item in research} == {
        roles.CATEGORY_RESEARCH,
        roles.CATEGORY_HOOK,
        roles.CATEGORY_NOTES,
    }
    assert roles.extract_researcher(
        {"key_facts": [None], "references": [None], "hooks": [None], "risks": [None]}
    ) == []

    marketing = roles.extract_marketer(
        {
            "hook": "hook",
            "value_prop_one_liner": "value",
            "cta": "buy",
            "arc": ["start", "finish"],
        }
    )
    assert len(marketing) == 4
    assert roles.extract_marketer({"arc": []}) == []


def test_script_visual_sound_editor_extractors():
    script = roles.extract_scriptwriter(
        {"notes": ["note", 2], "alternates": ["alt", None]}
    )
    assert len(script) == 2 and script[-1].attributes["is_alternate"]
    assert roles.extract_scriptwriter({"notes": [], "alternates": []}) == []

    visual = roles.extract_art_director(
        {"style_bible": {"mood": "blue"}, "prompts": ["p", None]}
    )
    assert visual[0].kind == "template" and len(visual) == 2
    assert len(roles.extract_art_director({"prompts": ["p"]})) == 1

    sound = roles.extract_sound_designer(
        {
            "music_vibe": "bright",
            "music_bpm": 120,
            "sfx_cues": ["bad", {"beat": 1, "cue": "pop"}, {"cue": "whoosh"}, {"cue": None}],
        }
    )
    assert len(sound) == 3 and "beat 1" in sound[1].title
    assert roles.extract_sound_designer({"sfx_cues": []}) == []

    editor = roles.extract_editor(
        {
            "cuts": ["bad", {"beat": 0, "transition": "cut", "duration_ms": 10}, {"beat": 1, "transition": "fade"}],
            "notes": ["tight"],
        }
    )
    assert len(editor) == 2 and "10ms" in editor[0].text
    assert roles.extract_editor({"cuts": ["bad"], "notes": []}) == []
    assert len(roles.extract_editor({"notes": ["note"]})) == 1


def test_qa_and_team_leader_extractors():
    qa = roles.extract_qa(
        {"score": 9, "passed": False, "blockers": ["b"], "warnings": ["w"]}
    )[0]
    assert "passed: False" in qa.text and qa.category == roles.CATEGORY_QA
    assert roles.extract_qa({"misc": 1})[0].text.startswith("{")

    leader = roles.extract_team_leader(
        {
            "reasoning": "because",
            "selected_roles": ["pd"],
            "expected_artifacts": [{"role": "pd"}],
            "risks": ["late"],
        }
    )[0]
    assert "expected_artifacts" in leader.text
    assert roles.extract_team_leader({"misc": 1})[0].text.startswith("{")


def test_ensure_meta_slot_paths():
    client = QueueClient()
    client.queue("slot", [{"id": "existing"}])
    assert roles._ensure_meta_slot(client, "r", "pd") == "existing"

    client = QueueClient()
    client.queue("slot", RuntimeError("old schema"))
    client.queue("slot", [{"id": "inserted"}], "insert")
    assert roles._ensure_meta_slot(client, "r", "pd") == "inserted"

    client = QueueClient()
    client.queue("slot", [])
    client.queue("slot", [], "insert")
    assert roles._ensure_meta_slot(client, "r", "pd") is None

    client = QueueClient()
    client.queue("slot", [])
    client.queue("slot", RuntimeError("insert"), "insert")
    assert roles._ensure_meta_slot(client, "r", "pd") is None


def test_backfill_role_category_paths():
    complete = {
        "id": "a", "role_code": "pd", "category": "brief", "attributes": {"x": 1}
    }
    assert roles._backfill_role_category(
        QueueClient(), complete, role_code="pd", category="brief", attrs={"x": 1}
    ) is complete

    client = QueueClient()
    client.queue("artifact", [{"id": "a", "role_code": "pd", "category": "brief"}], "update")
    out = roles._backfill_role_category(
        client, {"id": "a"}, role_code="pd", category="brief", attrs={"x": 1}
    )
    assert out["role_code"] == "pd"

    client = QueueClient()
    client.queue("artifact", RuntimeError("columns"), "update")
    client.queue("artifact", [], "update")
    out = roles._backfill_role_category(
        client,
        {"id": "a", "role_code": "pd", "attributes": {"old": 1}},
        role_code="pd",
        category="brief",
        attrs={"new": 2},
    )
    assert out["category"] == "brief" and out["attributes"] == {"old": 1, "new": 2}

    client = QueueClient()
    client.queue("artifact", RuntimeError("columns"), "update")
    client.queue("artifact", RuntimeError("again"), "update")
    assert roles._backfill_role_category(
        client, {"id": "a"}, role_code="pd", category="brief", attrs={}
    )["role_code"] == "pd"

    client = QueueClient()
    client.queue("artifact", [], "update")
    out = roles._backfill_role_category(
        client,
        {"id": "a", "role_code": "pd", "category": "brief", "attributes": {"old": 1}},
        role_code="pd",
        category="brief",
        attrs={"new": 2},
    )
    assert out["attributes"] == {"old": 1, "new": 2}


def test_insert_text_artifact_select_fallbacks(monkeypatch):
    registered = []
    monkeypatch.setattr(roles, "register_asset_library_item", lambda *_a, **kw: registered.append(kw))
    monkeypatch.setattr(roles, "_backfill_role_category", lambda _c, art, **_k: {**art, "filled": True})

    client = QueueClient()
    client.queue("artifact", [{"id": "a", "storage_key": "k", "sha256": "h", "mime": "text/plain"}])
    client.queue("asset_library_item", [], "update")
    out = roles._insert_text_artifact(
        client, run_id="r", role_code="pd", category="brief", kind="text",
        text="hello", title="title", source_call_id="call", attributes={"beat": 1},
    )
    assert out["filled"] and registered

    client = QueueClient()
    client.queue("artifact", RuntimeError("new columns"))
    client.queue("artifact", [{"id": "legacy", "storage_key": "k", "sha256": "h", "mime": "text/plain"}])
    client.queue("asset_library_item", RuntimeError("old library"), "update")
    assert roles._insert_text_artifact(
        client, run_id="r", role_code="pd", category="brief", kind="text",
        text="hello", title="title", source_call_id=None, attributes=None,
    )["id"] == "legacy"

    monkeypatch.setattr(roles, "_insert_artifact_resilient", lambda *_a, **_k: {"id": "new", "storage_key": "k", "sha256": "h", "mime": "text/plain"})
    client = QueueClient()
    client.queue("artifact", RuntimeError("one"))
    client.queue("artifact", RuntimeError("two"))
    client.queue("asset_library_item", [], "update")
    assert roles._insert_text_artifact(
        client, run_id="r", role_code="pd", category="brief", kind="text",
        text="hello", title="title", source_call_id=None, attributes=None,
    )["id"] == "new"

    monkeypatch.setattr(roles, "_insert_artifact_resilient", lambda *_a, **_k: None)
    client = QueueClient()
    client.queue("artifact", [])
    assert roles._insert_text_artifact(
        client, run_id="r", role_code="pd", category="brief", kind="text",
        text="hello", title="title", source_call_id=None, attributes=None,
    ) is None


def test_insert_artifact_resilient_all_passes(monkeypatch, capsys):
    payload = {"role_code": "pd", "category": "brief", "attributes": {}}
    client = QueueClient()
    client.queue("artifact", [{"id": "first"}], "insert")
    assert roles._insert_artifact_resilient(
        client, run_id="r", role_code="pd", payload=payload, attrs={}
    )["id"] == "first"

    client = QueueClient()
    client.queue("artifact", RuntimeError("slot needed"), "insert")
    client.queue("artifact", [{"id": "second"}], "insert")
    monkeypatch.setattr(roles, "_ensure_meta_slot", lambda *_a: "slot")
    assert roles._insert_artifact_resilient(
        client, run_id="r", role_code="pd", payload=payload, attrs={}
    )["id"] == "second"

    client = QueueClient()
    client.queue("artifact", RuntimeError("first"), "insert")
    client.queue("artifact", RuntimeError("second"), "insert")
    client.queue("artifact", [{"id": "legacy"}], "insert")
    assert roles._insert_artifact_resilient(
        client, run_id="r", role_code="pd", payload=payload, attrs={"x": 1}
    )["id"] == "legacy"
    assert "role_code" not in client.executed[-1].payload

    slots = iter([None, "retry-slot"])
    monkeypatch.setattr(roles, "_ensure_meta_slot", lambda *_a: next(slots))
    client = QueueClient()
    client.queue("artifact", RuntimeError("first"), "insert")
    client.queue("artifact", [{"id": "retry"}], "insert")
    assert roles._insert_artifact_resilient(
        client, run_id="r", role_code="pd", payload=payload, attrs={}
    )["id"] == "retry"
    assert client.executed[-1].payload["slot_id"] == "retry-slot"

    monkeypatch.setattr(roles, "_ensure_meta_slot", lambda *_a: None)
    client = QueueClient()
    client.queue("artifact", RuntimeError("first"), "insert")
    client.queue("artifact", RuntimeError("last"), "insert")
    assert roles._insert_artifact_resilient(
        client, run_id="r", role_code="pd", payload=payload, attrs={}
    ) is None
    assert "insert failed" in capsys.readouterr().err

    monkeypatch.setattr(builtins, "print", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("print")))
    client = QueueClient()
    client.queue("artifact", RuntimeError("first"), "insert")
    client.queue("artifact", RuntimeError("last"), "insert")
    assert roles._insert_artifact_resilient(
        client, run_id="r", role_code="pd", payload=payload, attrs={}
    ) is None


def test_materialize_role_outputs_terminal_paths(monkeypatch):
    assert roles.materialize_role_outputs(
        QueueClient(), run_id="r", role_code="pd", call_id=None, output=None
    ) == []
    assert roles.materialize_role_outputs(
        QueueClient(), run_id="r", role_code="unknown", call_id=None, output={"x": 1}
    ) == []

    monkeypatch.setitem(roles.EXTRACTORS, "broken", lambda _o: (_ for _ in ()).throw(RuntimeError("bad")))
    assert roles.materialize_role_outputs(
        QueueClient(), run_id="r", role_code="broken", call_id=None, output={"x": 1}
    ) == []

    monkeypatch.setitem(
        roles.EXTRACTORS,
        "custom",
        lambda _o: [
            roles.RoleArtifact(text="", title="empty", category="notes"),
            roles.RoleArtifact(text="one", title="one", category="notes"),
            roles.RoleArtifact(text="two", title="two", category="notes"),
            roles.RoleArtifact(text="three", title="three", category="notes"),
        ],
    )
    outcomes = iter([{"id": "one"}, None, RuntimeError("write")])

    def insert(*_a, **_k):
        value = next(outcomes)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(roles, "_insert_text_artifact", insert)
    assert roles.materialize_role_outputs(
        QueueClient(), run_id="r", role_code="custom", call_id="c", output={"x": 1}
    ) == [{"id": "one"}]


def test_expected_and_actual_diff_normalization():
    assert roles.expected_artifacts_from_team_leader(None) == []
    assert roles.expected_artifacts_from_team_leader({"expected_artifacts": "bad"}) == []
    expected = roles.expected_artifacts_from_team_leader(
        {
            "expected_artifacts": [
                "bad",
                {"role": "", "category": ""},
                {"role": " pd ", "count": "2"},
                {"category": "qa", "count": None},
                {"category": "notes", "count": "bad"},
                {"role": "editor", "count": 0},
            ]
        }
    )
    assert [item["count"] for item in expected] == [2, 1, 1, 1]

    actual = [
        {"role_code": "pd", "category": "brief"},
        {"attributes": {"role_code": "pd", "category": "qa"}},
        {"attributes": {}},
    ]
    gaps = roles.diff_expected_vs_actual(expected, actual)
    assert any(gap.get("category") == "notes" and gap["missing"] == 1 for gap in gaps)
    assert not any(gap.get("role") == "pd" for gap in gaps)
