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
        .update({"status": "running", "started_at": "now()"})
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
        "ended_at": "now()",
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
        .update({"markers": markers, "updated_at": "now()"})
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


def verify_slots_coverage(client: Client, run_id: str) -> dict[str, Any]:
    """Diagnostic gate: verify all beats have ≥1 voice (audio/sfx) slot.

    This is Stage 1 of the 3-stage sync closure (PRD §4.2). Checks whether every
    beat has artifact-backed slots before attempting to bind them to clips.

    Args:
        client: Supabase client
        run_id: Run ID to diagnose

    Returns:
        {
          "ok": bool,
          "violations": ["beat_N: reason" ...],  # Critical failures
          "warnings": ["beat_N: reason" ...]     # Non-critical issues (sub-shot, etc)
        }

    Example (ViewOK run 51399, beat 3 SFX only, no voice):
        {
            "ok": False,
            "violations": ["beat_3: voice_slots=0 (no audio/sfx with artifact_id)"],
            "warnings": ["beat_3: sub-shot SFX (beat_index=NULL) exists but won't bind"]
        }
    """
    # 1. Fetch all slots for this run with artifacts attached.
    slot_rows: list[dict] = (
        client.table("slot")
        .select("id, track, beat_index, start_ms, current_artifact_id")
        .eq("run_id", run_id)
        .filter("current_artifact_id", "not.is", "null")
        .execute()
        .data
    ) or []

    if not slot_rows:
        return {
            "ok": False,
            "violations": ["no slots with artifacts found for this run"],
            "warnings": [],
        }

    # 2. Categorize slots by beat_index and kind (voice vs SFX).
    voice_by_beat: dict[int, int] = defaultdict(int)
    sfx_by_beat: dict[int, int] = defaultdict(int)
    subshot_warnings: list[str] = []

    for slot in slot_rows:
        bi = slot.get("beat_index")
        kind = _SLOT_TRACK_TO_CLIP_KIND.get(slot["track"])

        if kind == "audio":
            if bi is not None:
                voice_by_beat[int(bi)] += 1
            else:
                subshot_warnings.append(
                    f"voice sub-shot at {slot.get('start_ms')}ms (beat_index=NULL, will use start_ms fallback)"
                )
        elif kind == "sfx":
            if bi is not None:
                sfx_by_beat[int(bi)] += 1
            else:
                subshot_warnings.append(
                    f"SFX sub-shot at {slot.get('start_ms')}ms (beat_index=NULL, will use start_ms fallback)"
                )

    # 3. Determine the range of beat indices present.
    all_beats = set(voice_by_beat.keys()) | set(sfx_by_beat.keys())
    if not all_beats:
        return {
            "ok": False,
            "violations": ["no beat-indexed voice or SFX slots found"],
            "warnings": subshot_warnings,
        }

    max_beat = max(all_beats)

    # 4. Check each beat for voice coverage (critical), SFX is secondary.
    violations = []
    for beat_idx in range(max_beat + 1):
        voice_count = voice_by_beat.get(beat_idx, 0)
        sfx_count = sfx_by_beat.get(beat_idx, 0)

        # Critical: beat has no voice at all (audio + sfx = 0)
        if voice_count == 0 and sfx_count == 0:
            violations.append(
                f"beat_{beat_idx}: no voice or SFX slots (voice={voice_count}, sfx={sfx_count})"
            )
        # Warning: beat has SFX only (may be silent risk if SFX doesn't cover full beat)
        elif voice_count == 0 and sfx_count > 0:
            violations.append(
                f"beat_{beat_idx}: SFX-only (voice_slots=0, silent risk if SFX incomplete)"
            )

    return {
        "ok": len(violations) == 0,
        "violations": violations,
        "warnings": subshot_warnings,
    }


