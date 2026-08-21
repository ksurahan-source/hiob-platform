from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import hiob_platform
from hiob_platform import agent_outputs, brand_kit, client, pronunciation
from hiob_platform.render_gate import check_run_render_ready
from hiob_platform.schemas import validate_production_job_update

from .fakes import QueueClient

notify = importlib.import_module("hiob_platform.notify")


def test_latest_art_director_output_all_shapes():
    sb = QueueClient()
    sb.queue("agent_team", [])
    assert agent_outputs._latest_art_director_output(sb, "r") == {}

    sb.queue("agent_team", [{"id": "t"}])
    sb.queue("agent_call", [])
    assert agent_outputs._latest_art_director_output(sb, "r") == {}

    sb.queue("agent_team", [{"id": "t"}])
    sb.queue("agent_call", [{"id": "c", "output": {"shot": 1}}])
    assert agent_outputs._latest_art_director_output(sb, "r") == {
        "shot": 1,
        "_call_id": "c",
    }

    sb.queue("agent_team", [{"id": "t"}])
    sb.queue("agent_call", [{"id": "c", "output": "bad"}])
    assert agent_outputs._latest_art_director_output(sb, "r") == {}


class _HttpClient:
    response = SimpleNamespace(status_code=200, json=lambda: {"ok": True, "kit": {"colors": {"primary": "#fff"}}})
    error: Exception | None = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def get(self, *_args, **_kwargs):
        if self.error:
            raise self.error
        return self.response


def _clear_brand_cache():
    brand_kit.get_brand_kit.cache_clear()


def test_brand_kit_fetch_and_fallbacks(monkeypatch, capsys):
    _clear_brand_cache()
    monkeypatch.delenv("SUPABASE_SECRET_KEY", raising=False)
    assert brand_kit.get_brand_kit("x") is None

    _clear_brand_cache()
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "secret")
    monkeypatch.setenv("STUDIO_URL", "https://studio.example")
    monkeypatch.setattr(brand_kit.httpx, "Client", _HttpClient)
    _HttpClient.error = None
    _HttpClient.response = SimpleNamespace(
        status_code=200,
        json=lambda: {"ok": True, "kit": {"colors": {"primary": "#fff"}}},
    )
    assert brand_kit.get_brand_kit("x")["colors"]["primary"] == "#fff"

    _clear_brand_cache()
    _HttpClient.response = SimpleNamespace(status_code=404, json=lambda: {})
    assert brand_kit.get_brand_kit("missing") is None

    _clear_brand_cache()
    _HttpClient.response = SimpleNamespace(status_code=200, json=lambda: {"ok": False})
    assert brand_kit.get_brand_kit("inactive") is None

    _clear_brand_cache()
    _HttpClient.error = RuntimeError("offline")
    assert brand_kit.get_brand_kit("broken") is None
    assert "offline" in capsys.readouterr().out

    assert brand_kit.resolve_color(None, "primary", "fallback") == "fallback"
    assert brand_kit.resolve_color({"colors": {"primary": "red"}}, "primary") == "red"
    assert brand_kit.resolve_color({"colors": {}}, "primary", "fallback") == "fallback"
    assert brand_kit.resolve_font_family(None) == "Noto Sans KR"
    assert brand_kit.resolve_font_family({"fonts": {"body": {"family": "Inter"}}}) == "Inter"
    assert brand_kit.resolve_font_family({"fonts": {}}, fallback="Fallback") == "Fallback"


def test_service_client_environment(monkeypatch):
    monkeypatch.delenv("SUPABASE_SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="SUPABASE_SECRET_KEY"):
        client._required("SUPABASE_SECRET_KEY")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "key")
    assert client._required("SUPABASE_SECRET_KEY") == "key"

    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("NEXT_PUBLIC_SUPABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="SUPABASE_URL"):
        client._required_url()
    monkeypatch.setenv("NEXT_PUBLIC_SUPABASE_URL", "https://fallback.example")
    assert client._required_url() == "https://fallback.example"
    monkeypatch.setenv("SUPABASE_URL", "https://primary.example")
    assert client._required_url() == "https://primary.example"

    sentinel = object()
    monkeypatch.setattr(client, "create_client", lambda url, key: (url, key, sentinel))
    assert client.get_service_client() == ("https://primary.example", "key", sentinel)


def test_notify_payloads_and_wrappers(monkeypatch):
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    assert notify.notify("info", title="x") is False

    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://hook.example")
    sent = []

    def post(url, *, json, timeout):
        sent.append((url, json, timeout))
        return SimpleNamespace(status_code=204)

    monkeypatch.setattr(notify.httpx, "post", post)
    assert notify.notify(
        "unknown",
        title="title",
        fields={"short": "x", "long": "y" * 1001},
        url="https://run.example",
    )
    embed = sent[-1][1]["embeds"][0]
    assert embed["color"] == notify.COLOR["info"]
    assert embed["fields"][0]["inline"] is True
    assert len(embed["fields"][1]["value"]) == 1000
    assert embed["url"] == "https://run.example"

    monkeypatch.setattr(notify.httpx, "post", lambda *_a, **_k: SimpleNamespace(status_code=500))
    assert notify.notify("info", title="x") is False
    monkeypatch.setattr(notify.httpx, "post", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("down")))
    assert notify.notify("info", title="x") is False

    monkeypatch.setattr(notify, "notify", lambda event, **kwargs: (event, kwargs))
    monkeypatch.setenv("STUDIO_BASE_URL", "https://studio.example/")
    started = notify.notify_run_started("123456789", None)
    assert started[0] == "started" and "Untitled" in started[1]["title"]
    done = notify.notify_run_done("123456789", {"run_id": "r", "clips": 4})
    assert done[1]["fields"] == {"run_id": "12345678", "clips": 4}
    regen = notify.notify_regen("run", "slot", "a" * 300, "b" * 300)
    assert len(regen[1]["fields"]["before"]) == 200
    assert notify._studio_run_url("r") == "https://studio.example/studio/runs/r"
    monkeypatch.delenv("STUDIO_BASE_URL")
    assert notify._studio_run_url("r") is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Hi-Ob REELS OECD 405", "히옵 릴스 오이시디 사백오"),
        ("CTA 문의", "지금 문의 문의"),
        ("CTA 등록", "지금 신청 등록"),
        ("10초 2개 21명 3개월 50%", "십초 두 개 이십일 명 삼개월 오십퍼센트"),
    ],
)
def test_pronunciation_normalization(text, expected):
    assert pronunciation.normalize_korean_pronunciation(text) == expected


