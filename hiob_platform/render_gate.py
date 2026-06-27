"""렌더前 readiness 게이트 (substrate — star·atropos 공용, 순환 방지).

렌더 dispatch 전에 호출 → 전 비트가 보이스(P1)·비주얼·자막을 갖췄는지 *DB 실데이터로*
증명. 미달이면 block → "음소거 슬라이드쇼" 라이브 봉쇄 + false-DONE 차단.

substrate 거주 이유: 오케(star)와 조립(atropos/composer_v2) 둘 다 렌더前 게이트를 호출한다.
star에 두면 atropos→star 순환이 생기므로 공용 기층(platform)이 단일 진실.
grounding: slot(run_id, track, beat_index, current_artifact_id) — beat_index는 slot에.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

VOICE_TRACKS = {"voiceover", "voice"}
VISUAL_TRACKS = {"visual"}
CAPTION_TRACK = "caption"
SFX_TRACK = "sfx"


@dataclass(frozen=True)
class RenderReadiness:
    ok: bool
    violations: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def _load_slots(client: Any, run_id: str) -> list[dict]:
    return (
        client.table("slot")
        .select("id, track, beat_index, current_artifact_id")
        .eq("run_id", run_id)
        .execute()
        .data
    ) or []


def check_run_render_ready(
    client: Any,
    run_id: str,
    *,
    require_voice_per_beat: bool = True,
    require_caption_per_beat: bool = False,
) -> RenderReadiness:
    """run의 slot을 읽어 비트별 커버리지를 증명. 미달=block(P1)."""
    slots = _load_slots(client, run_id)
    if not slots:
        return RenderReadiness(ok=False, violations=(f"run {run_id} slot 0개",))

    beats = {s["beat_index"] for s in slots if s.get("beat_index") is not None}
    if not beats:
        return RenderReadiness(ok=False, violations=("beat_index 있는 slot 0개",))

    def covered(tracks: set[str] | str) -> set[int]:
        ts = {tracks} if isinstance(tracks, str) else tracks
        return {s["beat_index"] for s in slots
                if s.get("track") in ts and s.get("current_artifact_id") and s.get("beat_index") is not None}

    voice_beats = covered(VOICE_TRACKS)
    media_beats = covered(VISUAL_TRACKS)
    caption_beats = covered(CAPTION_TRACK)
    has_music = any(s.get("track") == "music" and s.get("current_artifact_id") for s in slots)

    violations: list[str] = []
    warnings: list[str] = []

    if require_voice_per_beat:
        miss = sorted(beats - voice_beats)
        if miss:
            violations.append(f"P1 보이스 미결박 비트 {miss} (음소거 위험)")
    miss_media = sorted(beats - media_beats)
    if miss_media:
        violations.append(f"비주얼 미결박 비트 {miss_media}")
    if require_caption_per_beat:
        miss_cap = sorted(beats - caption_beats)
        if miss_cap:
            warnings.append(f"P13 자막 미결박 비트 {miss_cap} (dead air 위험)")
    if not has_music:
        warnings.append("음악 트랙 없음")

    return RenderReadiness(ok=not violations, violations=tuple(violations), warnings=tuple(warnings))