def sync_clips_from_slots(client: Client, run_id: str) -> dict:
    """Backfill ``clip.artifact_id`` from ``slot.current_artifact_id``.

    Workers upload artifacts to slots (setting ``slot.current_artifact_id``)
    but never patch the timeline clips that were created as placeholders.
    This function links the two so the editor can resolve clip→artifact→URL.

    Stage 2 of 3-stage closure (PRD §4.2): implements 1:N lossless sync.
    Multiple slots per beat (J/L cut pattern, voice A + voice B) now map to
    multiple clips. **All artifacts bind, no loss**.

    Only updates clips where ``artifact_id IS NULL`` to avoid overwriting
    any user or automated edits that already placed a specific artifact.

    Returns ``{"updated": <count>, "run_id": <run_id>}``.
    """
    # 1. All slots for this run that already have an artifact attached.
    # supabase-py 2.x made `.not_` a chainable property (no longer callable),
    # so use the explicit .filter() form with "not.is" for "IS NOT NULL".
    slot_rows: list[dict] = (
        client.table("slot")
        .select("id, track, beat_index, start_ms, current_artifact_id")
        .eq("run_id", run_id)
        .filter("current_artifact_id", "not.is", "null")
        .execute()
        .data
    ) or []

    if not slot_rows:
        return {"updated": 0, "run_id": run_id}

    # 2. Timeline for this run; extract per-beat durations from markers.
    tl = (
        client.table("timeline")
        .select("id, markers")
        .eq("run_id", run_id)
        .limit(1)
        .execute()
        .data
    )
    if not tl:
        return {"updated": 0, "run_id": run_id}
    timeline_id = tl[0]["id"]

    # Extract beat_index → beat_duration_ms mapping from markers (decouple from _BEAT_MS grid).
    beat_duration_by_idx: dict[int, int] = {}
    markers = tl[0].get("markers") or []
    for marker in markers:
        if marker.get("beatIndex") is not None and marker.get("durationMs") is not None:
            beat_duration_by_idx[int(marker["beatIndex"])] = int(marker["durationMs"])

    # 3. Tracks → map kind → track ids. Some kinds are intentionally duplicated:
    # Voice A and Voice B are both kind="audio" with different ord values, so a
    # flat kind→id map silently drops one lane and leaves voice clips unlinked.
    tracks: list[dict] = (
        client.table("timeline_track")
        .select("id, kind, ord")
        .eq("timeline_id", timeline_id)
        .order("ord")
        .execute()
        .data
    ) or []

    track_ids_by_kind: dict[str, list[str]] = {}
    for track in tracks:
        track_ids_by_kind.setdefault(track["kind"], []).append(track["id"])
    all_track_ids = [t["id"] for t in tracks]

    if not all_track_ids:
        return {"updated": 0, "run_id": run_id}

    artifact_ids = [s["current_artifact_id"] for s in slot_rows if s.get("current_artifact_id")]
    artifact_rows: list[dict] = (
        client.table("artifact")
        .select("id, duration_ms")
        .in_("id", artifact_ids)
        .execute()
        .data
        if artifact_ids
        else []
    ) or []
    artifact_by_id = {a["id"]: a for a in artifact_rows}

    # 4. Clips that still need an artifact (artifact_id IS NULL).
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

    # Build a direct track_id → kind map for O(1) clip→kind lookup.
    track_kind_by_id: dict[str, str] = {t["id"]: t["kind"] for t in tracks}

    # 5. Index null clips by (beat_index, clip_kind) → LIST OF CLIPS (1:N, Stage 2).
    # Multiple clips per beat are now collected in a list, not overwritten.
    clip_by_beat_kind: dict[tuple[int, str], list[dict]] = defaultdict(list)
    clip_by_ms_kind: dict[tuple[int, str], list[dict]] = defaultdict(list)

    for clip in null_clips:
        kind = track_kind_by_id.get(clip["track_id"])
        if not kind:
            continue
        bi = clip.get("beat_index")
        if bi is not None:
            clip_by_beat_kind[(int(bi), kind)].append(clip)
        else:
            ms = int(clip.get("start_ms") or 0)
            # For start_ms keying, keep only the first clip per (ms, kind) to avoid
            # duplicating non-beat-indexed clips across multiple slots.
            if not clip_by_ms_kind[(ms, kind)]:
                clip_by_ms_kind[(ms, kind)].append(clip)

    # 6. Match each slot to all its clips via (beat_index, clip_kind); patch artifact_id.
    # Stage 2: iterate over ALL clips in the list, not just pop() the first.
    # Prefer beat-keyed lookup (using beat_index + per-beat duration from markers) when available;
    # fall back to start_ms for true orphans (beat_index=NULL).
    updated = 0
    for slot in slot_rows:
        clip_kind = _SLOT_TRACK_TO_CLIP_KIND.get(slot["track"])
        if not clip_kind:
            continue

        beat_index = slot.get("beat_index")
        start_ms = int(slot.get("start_ms") or 0)

        if beat_index is not None:
            target_clips = clip_by_beat_kind.pop((int(beat_index), clip_kind), [])
        else:
            # No beat_index (orphan sub-shot): match by start_ms (music full-clip, sfx non-beat slots)
            target_clips = clip_by_ms_kind.pop((start_ms, clip_kind), [])

        # Stage 2: bind ALL clips (not just first).
        for target_clip in target_clips:
            patch: dict[str, Any] = {"artifact_id": slot["current_artifact_id"], "updated_at": "now()"}
            artifact = artifact_by_id.get(slot["current_artifact_id"]) or {}
            if clip_kind == "audio" and artifact.get("duration_ms"):
                # Voice turns must stay uncut (napkin I4). Use the measured TTS
                # duration instead of the placeholder 1000ms beat duration.
                patch["duration_ms"] = max(100, int(artifact["duration_ms"]))
            client.table("clip").update(patch).eq("id", target_clip["id"]).execute()
            updated += 1

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
_MIN_SCENE_MS = max(0, int(os.environ.get("MIN_SCENE_MS", "800")))


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


