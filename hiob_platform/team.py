"""Agent team + agent_call write helpers + timeline materialization.

The team layer is what the user sees in the UI: a PD, researcher, marketer,
scriptwriter, art director, sound designer, editor, and QA — each visible,
each with their input/output, each with a status.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from typing import Any

from supabase import Client

DEFAULT_TEAM_ROLES: tuple[str, ...] = (
    "pd",
    "researcher",
    "marketer",
    "scriptwriter",
    "art_director",
    "sound_designer",
    "editor",
    "qa",
)

_DATABASE_NOW = "now()"
_NOT_IS = "not.is"
_SELECT_ID_KIND = "id, kind"
_SELECT_ID_MARKERS = "id, markers"
_SELECT_ID_DURATION = "id, duration_ms"
_SELECT_START_DURATION = "start_ms, duration_ms"
_NO_TIMELINE = "no timeline"
_NO_VIDEO_TRACK = "no video track"


def create_team(client: Client, *, run_id: str, keyword: str) -> dict:
    res = (
        client.table("agent_team")
        .insert({"run_id": run_id, "keyword": keyword, "status": "assembling"})
        .execute()
    )
    return res.data[0]


def update_team(client: Client, team_id: str, **fields: Any) -> dict:
    res = client.table("agent_team").update(fields).eq("id", team_id).execute()
    return res.data[0] if res.data else {}


def create_call(
    client: Client,
    *,
    team_id: str,
    role_code: str,
    step_index: int,
    parent_call_id: str | None = None,
    input: dict | None = None,
    model: str | None = None,
) -> dict:
    res = (
        client.table("agent_call")
        .insert(
            {
                "team_id": team_id,
                "role_code": role_code,
                "step_index": step_index,
                "parent_call_id": parent_call_id,
                "status": "queued",
                "input": input or {},
                "model": model,
            }
        )
        .execute()
    )
    return res.data[0]


def start_call(client: Client, call_id: str) -> dict:
    res = (
        client.table("agent_call")
        .update({"status": "running", "started_at": _DATABASE_NOW})
        .eq("id", call_id)
        .execute()
    )
    return res.data[0] if res.data else {}


def finish_call(
    client: Client,
    call_id: str,
    *,
    output: dict | None = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cost_usd: float = 0.0,
    error: dict | None = None,
) -> dict:
    payload: dict[str, Any] = {
        "status": "error" if error else "ok",
        "ended_at": _DATABASE_NOW,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": cost_usd,
    }
    if output is not None:
        payload["output"] = output
    if error is not None:
        payload["error"] = error
    res = client.table("agent_call").update(payload).eq("id", call_id).execute()
    return res.data[0] if res.data else {}


def create_meeting_note(
    client: Client,
    *,
    team_id: str,
    run_id: str,
    step_index: int,
    meeting_type: str,
    speaker_role: str | None,
    audience_roles: list[str] | None = None,
    source_call_id: str | None = None,
    summary: str,
    decisions: list[dict] | list[str] | None = None,
    open_questions: list[dict] | list[str] | None = None,
    next_actions: list[dict] | list[str] | None = None,
    refs: list[dict] | None = None,
) -> dict:
    payload = {
        "team_id": team_id,
        "run_id": run_id,
        "step_index": step_index,
        "meeting_type": meeting_type,
        "speaker_role": speaker_role,
        "audience_roles": audience_roles or [],
        "source_call_id": source_call_id,
        "summary": summary,
        "decisions": decisions or [],
        "open_questions": open_questions or [],
        "next_actions": next_actions or [],
        "refs": refs or [],
    }
    try:
        res = client.table("agent_meeting").insert(payload).execute()
    except Exception:
        # Older deployments without 0007 do not yet have the `refs` column;
        # retry without it so the meeting still lands.
        payload.pop("refs", None)
        res = client.table("agent_meeting").insert(payload).execute()
    return res.data[0] if res.data else {}


def load_role(client: Client, role_code: str) -> dict:
    res = client.table("agent_role").select("*").eq("code", role_code).single().execute()
    return res.data


# ----------------------------------------------------------------
# Timeline materialization — turns agent outputs into editable clips
# ----------------------------------------------------------------

def ensure_timeline(
    client: Client,
    *,
    run_id: str,
    duration_ms: int,
    aspect: str = "9:16",
) -> dict:
    existing = (
        client.table("timeline").select("*").eq("run_id", run_id).limit(1).execute()
    )
    if existing.data:
        return existing.data[0]
    width, height = (1080, 1920) if aspect == "9:16" else (1920, 1080) if aspect == "16:9" else (1080, 1080)
    res = (
        client.table("timeline")
        .insert(
            {
                "run_id": run_id,
                "fps": 30,
                "width": width,
                "height": height,
                "duration_ms": duration_ms,
                "aspect": aspect,
            }
        )
        .execute()
    )
    return res.data[0]


def ensure_track(
    client: Client,
    *,
    timeline_id: str,
    kind: str,
    label: str,
    ord: int,
    z_index: int = 0,
) -> dict:
    existing = (
        client.table("timeline_track")
        .select("*")
        .eq("timeline_id", timeline_id)
        .eq("kind", kind)
        .eq("ord", ord)
        .limit(1)
        .execute()
    )
    if existing.data:
        return existing.data[0]
    res = (
        client.table("timeline_track")
        .insert(
            {
                "timeline_id": timeline_id,
                "kind": kind,
                "label": label,
                "ord": ord,
                "z_index": z_index,
            }
        )
        .execute()
    )
    return res.data[0]


def create_clip(
    client: Client,
    *,
    track_id: str,
    artifact_id: str | None,
    start_ms: int,
    duration_ms: int,
    beat_index: int | None = None,
    in_ms: int = 0,
    text_content: str | None = None,
    origin_call_id: str | None = None,
    transforms: dict | None = None,
    z_index_override: int | None = None,
    effects: list | None = None,
    attributes: dict | None = None,
) -> dict:
    payload: dict[str, Any] = {
        "track_id": track_id,
        "artifact_id": artifact_id,
        "start_ms": start_ms,
        "duration_ms": duration_ms,
        "beat_index": beat_index,
        "in_ms": in_ms,
        "text_content": text_content,
        "origin_call_id": origin_call_id,
    }
    if transforms is not None:
        payload["transforms"] = transforms
    if effects is not None:
        payload["effects"] = effects
    if attributes is not None:
        payload["attributes"] = attributes
    try:
        res = client.table("clip").insert(payload).execute()
    except Exception:
        if beat_index is None:
            raise
        try:
            existing = (
                client.table("clip")
                .select("id")
                .eq("track_id", track_id)
                .eq("beat_index", beat_index)
                .limit(1)
                .execute()
                .data
                or []
            )
            if existing:
                update_payload = dict(payload)
                if artifact_id is None:
                    update_payload.pop("artifact_id", None)
                res = client.table("clip").update(update_payload).eq("id", existing[0]["id"]).execute()
            else:
                legacy = {k: v for k, v in payload.items() if k != "beat_index"}
                res = client.table("clip").insert(legacy).execute()
        except Exception:
            legacy = {k: v for k, v in payload.items() if k != "beat_index"}
            res = client.table("clip").insert(legacy).execute()
    return res.data[0]


def update_timeline_markers(client: Client, timeline_id: str, markers: list[dict]) -> dict:
    res = (
        client.table("timeline")
        .update({"markers": markers, "updated_at": _DATABASE_NOW})
        .eq("id", timeline_id)
        .execute()
    )
    return res.data[0] if res.data else {}


# ----------------------------------------------------------------
# Slot → Clip artifact backfill
# ----------------------------------------------------------------

# Workers upload artifacts to *slots*; the timeline clips created by
# _materialize_timeline() start with artifact_id=None because clips are
# materialized before media workers run.  This mapping bridges the two.
_SLOT_TRACK_TO_CLIP_KIND: dict[str, str] = {
    "visual": "video",
    "voiceover": "audio",
    "caption": "caption",
    "music": "music",
    "sfx": "sfx",
}

# Must match the BEAT_MS used in team_orchestrator.py and sfx.py.
_BEAT_MS: int = 1000


def _slot_coverage_inventory(
    slot_rows: list[dict],
) -> tuple[dict[int, int], dict[int, int], list[str]]:
    voice_by_beat: dict[int, int] = defaultdict(int)
    sfx_by_beat: dict[int, int] = defaultdict(int)
    warnings: list[str] = []
    for slot in slot_rows:
        bi = slot.get("beat_index")
        kind = _SLOT_TRACK_TO_CLIP_KIND.get(slot["track"])
        if kind == "audio":
            if bi is not None:
                voice_by_beat[int(bi)] += 1
            else:
                warnings.append(
                    f"voice sub-shot at {slot.get('start_ms')}ms (beat_index=NULL, will use start_ms fallback)"
                )
        elif kind == "sfx":
            if bi is not None:
                sfx_by_beat[int(bi)] += 1
            else:
                warnings.append(
                    f"SFX sub-shot at {slot.get('start_ms')}ms (beat_index=NULL, will use start_ms fallback)"
                )
    return voice_by_beat, sfx_by_beat, warnings


def _evaluate_slot_coverage(slot_rows: list[dict]) -> dict[str, Any]:
    voice_by_beat, sfx_by_beat, warnings = _slot_coverage_inventory(slot_rows)
    all_beats = set(voice_by_beat.keys()) | set(sfx_by_beat.keys())
    if not all_beats:
        return {
            "ok": False,
            "violations": ["no beat-indexed voice or SFX slots found"],
            "warnings": warnings,
        }
    max_beat = max(all_beats)
    violations: list[str] = []
    for beat_idx in range(max_beat + 1):
        voice_count = voice_by_beat.get(beat_idx, 0)
        sfx_count = sfx_by_beat.get(beat_idx, 0)
        if voice_count == 0 and sfx_count == 0:
            violations.append(
                f"beat_{beat_idx}: no voice or SFX slots (voice={voice_count}, sfx={sfx_count})"
            )
        elif voice_count == 0 and sfx_count > 0:
            violations.append(
                f"beat_{beat_idx}: SFX-only (voice_slots=0, silent risk if SFX incomplete)"
            )
    return {
        "ok": not violations,
        "violations": violations,
        "warnings": warnings,
    }


def verify_slots_coverage(client: Client, run_id: str) -> dict[str, Any]:
    """Verify that every beat has artifact-backed voice coverage."""
    slot_rows: list[dict] = (
        client.table("slot")
        .select("id, track, beat_index, start_ms, current_artifact_id")
        .eq("run_id", run_id)
        .filter("current_artifact_id", _NOT_IS, "null")
        .execute()
        .data
    ) or []
    if not slot_rows:
        return {
            "ok": False,
            "violations": ["no slots with artifacts found for this run"],
            "warnings": [],
        }
    return _evaluate_slot_coverage(slot_rows)


def _artifact_duration_by_id(client: Client, slot_rows: list[dict]) -> dict[str, dict]:
    artifact_ids = [
        slot["current_artifact_id"]
        for slot in slot_rows
        if slot.get("current_artifact_id")
    ]
    if not artifact_ids:
        return {}
    rows = (
        client.table("artifact")
        .select(_SELECT_ID_DURATION)
        .in_("id", artifact_ids)
        .execute()
        .data
        or []
    )
    return {row["id"]: row for row in rows}


def _index_unbound_clips(
    clips: list[dict], tracks: list[dict]
) -> tuple[
    dict[tuple[int, str], list[dict]],
    dict[tuple[int, str], list[dict]],
]:
    kind_by_track = {track["id"]: track["kind"] for track in tracks}
    by_beat: dict[tuple[int, str], list[dict]] = defaultdict(list)
    by_start: dict[tuple[int, str], list[dict]] = defaultdict(list)
    for clip in clips:
        kind = kind_by_track.get(clip["track_id"])
        if not kind:
            continue
        beat = clip.get("beat_index")
        if beat is not None:
            by_beat[(int(beat), kind)].append(clip)
            continue
        key = (int(clip.get("start_ms") or 0), kind)
        if not by_start[key]:
            by_start[key].append(clip)
    return by_beat, by_start


def _bind_slot_artifacts(
    client: Client,
    slot_rows: list[dict],
    artifact_by_id: dict[str, dict],
    clips_by_beat: dict[tuple[int, str], list[dict]],
    clips_by_start: dict[tuple[int, str], list[dict]],
) -> int:
    updated = 0
    for slot in slot_rows:
        clip_kind = _SLOT_TRACK_TO_CLIP_KIND.get(slot["track"])
        if not clip_kind:
            continue
        beat = slot.get("beat_index")
        if beat is None:
            key = (int(slot.get("start_ms") or 0), clip_kind)
            target_clips = clips_by_start.pop(key, [])
        else:
            target_clips = clips_by_beat.pop((int(beat), clip_kind), [])
        for target in target_clips:
            patch: dict[str, Any] = {
                "artifact_id": slot["current_artifact_id"],
                "updated_at": _DATABASE_NOW,
            }
            artifact = artifact_by_id.get(slot["current_artifact_id"]) or {}
            if clip_kind == "audio" and artifact.get("duration_ms"):
                patch["duration_ms"] = max(100, int(artifact["duration_ms"]))
            client.table("clip").update(patch).eq("id", target["id"]).execute()
            updated += 1
    return updated


def sync_clips_from_slots(client: Client, run_id: str) -> dict:
    """Bind every artifact-backed slot to all matching unbound clips."""
    slot_rows: list[dict] = (
        client.table("slot")
        .select("id, track, beat_index, start_ms, current_artifact_id")
        .eq("run_id", run_id)
        .filter("current_artifact_id", _NOT_IS, "null")
        .execute()
        .data
    ) or []

    if not slot_rows:
        return {"updated": 0, "run_id": run_id}

    # 2. Timeline for this run; extract per-beat durations from markers.
    tl = (
        client.table("timeline")
        .select(_SELECT_ID_MARKERS)
        .eq("run_id", run_id)
        .limit(1)
        .execute()
        .data
    )
    if not tl:
        return {"updated": 0, "run_id": run_id}
    timeline_id = tl[0]["id"]
    tracks: list[dict] = (
        client.table("timeline_track")
        .select("id, kind, ord")
        .eq("timeline_id", timeline_id)
        .order("ord")
        .execute()
        .data
    ) or []
    all_track_ids = [track["id"] for track in tracks]
    if not all_track_ids:
        return {"updated": 0, "run_id": run_id}
    artifact_by_id = _artifact_duration_by_id(client, slot_rows)
    null_clips: list[dict] = (
        client.table("clip")
        .select("id, track_id, start_ms, beat_index, duration_ms")
        .in_("track_id", all_track_ids)
        .is_("artifact_id", "null")
        .execute()
        .data
    ) or []

    if not null_clips:
        return {"updated": 0, "run_id": run_id}
    by_beat, by_start = _index_unbound_clips(null_clips, tracks)
    updated = _bind_slot_artifacts(
        client, slot_rows, artifact_by_id, by_beat, by_start
    )
    print(f"[SYNC-CLIPS] run={run_id} updated={updated} (all artifacts, no loss)")
    return {"updated": updated, "run_id": run_id}


# ----------------------------------------------------------------
# Audio-aligned timeline repack
# ----------------------------------------------------------------

# Per-beat duration when repacking to voiceover length. The double-start clip
# model (PHASE5 B): a voice clip's timeline duration must be >= its REAL audio
# length — NEVER truncated. The old 5.5s cap cut long Korean lines mid-sentence
# (the renderer plays each clip only for clip.durationMs, so a cap = a cut).
# We drop the cap entirely: duration = measured audio length + a small breath.
# Voiceover MP3s are silence-trimmed to the spoken words (see
# voiceover._trim_silence_to_words) and their FRONT is never head-trimmed
# (clip.in_ms stays 0), so the line always plays in full.
_REPACK_MIN_MS = 100
_BEAT_PAD_MS = 30

# Voice OVERLAP: each beat's voice starts this many ms BEFORE the previous beat's
# voice ends, so consecutive speakers bleed into each other (theatre interjection,
# "받아치는" 느낌) instead of waiting their turn. Only applied between two beats that
# both carry real voice. The on-screen visuals + captions stay contiguous (they
# cut exactly at the next beat) — only the AUDIO overlaps. Env-tunable for fast
# eyeball iteration without a redeploy.
_VOICE_OVERLAP_MS = max(0, int(os.environ.get("VOICE_OVERLAP_MS", "250")))

# NAPKIN "THE SCENE": every scene image must hold UNINTERRUPTED >= 0.8s (no sub-second
# flicker). This floors the ON-SCREEN image/caption window; audio still plays in full and
# may overflow the image cut (audio lanes are independent of image cuts).
# D-43 (founder 2026-07-02): 800ms 바닥이 0.8초 컷 지옥의 공범 — 문장 단위(2~3.5s) 리듬을
# 위해 기본 1800ms. 구 동작은 env MIN_SCENE_MS=800.
_MIN_SCENE_MS = max(0, int(os.environ.get("MIN_SCENE_MS", "1800")))


# EDIT-WIRE-3: extra silence per scene type so comedy/product beats breathe.
_SCENE_BREATH_MS: dict[str, int] = {
    "hook": 350,     # comedy punchline — let it land
    "product": 250,  # product reveal — impact pause
}
_SCENE_BREATH_DEFAULT_MS: int = 50


def _scene_breath_ms(scene_type: str | None, voiced: bool = True) -> int:
    """Extra silence after a voiced beat (EDIT-WIRE-3). 0 for silent beats."""
    if not voiced:
        return 0
    return _SCENE_BREATH_MS.get(str(scene_type or "").lower(), _SCENE_BREATH_DEFAULT_MS)


def _beat_duration_ms(audio_ms: int) -> int:
    """Timeline duration for one voice beat: the clip plays its FULL audio
    length plus a small inter-beat breath, floored at _REPACK_MIN_MS. There is
    no upper cap — the voice line is never truncated (double-start clip model,
    PHASE5 B). Beats with no audio yet keep the floor.
    """
    if not audio_ms or audio_ms <= 0:
        return _REPACK_MIN_MS
    return max(_REPACK_MIN_MS, int(audio_ms) + _BEAT_PAD_MS)


def _repack_sample(client: Client, timeline_id: str) -> list[dict]:
    rows = (
        client.table("timeline_track")
        .select(_SELECT_ID_KIND)
        .eq("timeline_id", timeline_id)
        .in_("kind", ["video", "audio", "caption"])
        .execute()
        .data
        or []
    )
    return (
        client.table("clip")
        .select("start_ms, beat_index, track_id")
        .in_("track_id", [row["id"] for row in rows])
        .limit(500)
        .execute()
        .data
        or []
    )


def _clips_are_packed(sample: list[dict]) -> bool:
    return bool(
        sample
        and any(
            row.get("beat_index") is not None
            and int(row.get("beat_index") or 0) >= 1
            and int(row.get("start_ms") or 0)
            != int(row.get("beat_index") or 0) * _BEAT_MS
            for row in sample
        )
    )


def _partition_repack_tracks(
    tracks: list[dict],
) -> tuple[dict[str, list[str]], dict[str, str]]:
    aligned = {"audio": [], "video": [], "caption": []}
    singleton: dict[str, str] = {}
    for track in tracks:
        kind = track["kind"]
        if kind in aligned:
            aligned[kind].append(track["id"])
        else:
            singleton[kind] = track["id"]
    return aligned, singleton


def _slots_are_packed(slots: list[dict]) -> bool:
    return any(
        int(slot.get("start_ms") or 0)
        != int(slot.get("beat_index") or 0) * _BEAT_MS
        for slot in slots
        if slot.get("beat_index") is not None
    )


def _load_repack_durations(client: Client, slots: list[dict]) -> dict[str, int]:
    artifact_ids = [
        slot["current_artifact_id"]
        for slot in slots
        if slot["current_artifact_id"]
    ]
    if not artifact_ids:
        return {}
    rows = (
        client.table("artifact")
        .select(_SELECT_ID_DURATION)
        .in_("id", artifact_ids)
        .execute()
        .data
        or []
    )
    return {row["id"]: (row.get("duration_ms") or 0) for row in rows}


def _load_scene_types(client: Client, audio_track_ids: list[str]) -> dict[int, str]:
    if not audio_track_ids:
        return {}
    rows = (
        client.table("clip")
        .select("beat_index, attributes")
        .in_("track_id", audio_track_ids)
        .not_.is_("beat_index", "null")
        .execute()
        .data
        or []
    )
    return {
        int(row["beat_index"]): str(
            (row.get("attributes") or {}).get("scene_type") or ""
        ).lower()
        for row in rows
        if row.get("beat_index") is not None
    }


def _build_repack_plan(
    slots: list[dict],
    durations: dict[str, int],
    scene_types: dict[int, str],
) -> tuple[list[dict[str, int]], int]:
    cursor_ms = 0
    plan: list[dict[str, int]] = []
    previous_voiced = False
    for slot in slots:
        beat = slot.get("beat_index")
        if beat is None:
            continue
        artifact_id = slot.get("current_artifact_id")
        audio_ms = durations.get(artifact_id, 0) if artifact_id else 0
        voiced = audio_ms > 0
        duration_ms = _beat_duration_ms(audio_ms) + _scene_breath_ms(
            scene_types.get(int(beat), ""), voiced
        )
        overlap = _VOICE_OVERLAP_MS if plan and previous_voiced and voiced else 0
        start_ms = max(0, cursor_ms - overlap)
        plan.append(
            {
                "beat": int(beat),
                "old_start_ms": int(slot.get("start_ms") or beat * _BEAT_MS),
                "new_start_ms": start_ms,
                "new_duration_ms": duration_ms,
            }
        )
        cursor_ms = max(
            start_ms + _MIN_SCENE_MS + _VOICE_OVERLAP_MS,
            start_ms + duration_ms,
        )
        previous_voiced = voiced
    return plan, cursor_ms


def _add_visual_windows(plan: list[dict[str, int]]) -> None:
    for index, entry in enumerate(plan):
        if index + 1 < len(plan):
            next_start = plan[index + 1]["new_start_ms"]
        else:
            next_start = entry["new_start_ms"] + entry["new_duration_ms"]
        entry["visual_duration_ms"] = max(
            _MIN_SCENE_MS, next_start - entry["new_start_ms"]
        )


def _latest_content_end(
    client: Client, track_ids: list[str], fallback: int
) -> int:
    ends: list[int] = []
    for track_id in track_ids:
        rows = (
            client.table("clip")
            .select(_SELECT_START_DURATION)
            .eq("track_id", track_id)
            .execute()
            .data
            or []
        )
        ends.extend(
            int(row.get("start_ms") or 0) + int(row.get("duration_ms") or 0)
            for row in rows
        )
    latest = max(ends, default=0)
    return latest if latest > 0 else fallback


def _stretch_music(client: Client, music_track_id: str | None, duration_ms: int) -> None:
    if music_track_id:
        client.table("clip").update(
            {"start_ms": 0, "duration_ms": duration_ms}
        ).eq("track_id", music_track_id).execute()


def _resync_packed_timeline(
    client: Client,
    timeline_id: str,
    plan: list[dict[str, int]],
    cursor_ms: int,
    aligned_tracks: dict[str, list[str]],
    music_track_id: str | None,
) -> dict:
    track_ids = (
        aligned_tracks["audio"]
        + aligned_tracks["video"]
        + aligned_tracks["caption"]
    )
    cursor_ms = _latest_content_end(client, track_ids, cursor_ms)
    _stretch_music(client, music_track_id, cursor_ms)
    client.table("timeline").update({"duration_ms": cursor_ms}).eq(
        "id", timeline_id
    ).execute()
    return {
        "updated_clips": 0,
        "new_duration_ms": cursor_ms,
        "beats": len(plan),
        "skipped": "clips already packed; timeline duration re-synced",
    }


def _update_one_repack_clip(
    client: Client,
    track_id: str,
    entry: dict[str, int],
    duration_ms: int,
) -> int:
    patch = {"start_ms": entry["new_start_ms"], "duration_ms": duration_ms}
    try:
        result = (
            client.table("clip")
            .update(patch)
            .eq("track_id", track_id)
            .eq("beat_index", entry["beat"])
            .execute()
        )
    except Exception:
        result = (
            client.table("clip")
            .update(patch)
            .eq("track_id", track_id)
            .eq("start_ms", entry["old_start_ms"])
            .execute()
        )
    if not (result.data or []):
        result = (
            client.table("clip")
            .update(patch)
            .eq("track_id", track_id)
            .eq("start_ms", entry["old_start_ms"])
            .execute()
        )
    return len(result.data or [])


def _update_repack_clips(
    client: Client,
    aligned_tracks: dict[str, list[str]],
    plan: list[dict[str, int]],
) -> int:
    updated = 0
    for kind in ("video", "audio", "caption"):
        for track_id in aligned_tracks[kind]:
            for entry in plan:
                duration_ms = (
                    entry["new_duration_ms"]
                    if kind == "audio"
                    else entry["visual_duration_ms"]
                )
                updated += _update_one_repack_clip(
                    client, track_id, entry, duration_ms
                )
    return updated


def _cursor_with_subshots(
    client: Client, video_track_ids: list[str], cursor_ms: int
) -> int:
    for track_id in video_track_ids:
        rows = (
            client.table("clip")
            .select(_SELECT_START_DURATION)
            .eq("track_id", track_id)
            .is_("beat_index", "null")
            .execute()
            .data
            or []
        )
        for row in rows:
            cursor_ms = max(
                cursor_ms,
                int(row.get("start_ms") or 0)
                + int(row.get("duration_ms") or 0),
            )
    return cursor_ms


def _update_repack_slots(
    client: Client, run_id: str, plan: list[dict[str, int]]
) -> None:
    for kind in ("visual", "voiceover", "caption"):
        for entry in plan:
            duration_ms = (
                entry["new_duration_ms"]
                if kind == "voiceover"
                else entry["visual_duration_ms"]
            )
            client.table("slot").update(
                {
                    "start_ms": entry["new_start_ms"],
                    "end_ms": entry["new_start_ms"] + duration_ms,
                }
            ).eq("run_id", run_id).eq("track", kind).eq(
                "beat_index", entry["beat"]
            ).execute()


def _persist_repack_timeline(
    client: Client,
    timeline_id: str,
    cursor_ms: int,
    plan: list[dict[str, int]],
) -> None:
    markers = [
        {
            "id": f"beat-{entry['beat']}",
            "timeMs": entry["new_start_ms"],
            "beatIndex": entry["beat"],
            "label": f"beat {entry['beat']}",
        }
        for entry in plan
    ]
    client.table("timeline").update({"duration_ms": cursor_ms}).eq(
        "id", timeline_id
    ).execute()
    try:
        client.table("timeline").update({"markers": markers}).eq(
            "id", timeline_id
        ).execute()
    except Exception:
        pass


def repack_timeline_to_audio(client: Client, run_id: str) -> dict:
    """Repack all editor lanes onto measured voice timing without truncation."""
    timeline_rows = (
        client.table("timeline")
        .select(_SELECT_ID_DURATION)
        .eq("run_id", run_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    if not timeline_rows:
        return {"updated_clips": 0, "skipped": _NO_TIMELINE}
    timeline_id = timeline_rows[0]["id"]
    clips_already_packed = _clips_are_packed(_repack_sample(client, timeline_id))
    tracks = (
        client.table("timeline_track")
        .select(_SELECT_ID_KIND)
        .eq("timeline_id", timeline_id)
        .execute()
        .data
        or []
    )
    aligned_tracks, singleton_tracks = _partition_repack_tracks(tracks)
    voice_slots = (
        client.table("slot")
        .select("beat_index, current_artifact_id, start_ms")
        .eq("run_id", run_id)
        .eq("track", "voiceover")
        .order("beat_index")
        .execute()
        .data
        or []
    )
    if not voice_slots:
        return {"updated_clips": 0, "skipped": "no voiceover slots"}
    slots_already_packed = _slots_are_packed(voice_slots)
    durations = _load_repack_durations(client, voice_slots)
    scene_types = _load_scene_types(client, aligned_tracks["audio"])
    plan, cursor_ms = _build_repack_plan(voice_slots, durations, scene_types)
    if not plan:
        return {"updated_clips": 0, "skipped": "no beats"}
    _add_visual_windows(plan)
    music_track_id = singleton_tracks.get("music")
    if clips_already_packed and slots_already_packed:
        return _resync_packed_timeline(
            client,
            timeline_id,
            plan,
            cursor_ms,
            aligned_tracks,
            music_track_id,
        )
    updated_clips = _update_repack_clips(client, aligned_tracks, plan)
    cursor_ms = _cursor_with_subshots(
        client, aligned_tracks["video"], cursor_ms
    )
    _update_repack_slots(client, run_id, plan)
    _stretch_music(client, music_track_id, cursor_ms)
    _persist_repack_timeline(client, timeline_id, cursor_ms, plan)
    return {
        "updated_clips": updated_clips,
        "new_duration_ms": cursor_ms,
        "beats": len(plan),
    }


_SUBBEAT_MAX_MS = 3500  # mirror of athena SUBBEAT_MAX_MS (founder 3초/장 규범 2026-07-08)
_SUBBEAT_MAX_COUNT = 2  # cap sub-beat count to limit API spend


def _beat_positions_from_markers(markers: list[dict]) -> dict[int, dict[str, int]]:
    valid = [
        marker
        for marker in markers
        if marker.get("beatIndex") is not None
        and marker.get("durationMs") is not None
    ]
    sorted_markers = sorted(valid, key=lambda marker: marker.get("beatIndex") or 0)
    positions: dict[int, dict[str, int]] = {}
    for index, marker in enumerate(sorted_markers):
        start_ms = int(marker.get("timeMs") or 0)
        if index + 1 < len(sorted_markers):
            next_start_ms = int(sorted_markers[index + 1].get("timeMs") or 0)
        else:
            next_start_ms = start_ms + int(marker["durationMs"])
        positions[int(marker["beatIndex"])] = {
            "start_ms": start_ms,
            "visual_duration_ms": max(50, next_start_ms - start_ms),
        }
    return positions


def _artifact_storage_by_id(client: Client, clips: list[dict]) -> dict[str, str]:
    artifact_ids = [clip["artifact_id"] for clip in clips if clip.get("artifact_id")]
    if not artifact_ids:
        return {}
    rows = (
        client.table("artifact")
        .select("id, storage_key")
        .in_("id", artifact_ids)
        .execute()
        .data
        or []
    )
    return {row["id"]: (row.get("storage_key") or "") for row in rows}


def _video_position_patch(
    clip: dict,
    positions: dict[int, dict[str, int]],
    storage_by_id: dict[str, str],
) -> dict[str, Any] | None:
    beat = clip.get("beat_index")
    if beat is None:
        return None
    position = positions.get(int(beat))
    if not position:
        return None
    new_start = position["start_ms"]
    new_duration = position["visual_duration_ms"]
    patch: dict[str, Any] = {"updated_at": _DATABASE_NOW}
    if (
        int(clip.get("start_ms") or 0) != new_start
        or int(clip.get("duration_ms") or 0) != new_duration
    ):
        patch.update({"start_ms": new_start, "duration_ms": new_duration})
    attributes = dict(clip.get("attributes") or {})
    sub_images = attributes.get("sub_images") or []
    needed = min(
        _SUBBEAT_MAX_COUNT,
        max(1, (new_duration + _SUBBEAT_MAX_MS - 1) // _SUBBEAT_MAX_MS),
    )
    should_extend = (
        new_duration > _SUBBEAT_MAX_MS
        and len(sub_images) < needed
        and bool(clip.get("artifact_id"))
    )
    if should_extend:
        storage_key = storage_by_id.get(clip["artifact_id"] or "")
        if storage_key:
            attributes["sub_images"] = (
                list(sub_images) + [storage_key] * (needed - len(sub_images))
            )[:needed]
            patch["attributes"] = attributes
    return patch if len(patch) > 1 else None


def sync_video_positions_from_markers(client: Client, run_id: str) -> dict:
    """Align video clips to markers and backfill long-beat sub-images."""
    tl = client.table("timeline").select(_SELECT_ID_MARKERS).eq("run_id", run_id).limit(1).execute().data
    if not tl:
        return {"synced": 0, "skipped": "no_timeline"}
    timeline = tl[0]
    markers = list(timeline.get("markers") or [])
    if not markers:
        return {"synced": 0, "skipped": "no_markers"}
    positions = _beat_positions_from_markers(markers)
    if not positions:
        return {"synced": 0, "skipped": "no_valid_markers"}
    video_tracks = (
        client.table("timeline_track")
        .select("id")
        .eq("timeline_id", timeline["id"])
        .eq("kind", "video")
        .execute()
        .data or []
    )
    if not video_tracks:
        return {"synced": 0, "skipped": "no_video_tracks"}
    track_ids = [t["id"] for t in video_tracks]
    video_clips = (
        client.table("clip")
        .select("id, beat_index, start_ms, duration_ms, artifact_id, attributes")
        .in_("track_id", track_ids)
        .not_.is_("beat_index", "null")
        .execute()
        .data or []
    )
    storage_by_id = _artifact_storage_by_id(client, video_clips)
    synced = 0
    for clip in video_clips:
        patch = _video_position_patch(clip, positions, storage_by_id)
        if patch:
            client.table("clip").update(patch).eq("id", clip["id"]).execute()
            synced += 1
    return {"synced": synced, "total": len(video_clips)}


def _append_music_loops(client: Client, last: dict, video_end: int) -> tuple[int, int]:
    music_end = int(last["start_ms"] or 0) + int(last["duration_ms"] or 0)
    unit_ms = max(1000, int(last["duration_ms"] or 0))
    looped = 0
    while video_end - music_end > 500 and looped < 8:
        duration_ms = min(unit_ms, video_end - music_end)
        client.table("clip").insert(
            {
                "track_id": last["track_id"],
                "artifact_id": last["artifact_id"],
                "start_ms": music_end,
                "duration_ms": duration_ms,
                "in_ms": 0,
                "beat_index": None,
                "transforms": last.get("transforms")
                or {"x": 0, "y": 0, "scale": 1, "rotation": 0, "opacity": 1},
                "effects": last.get("effects") or [],
                "attributes": {"looped": True, "loop_of": last["id"]},
            }
        ).execute()
        music_end += duration_ms
        looped += 1
    return looped, music_end


def extend_music_to_cover(client: Client, run_id: str) -> dict:
    """Loop the final music asset until it covers the video lane."""
    tl = client.table("timeline").select("id").eq("run_id", run_id).limit(1).execute().data
    if not tl:
        return {"looped": 0, "skipped": "no_timeline"}
    timeline_id = tl[0]["id"]
    tracks = (client.table("timeline_track").select(_SELECT_ID_KIND)
              .eq("timeline_id", timeline_id).execute().data or [])
    video_ids = [t["id"] for t in tracks if t["kind"] == "video"]
    music_ids = [t["id"] for t in tracks if t["kind"] == "music"]
    if not video_ids or not music_ids:
        return {"looped": 0, "skipped": "no_tracks"}
    v_clips = (client.table("clip").select(_SELECT_START_DURATION)
               .in_("track_id", video_ids).execute().data or [])
    video_end = max((int(c["start_ms"] or 0) + int(c["duration_ms"] or 0) for c in v_clips), default=0)
    m_clips = (client.table("clip").select("id, track_id, artifact_id, start_ms, duration_ms, transforms, effects")
               .in_("track_id", music_ids).order("start_ms").execute().data or [])
    if not m_clips or not video_end:
        return {"looped": 0, "skipped": "no_music_or_video"}
    looped, music_end = _append_music_loops(client, m_clips[-1], video_end)
    return {"looped": looped, "video_end": video_end, "music_end": music_end}


def _audio_position_patch(
    clip: dict, positions: dict[int, dict[str, int]]
) -> dict[str, Any] | None:
    beat = int(clip["beat_index"])
    position = positions.get(beat)
    if not position:
        return None
    start_ms = position["start_ms"]
    duration_ms = int(clip.get("duration_ms") or 0)
    window_ms = position["visual_duration_ms"]
    new_duration = min(duration_ms, window_ms) if duration_ms > 0 else window_ms
    if int(clip.get("start_ms") or 0) == start_ms and duration_ms == new_duration:
        return None
    return {
        "start_ms": start_ms,
        "duration_ms": new_duration,
        "updated_at": _DATABASE_NOW,
    }


def sync_audio_positions_from_markers(client: Client, run_id: str) -> dict:
    """Align voice and SFX clips to the same marker clock as video."""
    tl = client.table("timeline").select(_SELECT_ID_MARKERS).eq("run_id", run_id).limit(1).execute().data
    if not tl:
        return {"synced": 0, "skipped": "no_timeline"}
    timeline = tl[0]
    positions = _beat_positions_from_markers(list(timeline.get("markers") or []))
    if not positions:
        return {"synced": 0, "skipped": "no_markers"}
    tracks = (client.table("timeline_track").select(_SELECT_ID_KIND)
              .eq("timeline_id", timeline["id"]).in_("kind", ["audio", "sfx"]).execute().data or [])
    if not tracks:
        return {"synced": 0, "skipped": "no_audio_tracks"}
    synced = 0
    for track in tracks:
        clips = (client.table("clip").select("id, beat_index, start_ms, duration_ms")
                 .eq("track_id", track["id"]).not_.is_("beat_index", "null").execute().data or [])
        for clip in clips:
            patch = _audio_position_patch(clip, positions)
            if patch:
                client.table("clip").update(patch).eq("id", clip["id"]).execute()
                synced += 1
    return {"synced": synced}


def _visual_position_patch(
    clip: dict, positions: dict[int, dict[str, int]]
) -> dict[str, Any] | None:
    beat = clip.get("beat_index")
    if beat is None:
        return None
    position = positions.get(int(beat))
    if not position:
        return None
    start_ms = position["start_ms"]
    duration_ms = position["visual_duration_ms"]
    if (
        int(clip.get("start_ms") or 0) == start_ms
        and int(clip.get("duration_ms") or 0) == duration_ms
    ):
        return None
    return {
        "start_ms": start_ms,
        "duration_ms": duration_ms,
        "updated_at": _DATABASE_NOW,
    }


def sync_caption_positions_from_markers(client: Client, run_id: str) -> dict:
    """Align caption clips to variable beat markers."""
    tl = client.table("timeline").select(_SELECT_ID_MARKERS).eq("run_id", run_id).limit(1).execute().data
    if not tl:
        return {"synced": 0, "skipped": "no_timeline"}
    timeline = tl[0]
    markers = list(timeline.get("markers") or [])
    if not markers:
        return {"synced": 0, "skipped": "no_markers"}
    positions = _beat_positions_from_markers(markers)
    if not positions:
        return {"synced": 0, "skipped": "no_valid_markers"}
    caption_tracks = (
        client.table("timeline_track")
        .select("id")
        .eq("timeline_id", timeline["id"])
        .eq("kind", "caption")
        .execute()
        .data or []
    )
    if not caption_tracks:
        return {"synced": 0, "skipped": "no_caption_tracks"}

    track_ids = [t["id"] for t in caption_tracks]
    caption_clips = (
        client.table("clip")
        .select("id, beat_index, start_ms, duration_ms")
        .in_("track_id", track_ids)
        .not_.is_("beat_index", "null")
        .execute()
        .data or []
    )

    synced = 0
    for clip in caption_clips:
        patch = _visual_position_patch(clip, positions)
        if patch:
            client.table("clip").update(patch).eq("id", clip["id"]).execute()
            synced += 1
    return {"synced": synced, "total": len(caption_clips)}


def _video_clips_by_beat(clips: list[dict]) -> dict[int, dict]:
    return {
        int(clip["beat_index"]): clip
        for clip in clips
        if clip.get("beat_index") is not None and clip.get("artifact_id")
    }


def _scene_entries(
    clips_by_beat: dict[int, dict], persona_by_artifact: dict[str, str]
) -> list[tuple[int, str, str]]:
    entries: list[tuple[int, str, str]] = []
    for beat in sorted(clips_by_beat):
        image_id = clips_by_beat[beat]["artifact_id"]
        persona = persona_by_artifact.get(image_id, "")
        entries.append((beat, persona if persona else f"_solo_{beat}", image_id))
    return entries


def _group_consecutive_scenes(
    entries: list[tuple[int, str, str]],
) -> list[list[tuple[int, str, str]]]:
    scenes = [[entries[0]]]
    for entry in entries[1:]:
        previous_beat, previous_key, _ = scenes[-1][-1]
        same_scene = (
            entry[0] == previous_beat + 1
            and entry[1] == previous_key
            and not entry[1].startswith("_solo_")
        )
        if same_scene:
            scenes[-1].append(entry)
        else:
            scenes.append([entry])
    return scenes


def _hold_scene_followers(
    client: Client,
    scenes: list[list[tuple[int, str, str]]],
    clips_by_beat: dict[int, dict],
) -> tuple[int, int]:
    held_beats = 0
    multi_beat_scenes = 0
    for scene in scenes:
        if len(scene) < 2:
            continue
        multi_beat_scenes += 1
        representative_image = scene[0][2]
        for beat, _key, _image in scene[1:]:
            clip = clips_by_beat[beat]
            if clip.get("artifact_id") == representative_image:
                continue
            client.table("clip").update(
                {
                    "artifact_id": representative_image,
                    "updated_at": _DATABASE_NOW,
                }
            ).eq("id", clip["id"]).execute()
            held_beats += 1
    return multi_beat_scenes, held_beats


def group_scenes_hold_image(client: Client, run_id: str) -> dict:
    """Hold one rendered image across consecutive beats of one persona."""
    timeline = (
        client.table("timeline").select("id").eq("run_id", run_id).limit(1).execute().data
    ) or []
    if not timeline:
        return {"scenes": 0, "skipped": _NO_TIMELINE}
    timeline_id = timeline[0]["id"]

    video_tracks = (
        client.table("timeline_track")
        .select("id")
        .eq("timeline_id", timeline_id)
        .eq("kind", "video")
        .execute()
        .data
    ) or []
    if not video_tracks:
        return {"scenes": 0, "skipped": _NO_VIDEO_TRACK}
    video_track_id = video_tracks[0]["id"]

    clips = (
        client.table("clip")
        .select("id, beat_index, artifact_id")
        .eq("track_id", video_track_id)
        .order("beat_index")
        .execute()
        .data
    ) or []
    clips_by_beat = _video_clips_by_beat(clips)
    if not clips_by_beat:
        return {"scenes": 0, "skipped": "no video clips with images"}
    art_rows = (
        client.table("artifact")
        .select("id, attributes")
        .in_("id", list({clip["artifact_id"] for clip in clips_by_beat.values()}))
        .execute()
        .data
    ) or []
    persona_by_artifact = {
        row["id"]: str(
            (row.get("attributes") or {}).get("persona_id") or ""
        ).strip()
        for row in art_rows
    }
    beats = sorted(clips_by_beat)
    scenes = _group_consecutive_scenes(
        _scene_entries(clips_by_beat, persona_by_artifact)
    )
    multi_beat_scenes, held_beats = _hold_scene_followers(
        client, scenes, clips_by_beat
    )
    return {
        "scenes": len(scenes),
        "multi_beat_scenes": multi_beat_scenes,
        "held_beats": held_beats,
        "total_beats": len(beats),
    }


def point_clips_to_own_beat_image(client: Client, run_id: str) -> dict:
    """B-SHOT1 (SV26 cut rhythm): every beat shows its OWN distinct rendered image.

    This is the inverse of :func:`group_scenes_hold_image`. The founder found one
    image holding across beats 1/2/3 for 27s — far too static for SV26 shorts,
    which cut roughly every 2–3s. Distinct per-beat images ARE generated (each
    beat has its own ``slot(track="visual")`` with a unique ``current_artifact_id``),
    but the legacy scene-hold overwrote follower beats' ``clip.artifact_id`` with
    the scene's first image.

    This re-points EVERY video clip back to its own beat's visual-slot artifact,
    so the rendered video lane cuts on every beat. It both repairs runs that were
    already held (clip.artifact_id was clobbered) and is the default pre-render
    step going forward. Scene continuity is preserved upstream by identity-lock in
    the visual prompts (same persona / place across beats), not by freezing one
    frame.

    Idempotent: a clip already pointing at its own beat image is left untouched.
    Beats with no visual slot artifact keep whatever they have (never nulled).
    """
    timeline = (
        client.table("timeline").select("id").eq("run_id", run_id).limit(1).execute().data
    ) or []
    if not timeline:
        return {"repointed": 0, "skipped": _NO_TIMELINE}
    timeline_id = timeline[0]["id"]

    video_tracks = (
        client.table("timeline_track")
        .select("id")
        .eq("timeline_id", timeline_id)
        .eq("kind", "video")
        .execute()
        .data
    ) or []
    if not video_tracks:
        return {"repointed": 0, "skipped": _NO_VIDEO_TRACK}
    video_track_ids = [t["id"] for t in video_tracks]

    # Each beat's distinct rendered image = its visual slot's current artifact.
    visual_slots = (
        client.table("slot")
        .select("beat_index, current_artifact_id")
        .eq("run_id", run_id)
        .eq("track", "visual")
        .filter("current_artifact_id", _NOT_IS, "null")
        .execute()
        .data
    ) or []
    image_by_beat: dict[int, str] = {
        int(s["beat_index"]): s["current_artifact_id"]
        for s in visual_slots
        if s.get("beat_index") is not None and s.get("current_artifact_id")
    }
    if not image_by_beat:
        return {"repointed": 0, "skipped": "no visual slot images"}

    clips = (
        client.table("clip")
        .select("id, beat_index, artifact_id")
        .in_("track_id", video_track_ids)
        .execute()
        .data
    ) or []

    repointed = 0
    distinct_beats: set[int] = set()
    for clip in clips:
        beat = clip.get("beat_index")
        if beat is None:
            continue
        own_image = image_by_beat.get(int(beat))
        if not own_image:
            continue
        distinct_beats.add(int(beat))
        if clip.get("artifact_id") == own_image:
            continue
        client.table("clip").update(
            {"artifact_id": own_image, "updated_at": _DATABASE_NOW}
        ).eq("id", clip["id"]).execute()
        repointed += 1

    return {
        "repointed": repointed,
        "beats_with_image": len(distinct_beats),
        "total_video_clips": len(clips),
    }


# ----------------------------------------------------------------
# B-SHOT2 — shot density: split long beats into sub-shots (cut rhythm)
# ----------------------------------------------------------------

# SV26 shorts cut roughly every 2–3s. A single per-beat image held for a long
# voice line (9–11s) reads as a static slide. This splits any long video clip
# into a SEQUENCE of sub-shots on the SAME track, each a distinct STATIC reframe
# (wide → medium → close → detail) of the beat's image. Because the renderer
# treats a clip with transforms.scale != 1 as MANUAL framing (skipping the
# ambient ken-burns drift), each sub-shot is a clean, deterministic, byte-stable
# crop of the SAME source image — so person/place identity stays perfectly
# locked (it is literally the same frame) while the on-screen framing cuts.
#
# Distinct sub-shot IMAGES (multi-generation) are a future enhancement; reframing
# the existing beat image needs zero extra image generation, renders identically
# in preview and Lambda, and works on every existing run.
_SUBSHOT_TARGET_MS = max(1200, int(os.environ.get("SUBSHOT_TARGET_MS", "2800")))
# Only beats longer than this are split (a ~4s beat stays one shot).
_SUBSHOT_MIN_SPLIT_MS = max(_SUBSHOT_TARGET_MS, int(os.environ.get("SUBSHOT_MIN_SPLIT_MS", "4200")))
_SUBSHOT_MAX = max(2, int(os.environ.get("SUBSHOT_MAX", "4")))
# Each sub-shot must hold at least this long (>= the _MIN_SCENE_MS floor so a
# sub-shot never sub-second-flickers).
_SUBSHOT_MIN_PIECE_MS = max(_MIN_SCENE_MS, int(os.environ.get("SUBSHOT_MIN_PIECE_MS", "1400")))

# Deterministic shot-size ladder. Each entry is a STATIC crop framing applied via
# clip.transforms. scale>1 zooms into the cover-fit image; x/y nudge the framing
# (the renderer maps x/y to translate(x*50%, y*50%)). Progressive push-in within a
# beat (wide establishing → detail) reads as an intentional edit.
_SHOT_LADDER: tuple[dict[str, Any], ...] = (
    {"shot_size": "wide", "scale": 1.06, "x": 0.0, "y": 0.0},
    {"shot_size": "medium", "scale": 1.34, "x": -0.10, "y": -0.16},
    {"shot_size": "close", "scale": 1.66, "x": 0.08, "y": -0.26},
    {"shot_size": "detail", "scale": 1.92, "x": -0.06, "y": 0.14},
)
# Product / proof beats want the tight end (hero macro on the real product), not a
# wide establishing shot, so they rotate through a tighter ladder.
_SHOT_LADDER_PRODUCT: tuple[dict[str, Any], ...] = (
    {"shot_size": "medium", "scale": 1.30, "x": 0.0, "y": -0.06},
    {"shot_size": "macro", "scale": 1.95, "x": 0.05, "y": 0.18},
    {"shot_size": "close", "scale": 1.62, "x": -0.08, "y": -0.20},
    {"shot_size": "wide", "scale": 1.10, "x": 0.0, "y": 0.0},
)


def _subshot_count(duration_ms: int) -> int:
    """How many sub-shots a clip of this length should become (1 = leave alone)."""
    if duration_ms < _SUBSHOT_MIN_SPLIT_MS:
        return 1
    n = max(2, min(_SUBSHOT_MAX, round(duration_ms / _SUBSHOT_TARGET_MS)))
    # Never produce a piece shorter than the floor.
    while n > 1 and duration_ms // n < _SUBSHOT_MIN_PIECE_MS:
        n -= 1
    return n


def _plan_subshots(
    start_ms: int, duration_ms: int, *, product: bool = False
) -> list[dict[str, Any]]:
    """Pure planner: a long clip window → an ordered list of sub-shot pieces.

    Each piece carries its absolute ``start_ms`` / ``duration_ms`` (contiguous,
    covering the original window exactly) and the static ``transforms`` framing
    for its shot size. Returns ``[]`` when the clip is too short to split.
    """
    n = _subshot_count(int(duration_ms))
    if n < 2:
        return []
    ladder = _SHOT_LADDER_PRODUCT if product else _SHOT_LADDER
    base = int(duration_ms) // n
    pieces: list[dict[str, Any]] = []
    for k in range(n):
        sub_start = int(start_ms) + k * base
        # Last piece absorbs the integer-division remainder so the sub-shots
        # tile the original window with no gap or overlap.
        sub_dur = (int(duration_ms) - k * base) if k == n - 1 else base
        frame = ladder[k % len(ladder)]
        pieces.append({
            "index": k,
            "start_ms": sub_start,
            "duration_ms": sub_dur,
            "shot_size": frame["shot_size"],
            "transforms": {
                "x": frame["x"],
                "y": frame["y"],
                "scale": frame["scale"],
                "rotation": 0,
                "opacity": 1,
            },
        })
    return pieces


def _planned_clip_subshots(
    clip: dict,
) -> tuple[int, dict[str, Any], list[dict[str, Any]]] | None:
    beat = clip.get("beat_index")
    if beat is None:
        return None
    attributes = dict(clip.get("attributes") or {})
    if attributes.get("subshot_count"):
        return None
    scene_type = str(attributes.get("scene_type") or "").lower()
    render_mode = str(attributes.get("render_mode") or "").lower()
    product = scene_type in ("product", "proof") or render_mode == "social_proof"
    pieces = _plan_subshots(
        int(clip.get("start_ms") or 0),
        int(clip.get("duration_ms") or 0),
        product=product,
    )
    if not pieces:
        return None
    return int(beat), attributes, pieces


def _subshot_attributes(
    attributes: dict[str, Any],
    piece: dict[str, Any],
    count: int,
    beat: int,
) -> dict[str, Any]:
    return {
        **attributes,
        "subshot_count": count,
        "subshot_index": piece["index"],
        "subshot_shot_size": piece["shot_size"],
        "subshot_of_beat": beat,
    }


def _materialize_subshots(
    client: Client,
    clip: dict,
    beat: int,
    attributes: dict[str, Any],
    pieces: list[dict[str, Any]],
) -> int:
    first = pieces[0]
    client.table("clip").update(
        {
            "duration_ms": first["duration_ms"],
            "transforms": first["transforms"],
            "attributes": _subshot_attributes(
                attributes, first, len(pieces), beat
            ),
            "updated_at": _DATABASE_NOW,
        }
    ).eq("id", clip["id"]).execute()
    for piece in pieces[1:]:
        client.table("clip").insert(
            {
                "track_id": clip["track_id"],
                "artifact_id": clip.get("artifact_id"),
                "start_ms": piece["start_ms"],
                "duration_ms": piece["duration_ms"],
                "in_ms": clip.get("in_ms") or 0,
                "out_ms": clip.get("out_ms"),
                "transforms": piece["transforms"],
                "effects": clip.get("effects") or [],
                "keyframes": clip.get("keyframes") or [],
                "text_content": clip.get("text_content"),
                "beat_index": None,
                "attributes": _subshot_attributes(
                    attributes, piece, len(pieces), beat
                ),
            }
        ).execute()
    return len(pieces) - 1


def split_long_beats_into_subshots(client: Client, run_id: str) -> dict:
    """Split long video beats into deterministic, idempotent reframed cuts."""
    timeline = (
        client.table("timeline").select("id").eq("run_id", run_id).limit(1).execute().data
    ) or []
    if not timeline:
        return {"split_beats": 0, "skipped": _NO_TIMELINE}
    timeline_id = timeline[0]["id"]

    video_tracks = (
        client.table("timeline_track")
        .select("id")
        .eq("timeline_id", timeline_id)
        .eq("kind", "video")
        .execute()
        .data
    ) or []
    if not video_tracks:
        return {"split_beats": 0, "skipped": _NO_VIDEO_TRACK}
    video_track_ids = [t["id"] for t in video_tracks]

    clips = (
        client.table("clip")
        .select(
            "id, track_id, beat_index, start_ms, duration_ms, artifact_id, "
            "in_ms, out_ms, transforms, effects, keyframes, text_content, attributes"
        )
        .in_("track_id", video_track_ids)
        .order("start_ms")
        .execute()
        .data
    ) or []

    split_beats = 0
    inserted = 0
    for clip in clips:
        planned = _planned_clip_subshots(clip)
        if not planned:
            continue
        beat, attributes, pieces = planned
        inserted += _materialize_subshots(
            client, clip, beat, attributes, pieces
        )
        split_beats += 1
    return {
        "split_beats": split_beats,
        "inserted_subshots": inserted,
        "total_video_clips": len(clips),
    }