def test_pronunciation_helpers_and_override_shapes():
    assert pronunciation.has_ascii_alpha("ABC")
    assert not pronunciation.has_ascii_alpha("")
    assert pronunciation.unknown_ascii_terms("Beta alpha Beta") == ["Beta", "alpha"]
    assert pronunciation._override_pairs(None) == []
    assert pronunciation._override_pairs("bad") == []
    assert pronunciation._override_pairs({"A": "에이", "": "x"}) == [("A", "에이")]
    pairs = pronunciation._override_pairs(
        [
            {"source": "Long term", "replacement": "긴말"},
            {"from": "B", "to": "비"},
            {"term": "한글", "pronunciation": "바꿈"},
            "ignored",
        ]
    )
    assert pairs[0] == ("Long term", "긴말")
    assert pronunciation._apply_overrides("A AB 한글", {"A": "에이", "한글": "바꿈"}) == "에이 AB 바꿈"
    assert pronunciation.normalize_korean_pronunciation("API", {"API": "에이피아이"}) == "에이피아이"

    assert pronunciation.sino_reading(0) == "영"
    assert pronunciation.sino_reading(100_010_001) == "억만일"
    assert pronunciation.native_reading(20) == "스무"
    assert pronunciation.native_reading(12) == "열두"
    assert pronunciation.native_reading(21) == "이십일"
    assert pronunciation.tts_numeral_reading("") == ""

    assert pronunciation._parse_hangul_number("한") == 1
    assert pronunciation._parse_hangul_number("스물") == 20
    assert pronunciation._parse_hangul_number("스물세") == 23
    assert pronunciation._parse_hangul_number("열") == 10
    assert pronunciation._parse_hangul_number("열두") == 12
    assert pronunciation._parse_hangul_number("십이") == 12
    assert pronunciation._parse_hangul_number("?") is None
    assert pronunciation._parse_hangul_number("") is None
    assert pronunciation.caption_numerals_to_digits("열초 천팔백개") == "10초 1800개"
    assert pronunciation.normalize_script_lines([" 10초 "]) == ["십초"]


def test_render_gate_all_terminal_shapes():
    client_ = QueueClient()
    client_.queue("slot", [])
    result = check_run_render_ready(client_, "empty")
    assert not result.ok and "slot 0개" in result.violations[0]

    client_.queue("slot", [{"track": "music", "beat_index": None, "current_artifact_id": "m"}])
    result = check_run_render_ready(client_, "no-beats")
    assert not result.ok and "beat_index" in result.violations[0]

    slots = [
        {"track": "visual", "beat_index": 0, "current_artifact_id": "v"},
        {"track": "caption", "beat_index": 0, "current_artifact_id": None},
    ]
    client_.queue("slot", slots)
    result = check_run_render_ready(
        client_,
        "warnings",
        require_voice_per_beat=False,
        require_caption_per_beat=True,
    )
    assert result.ok
    assert len(result.warnings) == 2

    client_.queue(
        "slot",
        [
            {"track": "voice", "beat_index": 0, "current_artifact_id": None},
            {"track": "caption", "beat_index": 0, "current_artifact_id": "c"},
            {"track": "music", "beat_index": None, "current_artifact_id": "m"},
        ],
    )
    result = check_run_render_ready(client_, "missing-media")
    assert not result.ok
    assert any("보이스" in item for item in result.violations)
    assert any("비주얼" in item for item in result.violations)

    client_.queue(
        "slot",
        [
            {"track": "voiceover", "beat_index": 0, "current_artifact_id": "a"},
            {"track": "visual", "beat_index": 0, "current_artifact_id": "v"},
            {"track": "caption", "beat_index": 0, "current_artifact_id": "c"},
            {"track": "music", "beat_index": None, "current_artifact_id": "m"},
        ],
    )
    result = check_run_render_ready(
        client_, "complete", require_caption_per_beat=True
    )
    assert result.ok and not result.warnings


def test_schema_validation_and_package_exports():
    model = validate_production_job_update(
        {"status": " succeeded ", "attributes": {"clips": 4}, "future": True}
    )
    assert model.status == "succeeded"
    assert not hasattr(model, "future")
    with pytest.raises(ValidationError):
        validate_production_job_update({})
    with pytest.raises(ValidationError):
        validate_production_job_update({"status": "ok", "attributes": []})
    assert hiob_platform.__version__ == "0.2.0"
    assert "get_service_client" in hiob_platform.__all__