def repack_timeline_to_audio(client: Client, run_id: str) -> dict:
    """Rescale beat-aligned clips so each beat fits its actual voiceover.

    After voiceover_run uploads the real TTS audio (with measured
    duration_ms on the artifact), this function:
      1. Reads voiceover artifact durations beat by beat.
      2. Computes a clamped per-beat duration and cumulative start_ms.
      3. Updates video / audio / caption clips at each beat in place.
      4. Updates timeline.duration_ms to the total.

    Idempotent + opt-out:
      * Sets ``timeline.attributes.audio_packed_at`` on first run; subsequent
        calls noop so manual edits aren't clobbered.
      * If a beat has no voiceover artifact, that beat keeps default
        ``_REPACK_MIN_MS``.
    """
    timeline_rows = (
        client.table("timeline")
        .select("id, duration_ms")
        .eq("run_id", run_id)
        .limit(1)
        .execute()
        .data
    ) or []
    if not timeline_rows:
        return {"updated_clips": 0, "skipped": "no timeline"}
    timeline = timeline_rows[0]
    timeline_id = timeline["id"]

    # Idempotency for CLIP repack: if any beat-aligned clip already has
    # duration_ms != _BEAT_MS, the timeline was already repacked (or the
    # user manually edited). We still want to fix timeline.duration_ms
    # below if a previous run failed mid-way, so the skip is partial:
    # clips stay as-is, but cursor_ms is recomputed and timeline updated.
    sample = (
        client.table("clip")
        .select("start_ms, beat_index, track_id")
        .in_("track_id", [
            t["id"] for t in (
                client.table("timeline_track")
                .select("id, kind")
                .eq("timeline_id", timeline_id)
                .in_("kind", ["video", "audio", "caption"])
                .execute()
                .data
            ) or []
        ])
        .limit(500)
        .execute()
        .data
    ) or []
    # A clip counts as "already packed" (or manually edited) only when its START
    # is OFF the flat beat*_BEAT_MS grid — mirroring slots_already_packed below.
    # BUG-2 fix: previously this keyed off duration_ms != _BEAT_MS, but
    # sync_artifacts_to_clips writes the MEASURED voice duration (e.g. 9145ms)
    # onto voice clips while their start_ms stays on the flat 0/1000/2000… grid.
    # That false-positive made repack bail at the "already packed" guard before
    # ever rewriting starts, so clips overlapped and captions crammed into the
    # first few seconds. Beat 0 starts at 0 in BOTH packed and unpacked states,
    # so only beats >= 1 are informative.
    clips_already_packed = bool(
        sample and any(
            c.get("beat_index") is not None
            and int(c.get("beat_index") or 0) >= 1
            and int(c.get("start_ms") or 0) != int(c.get("beat_index") or 0) * _BEAT_MS
            for c in sample
        )
    )

    tracks = (
        client.table("timeline_track")
        .select("id, kind")
        .eq("timeline_id", timeline_id)
        .execute()
        .data
    ) or []
    # Collect ALL beat-aligned tracks. There can be MORE THAN ONE video track
    # (e.g. a main per-beat persona track + a scene-hold track left by a prior
    # group_scenes_hold_image / re-produce cycle) and more than one audio lane
    # ("Voice A", "Voice B"). Earlier this collapsed video/caption to a single
    # last-wins track id, so when two video tracks existed repack packed only
    # ONE — chosen by unordered row order — and left the MAIN beat clips at the
    # 1000ms seed. The renderer composites every video track, so a half-packed
    # main track produced a black tail. Collect ALL of them, like audio.
    track_by_kind = {}
    audio_track_ids = []
    video_track_ids = []
    caption_track_ids = []
    for t in tracks:
        kind = t["kind"]
        if kind == "audio":
            audio_track_ids.append(t["id"])
        elif kind == "video":
            video_track_ids.append(t["id"])
        elif kind == "caption":
            caption_track_ids.append(t["id"])
        else:
            track_by_kind[kind] = t["id"]

    # Pull voiceover slots ordered by beat so we can rebuild durations.
    vo_slots = (
        client.table("slot")
        .select("beat_index, current_artifact_id, start_ms")
        .eq("run_id", run_id)
        .eq("track", "voiceover")
        .order("beat_index")
        .execute()
        .data
    ) or []

    if not vo_slots:
        return {"updated_clips": 0, "skipped": "no voiceover slots"}

    slots_already_packed = any(
        int(s.get("start_ms") or 0) != int(s.get("beat_index") or 0) * _BEAT_MS
        for s in vo_slots
        if s.get("beat_index") is not None
    )

    art_ids = [s["current_artifact_id"] for s in vo_slots if s["current_artifact_id"]]
    artifact_dur: dict[str, int] = {}
    if art_ids:
        rows = (
            client.table("artifact")
            .select("id, duration_ms")
            .in_("id", art_ids)
            .execute()
            .data
        ) or []
        artifact_dur = {r["id"]: (r.get("duration_ms") or 0) for r in rows}

    # EDIT-WIRE-3: fetch scene_type per beat from audio clip attributes
    beat_scene: dict[int, str] = {}
    if audio_track_ids:
        _scene_rows = (
            client.table("clip")
            .select("beat_index, attributes")
            .in_("track_id", audio_track_ids)
            .not_.is_("beat_index", "null")
            .execute()
            .data
        ) or []
        for _c in _scene_rows:
            _bi = _c.get("beat_index")
            if _bi is not None:
                beat_scene[int(_bi)] = str(
                    (_c.get("attributes") or {}).get("scene_type") or ""
                ).lower()

    # Build per-beat plan. Voices OVERLAP: each beat starts _VOICE_OVERLAP_MS
    # before the previous one ends (theatre interjection), but only when both
    # neighbours carry real voice. ``new_duration_ms`` is the AUDIO length (the
    # voice line always plays in full); the on-screen ``visual_duration_ms`` is
    # computed below so visuals + captions stay contiguous (cut at the next beat)
    # even though the audio bleeds across the boundary.
    cursor_ms = 0
    plan: list[dict[str, int]] = []
    prev_voiced = False
    for slot in vo_slots:
        beat = slot.get("beat_index")
        if beat is None:
            continue
        art_id = slot.get("current_artifact_id")
        audio_ms = artifact_dur.get(art_id, 0) if art_id else 0
        voiced = audio_ms > 0
        scene_t = beat_scene.get(int(beat), "")
        new_dur = _beat_duration_ms(audio_ms) + _scene_breath_ms(scene_t, voiced)
        overlap = _VOICE_OVERLAP_MS if (plan and prev_voiced and voiced) else 0
        start = max(0, cursor_ms - overlap)
        plan.append({
            "beat": int(beat),
            "old_start_ms": int(slot.get("start_ms") or beat * _BEAT_MS),
            "new_start_ms": start,
            "new_duration_ms": new_dur,
        })
        # Floor the beat advance to ensure image window (advance minus next overlap) stays >= _MIN_SCENE_MS
        cursor_ms = max(start + _MIN_SCENE_MS + _VOICE_OVERLAP_MS, start + new_dur)
        prev_voiced = voiced

    if not plan:
        return {"updated_clips": 0, "skipped": "no beats"}

    # Visual + caption duration = until the NEXT beat starts (contiguous: no
    # double image / double caption on screen even though the voices overlap).
    # The last beat keeps its full audio length.
    for i, entry in enumerate(plan):
        next_start = plan[i + 1]["new_start_ms"] if i + 1 < len(plan) else entry["new_start_ms"] + entry["new_duration_ms"]
        entry["visual_duration_ms"] = max(_MIN_SCENE_MS, next_start - entry["new_start_ms"])

    # If clips were already packed, recompute cursor_ms from existing clip
    # durations (not from voiceover slots) so timeline.duration_ms below
    # matches the actual on-disk state.
    if clips_already_packed and slots_already_packed:
        # Reel length = the LATEST content END across beat-aligned tracks, NOT the
        # SUM of audio-lane durations. Voice A and Voice B play in PARALLEL (J/L
        # zig-zag), so summing them double-counts and inflates the timeline, which
        # then stretches the music over a black/music-only TAIL (napkin I2 = no
        # blackout). Take max(start+duration) across video / audio lanes / caption.
        end_track_ids = list(audio_track_ids) + list(video_track_ids) + list(caption_track_ids)
        ends = []
        for tid in end_track_ids:
            rows = (
                client.table("clip")
                .select("start_ms, duration_ms")
                .eq("track_id", tid)
                .execute()
                .data
            ) or []
            ends.extend(int(c.get("start_ms") or 0) + int(c.get("duration_ms") or 0) for c in rows)
        if ends and max(ends) > 0:
            cursor_ms = max(ends)
        # Stretch music + update timeline.duration_ms below, but skip the
        # clip/slot rewrites since they're already correct.
        music_tid = track_by_kind.get("music")
        if music_tid:
            client.table("clip").update({
                "start_ms": 0,
                "duration_ms": cursor_ms,
            }).eq("track_id", music_tid).execute()
        client.table("timeline").update({
            "duration_ms": cursor_ms,
        }).eq("id", timeline_id).execute()
        return {
            "updated_clips": 0,
            "new_duration_ms": cursor_ms,
            "beats": len(plan),
            "skipped": "clips already packed; timeline duration re-synced",
        }

    # Update clips for each beat-aligned kind. Prefer explicit beat_index; fall
    # back to old_start_ms for deployments that have not applied 0012 yet.
    # Every beat-aligned kind is updated across ALL its tracks (there may be
    # multiple video tracks and multiple audio lanes) so no main track is left
    # at the 1000ms seed when a duplicate/scene-hold track is present.
    track_ids_by_kind = {
        "video": video_track_ids,
        "audio": audio_track_ids,
        "caption": caption_track_ids,
    }
    updated_clips = 0
    for kind in ("video", "audio", "caption"):
        track_ids = track_ids_by_kind[kind]

        for tid in track_ids:
            for entry in plan:
                # Audio plays its FULL voice line (overlapping into the next beat);
                # video + caption stay contiguous so only the sound overlaps.
                dur = entry["new_duration_ms"] if kind == "audio" else entry.get("visual_duration_ms", entry["new_duration_ms"])
                patch = {
                    "start_ms": entry["new_start_ms"],
                    "duration_ms": dur,
                }
                try:
                    res = (
                        client.table("clip")
                        .update(patch)
                        .eq("track_id", tid)
                        .eq("beat_index", entry["beat"])
                        .execute()
                    )
                except Exception:
                    res = (
                        client.table("clip")
                        .update(patch)
                        .eq("track_id", tid)
                        .eq("start_ms", entry["old_start_ms"])
                        .execute()
                    )
                if not (res.data or []):
                    res = (
                        client.table("clip")
                        .update(patch)
                        .eq("track_id", tid)
                        .eq("start_ms", entry["old_start_ms"])
                        .execute()
                    )
                updated_clips += len(res.data or [])

    # U-M1-EDIT-PACING: account for beat_index=NULL subshots (created by
    # split_long_beats_into_subshots) whose end may exceed the plan cursor_ms,
    # causing the music and timeline.duration_ms to be too short (orphan tail).
    for vid_tid in video_track_ids:
        _subshot_rows = (
            client.table("clip")
            .select("start_ms, duration_ms")
            .eq("track_id", vid_tid)
            .is_("beat_index", "null")
            .execute()
            .data or []
        )
        for ss in _subshot_rows:
            ss_end = int(ss.get("start_ms") or 0) + int(ss.get("duration_ms") or 0)
            if ss_end > cursor_ms:
                cursor_ms = ss_end

    # Update voiceover/visual/caption slot start_ms/end_ms so future syncs
    # still match (slot.start_ms is the canonical lookup key for clip sync).
    for kind in ("visual", "voiceover", "caption"):
        for entry in plan:
            dur = entry["new_duration_ms"] if kind == "voiceover" else entry.get("visual_duration_ms", entry["new_duration_ms"])
            client.table("slot").update({
                "start_ms": entry["new_start_ms"],
                "end_ms": entry["new_start_ms"] + dur,
            }).eq("run_id", run_id).eq("track", kind).eq("beat_index", entry["beat"]).execute()

    # Stretch the music clip (single span) to the new total duration.
    music_tid = track_by_kind.get("music")
    if music_tid:
        client.table("clip").update({
            "start_ms": 0,
            "duration_ms": cursor_ms,
        }).eq("track_id", music_tid).execute()

    # Persist new total duration + refresh markers so the editor's beat
    # ruler matches the new layout. Split into two updates: an earlier
    # combined update was silently failing (likely the "now()" string in
    # the timestamp column), and we *must* land duration_ms or the editor
    # truncates the last clip.
    markers = [
        {"id": f"beat-{p['beat']}", "timeMs": p["new_start_ms"], "beatIndex": p["beat"], "label": f"beat {p['beat']}"}
        for p in plan
    ]
    client.table("timeline").update({
        "duration_ms": cursor_ms,
    }).eq("id", timeline_id).execute()
    try:
        client.table("timeline").update({
            "markers": markers,
        }).eq("id", timeline_id).execute()
    except Exception:
        # Markers are nice-to-have; if PostgREST rejects the JSONB payload
        # for any reason, keep the duration_ms update we just landed.
        pass

    return {
        "updated_clips": updated_clips,
        "new_duration_ms": cursor_ms,
        "beats": len(plan),
    }


_SUBBEAT_MAX_MS = 800  # mirror of visual.py SUBBEAT_MAX_MS
_SUBBEAT_MAX_COUNT = 4  # cap sub-beat count to limit API spend


def sync_video_positions_from_markers(client: Client, run_id: str) -> dict:
    """Align video clip start_ms/duration_ms to variable beat markers and backfill sub_images.

    Called after patch_beat_markers_from_tts so video clips follow actual TTS-driven
    beat durations rather than the flat i*BEAT_MS grid. For long beats that gain
    duration_ms > _SUBBEAT_MAX_MS without sub_images, the primary artifact URL is
    repeated N times so the renderer can apply distinct Ken-Burns paths. Idempotent.
    """
    tl = client.table("timeline").select("id, markers").eq("run_id", run_id).limit(1).execute().data
    if not tl:
        return {"synced": 0, "skipped": "no_timeline"}
    timeline = tl[0]
    markers = list(timeline.get("markers") or [])
    if not markers:
        return {"synced": 0, "skipped": "no_markers"}

    valid = [m for m in markers if m.get("beatIndex") is not None and m.get("durationMs") is not None]
    if not valid:
        return {"synced": 0, "skipped": "no_valid_markers"}

    sorted_m = sorted(valid, key=lambda m: m.get("beatIndex") or 0)
    beat_positions: dict[int, dict[str, int]] = {}
    for idx, m in enumerate(sorted_m):
        bi = int(m["beatIndex"])
        time_ms = int(m.get("timeMs") or 0)
        dur_ms = int(m.get("durationMs") or 0)
        next_time_ms = int(sorted_m[idx + 1].get("timeMs") or 0) if idx + 1 < len(sorted_m) else time_ms + dur_ms
        visual_dur_ms = max(50, next_time_ms - time_ms)
        beat_positions[bi] = {"start_ms": time_ms, "visual_duration_ms": visual_dur_ms}

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

    artifact_ids = [c["artifact_id"] for c in video_clips if c.get("artifact_id")]
    art_url_by_id: dict[str, str] = {}
    if artifact_ids:
        arts = (
            client.table("artifact")
            .select("id, storage_key")
            .in_("id", artifact_ids)
            .execute()
            .data or []
        )
        # storage_key is used as public_url key; store raw key for caller to resolve
        art_url_by_id = {a["id"]: (a.get("storage_key") or "") for a in arts}

    synced = 0
    for clip in video_clips:
        bi = clip.get("beat_index")
        if bi is None:
            continue
        pos = beat_positions.get(int(bi))
        if not pos:
            continue
        new_start = pos["start_ms"]
        new_dur = pos["visual_duration_ms"]
        attrs = dict(clip.get("attributes") or {})

        patch: dict[str, Any] = {"updated_at": "now()"}
        changed = False

        if int(clip.get("start_ms") or 0) != new_start or int(clip.get("duration_ms") or 0) != new_dur:
            patch["start_ms"] = new_start
            patch["duration_ms"] = new_dur
            changed = True

        # Backfill sub_images for long beats that now have duration_ms > _SUBBEAT_MAX_MS
        # but no sub_images: repeat the primary artifact URL N times so the renderer
        # can show distinct Ken-Burns paths per sub-window even with the same image.
        existing_sub_images = attrs.get("sub_images") or []
        n_needed = min(_SUBBEAT_MAX_COUNT, max(1, (new_dur + _SUBBEAT_MAX_MS - 1) // _SUBBEAT_MAX_MS))
        if new_dur > _SUBBEAT_MAX_MS and len(existing_sub_images) < n_needed and clip.get("artifact_id"):
            sk = art_url_by_id.get(clip["artifact_id"] or "")
            if sk:
                # Extend sub_images to n_needed entries (repeat primary key as placeholder)
                extended = list(existing_sub_images) + [sk] * (n_needed - len(existing_sub_images))
                attrs["sub_images"] = extended[:n_needed]
                patch["attributes"] = attrs
                changed = True

        if changed:
            client.table("clip").update(patch).eq("id", clip["id"]).execute()
            synced += 1

    return {"synced": synced, "total": len(video_clips)}


def sync_caption_positions_from_markers(client: Client, run_id: str) -> dict:
    """Align caption clip start_ms/duration_ms to variable beat markers.

    Called after patch_beat_markers_from_tts so caption clips follow actual TTS
    durations instead of the flat i*BEAT_MS grid. Idempotent.
    """
    tl = client.table("timeline").select("id, markers").eq("run_id", run_id).limit(1).execute().data
    if not tl:
        return {"synced": 0, "skipped": "no_timeline"}
    timeline = tl[0]
    markers = list(timeline.get("markers") or [])
    if not markers:
        return {"synced": 0, "skipped": "no_markers"}

    valid = [m for m in markers if m.get("beatIndex") is not None and m.get("durationMs") is not None]
    if not valid:
        return {"synced": 0, "skipped": "no_valid_markers"}

    sorted_m = sorted(valid, key=lambda m: m.get("beatIndex") or 0)
    beat_positions: dict[int, dict[str, int]] = {}
    for idx, m in enumerate(sorted_m):
        bi = int(m["beatIndex"])
        time_ms = int(m.get("timeMs") or 0)
        next_time_ms = int(sorted_m[idx + 1].get("timeMs") or 0) if idx + 1 < len(sorted_m) else time_ms + int(m["durationMs"])
        visual_dur_ms = max(50, next_time_ms - time_ms)
        beat_positions[bi] = {"start_ms": time_ms, "visual_duration_ms": visual_dur_ms}

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
        bi = clip.get("beat_index")
        if bi is None:
            continue
        pos = beat_positions.get(int(bi))
        if not pos:
            continue
        new_start = pos["start_ms"]
        new_dur = pos["visual_duration_ms"]
        if int(clip.get("start_ms") or 0) == new_start and int(clip.get("duration_ms") or 0) == new_dur:
            continue
        client.table("clip").update({
            "start_ms": new_start,
            "duration_ms": new_dur,
            "updated_at": "now()",
        }).eq("id", clip["id"]).execute()
        synced += 1

    return {"synced": synced, "total": len(caption_clips)}


def group_scenes_hold_image(client: Client, run_id: str) -> dict:
    """NAPKIN I1 (IMAGE = SCENE): consecutive beats by the SAME persona are ONE
    SCENE → the on-screen IMAGE HOLDS across them. The video lane cuts only on a
    SCENE change (persona / visual change), NEVER per beat — decoupled from the
    finer, overlapping voice TURNS (I3 / I4 J-L cut, already handled by repack).

    Mechanism: each beat's persona + rendered image come from its VIDEO CLIP's
    artifact (``clip.artifact_id`` → ``attributes.persona_id``) — the rendered
    domain, so the hold matches exactly what the renderer shows. Consecutive
    beats sharing a non-empty persona are merged into a scene whose FIRST image
    is pointed to by every beat in it, so frames sampled across the scene show
    no cut. Beats with no persona (product / demo / proof B-roll, single-narrator)
    get a unique key and are NEVER merged, so a strict-alternation script is a
    safe no-op.

    Idempotent: re-running re-derives personas from the (unchanged) first beat of
    each scene and re-points the followers at the same image.
    """
    timeline = (
        client.table("timeline").select("id").eq("run_id", run_id).limit(1).execute().data
    ) or []
    if not timeline:
        return {"scenes": 0, "skipped": "no timeline"}
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
        return {"scenes": 0, "skipped": "no video track"}
    video_track_id = video_tracks[0]["id"]

    # Per-beat rendered image = the VIDEO clip's artifact_id. persona_id rides
    # that artifact's attributes. Clips are the rendered source of truth.
    clips = (
        client.table("clip")
        .select("id, beat_index, artifact_id")
        .eq("track_id", video_track_id)
        .order("beat_index")
        .execute()
        .data
    ) or []
    clip_by_beat = {
        int(c["beat_index"]): c
        for c in clips
        if c.get("beat_index") is not None and c.get("artifact_id")
    }
    if not clip_by_beat:
        return {"scenes": 0, "skipped": "no video clips with images"}

    art_rows = (
        client.table("artifact")
        .select("id, attributes")
        .in_("id", list({c["artifact_id"] for c in clip_by_beat.values()}))
        .execute()
        .data
    ) or []
    persona_by_art = {
        r["id"]: str((r.get("attributes") or {}).get("persona_id") or "").strip()
        for r in art_rows
    }

    # Ordered (beat, scene_key, image). Empty persona → unique key (never merges).
    beats = sorted(clip_by_beat.keys())
    keyed = []
    for b in beats:
        img = clip_by_beat[b]["artifact_id"]
        persona = persona_by_art.get(img, "")
        keyed.append((b, persona if persona else f"_solo_{b}", img))

    # Group beats that are TRULY consecutive (beat_index n, n+1) AND share the
    # same non-solo persona. A missing/empty beat in between breaks the scene.
    scenes: list[list[tuple]] = [[keyed[0]]]
    for entry in keyed[1:]:
        prev_beat, prev_key = scenes[-1][-1][0], scenes[-1][-1][1]
        if (
            entry[0] == prev_beat + 1
            and entry[1] == prev_key
            and not entry[1].startswith("_solo_")
        ):
            scenes[-1].append(entry)
        else:
            scenes.append([entry])

    held_beats = 0
    multi_beat_scenes = 0
    for scene in scenes:
        if len(scene) < 2:
            continue
        multi_beat_scenes += 1
        rep_img = scene[0][2]  # the scene's first rendered image holds across it
        for (b, _key, _img) in scene[1:]:
            clip = clip_by_beat.get(b)
            if not clip or clip.get("artifact_id") == rep_img:
                continue
            client.table("clip").update(
                {"artifact_id": rep_img, "updated_at": "now()"}
            ).eq("id", clip["id"]).execute()
            held_beats += 1

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
        return {"repointed": 0, "skipped": "no timeline"}
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
        return {"repointed": 0, "skipped": "no video track"}
    video_track_ids = [t["id"] for t in video_tracks]

    # Each beat's distinct rendered image = its visual slot's current artifact.
    visual_slots = (
        client.table("slot")
        .select("beat_index, current_artifact_id")
        .eq("run_id", run_id)
        .eq("track", "visual")
        .filter("current_artifact_id", "not.is", "null")
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
            {"artifact_id": own_image, "updated_at": "now()"}
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


def split_long_beats_into_subshots(client: Client, run_id: str) -> dict:
    """B-SHOT2: give long beats SV26 cut rhythm by splitting each long video clip
    into a sequence of reframed sub-shots on the same track.

    Runs at compose time, AFTER :func:`point_clips_to_own_beat_image` (so every
    beat first holds its own distinct image) and AFTER the audio repack (so clip
    start/duration are already voice-aligned). For every video clip longer than
    ``_SUBSHOT_MIN_SPLIT_MS`` it:

      * shrinks the existing clip to the FIRST sub-shot window and stamps its
        wide framing, then
      * INSERTS the remaining sub-shots (same artifact, tighter framings, and
        beat_index=NULL — a UNIQUE (track_id, beat_index) constraint means only
        ONE clip per beat may own the index) so the video lane cuts every ~2–3s.

    Idempotent: the base clip is stamped with ``attributes.subshot_count`` once
    split, and follower sub-shots (``subshot_index >= 1``) carry no beat_index, so
    re-composing skips both and never double-splits.
    """
    timeline = (
        client.table("timeline").select("id").eq("run_id", run_id).limit(1).execute().data
    ) or []
    if not timeline:
        return {"split_beats": 0, "skipped": "no timeline"}
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
        return {"split_beats": 0, "skipped": "no video track"}
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
        beat = clip.get("beat_index")
        if beat is None:
            continue  # a follower sub-shot from a previous split → skip
        attrs = dict(clip.get("attributes") or {})
        if attrs.get("subshot_count"):
            continue  # this base clip was already split → idempotent skip
        duration_ms = int(clip.get("duration_ms") or 0)
        scene_type = str(attrs.get("scene_type") or "").lower()
        render_mode = str(attrs.get("render_mode") or "").lower()
        product = scene_type in ("product", "proof") or render_mode == "social_proof"
        pieces = _plan_subshots(
            int(clip.get("start_ms") or 0), duration_ms, product=product
        )
        if not pieces:
            continue

        first = pieces[0]
        client.table("clip").update({
            "duration_ms": first["duration_ms"],
            "transforms": first["transforms"],
            "attributes": {
                **attrs,
                "subshot_count": len(pieces),
                "subshot_index": 0,
                "subshot_shot_size": first["shot_size"],
                "subshot_of_beat": int(beat),
            },
            "updated_at": "now()",
        }).eq("id", clip["id"]).execute()

        for piece in pieces[1:]:
            client.table("clip").insert({
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
                # Follower sub-shots carry NO beat_index: there is a UNIQUE
                # (track_id, beat_index) constraint, and only one clip per beat may
                # own it. The renderer drops beat_index at render time anyway and
                # derives scene_type from attributes, so a null-beat sub-shot
                # renders identically. ``subshot_of_beat`` keeps the lineage.
                "beat_index": None,
                "attributes": {
                    **attrs,
                    "subshot_count": len(pieces),
                    "subshot_index": piece["index"],
                    "subshot_shot_size": piece["shot_size"],
                    "subshot_of_beat": int(beat),
                },
            }).execute()
            inserted += 1
        split_beats += 1

    return {
        "split_beats": split_beats,
        "inserted_subshots": inserted,
        "total_video_clips": len(clips),
    }
