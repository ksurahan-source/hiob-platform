"""Place a reusable library video into a run's timeline as clip(s).

The napkin's "human places reusable videos where the script wants" — the FREE
half of the cost rule (generate once, place/repeat for free). Given a library
video (``asset_library_item``) and one or more beats, this:

  1. Creates ONE ``artifact`` row mirroring the library item's stored media
     (storage_key/sha256/mime/… copied; source='imported'). The clip model is
     unchanged — we only point existing FKs at this artifact.
  2. For each beat: ensures a ``visual`` slot, points ``slot.current_artifact_id``
     at the artifact, and creates a video ``clip`` referencing the same artifact.

"Repeat for emphasis" = the same artifact placed at multiple beats (Q3:
multi-beat only, no per-clip repeat column). The shared artifact means zero
extra generation cost. The result renders via the live composer
(``composer_v2.build_clip_render_props`` → renderer) because every clip resolves
``artifact_id -> storage_key -> public_url``.
"""
from __future__ import annotations

from typing import Any

from supabase import Client

from hiob_platform.team import create_clip, ensure_timeline, ensure_track

# Per-beat fallback duration when the library item carries no duration_ms.
_DEFAULT_CLIP_MS = 4000


def _next_artifact_version(client: Client, slot_id: str) -> int:
    existing = (
        client.table("artifact")
        .select("version")
        .eq("slot_id", slot_id)
        .order("version", desc=True)
        .limit(1)
        .execute()
        .data
    )
    return (existing[0]["version"] + 1) if existing else 1


def _ensure_slot(
    client: Client, *, run_id: str, track: str, beat_index: int, start_ms: int, end_ms: int
) -> dict:
    existing = (
        client.table("slot")
        .select("*")
        .eq("run_id", run_id)
        .eq("track", track)
        .eq("beat_index", beat_index)
        .limit(1)
        .execute()
        .data
    )
    if existing:
        return existing[0]
    return (
        client.table("slot")
        .insert(
            {
                "run_id": run_id,
                "track": track,
                "beat_index": beat_index,
                "start_ms": start_ms,
                "end_ms": end_ms,
            }
        )
        .execute()
        .data[0]
    )


def _artifact_from_item(client: Client, *, run_id: str, slot_id: str, item: dict, reuse: str) -> dict:
    """Create one ``artifact`` row mirroring a library item's stored media."""
    return (
        client.table("artifact")
        .insert(
            {
                "run_id": run_id,
                "slot_id": slot_id,
                "version": _next_artifact_version(client, slot_id),
                "source": "imported",
                "storage": item.get("storage", "supabase"),
                "storage_key": item["storage_key"],
                "sha256": item["sha256"],
                "mime": item["mime"],
                "bytes": item.get("bytes"),
                "duration_ms": item.get("duration_ms"),
                "width": item.get("width"),
                "height": item.get("height"),
                "attributes": {
                    "placed_from_asset_library_item": item.get("id"),
                    "source": "imported",
                    "reuse": reuse,
                },
            }
        )
        .execute()
        .data[0]
    )


def _resolve_placements(
    ordered_beats: list[int],
    existing_by_beat: dict[int, tuple[int, int]],
    per: int,
    base_end: int,
    *,
    at_ms: int | None = None,
    mode: str = "at_beat",
) -> list[dict[str, int]]:
    """Pure: decide WHERE each placement lands → [{beat, start_ms, duration_ms}].

    * ``at_ms`` (explicit timeline position) wins → one clip at that exact time.
    * ``mode="at_beat"`` (demo placement): place AT each beat's real timeline
      position, overlaying the existing clip there (the napkin's "place the demo
      in the hook / repeat for emphasis"). A beat with no existing clip yet is
      appended after the current end. REPEAT/MULTIPLE = the same beats list with
      >1 entry (the caller reuses ONE artifact → no re-spend).
    * ``mode="append"`` (a NEW scene, e.g. social proof): lay sequentially after
      everything else so separate scenes don't stack at 0ms (legacy behavior).
    """
    if at_ms is not None:
        beat = ordered_beats[0] if ordered_beats else 0
        return [{"beat": int(beat), "start_ms": int(at_ms), "duration_ms": per}]
    out: list[dict[str, int]] = []
    if mode == "append":
        for i, b in enumerate(ordered_beats):
            out.append({"beat": int(b), "start_ms": base_end + i * per, "duration_ms": per})
        return out
    cursor = base_end
    for b in ordered_beats:
        if int(b) in existing_by_beat:
            start_ms, dur = existing_by_beat[int(b)]
            out.append({"beat": int(b), "start_ms": int(start_ms), "duration_ms": int(dur or per)})
        else:
            out.append({"beat": int(b), "start_ms": cursor, "duration_ms": per})
            cursor += per
    return out


def _place_media(
    client: Client,
    *,
    run_id: str,
    item: dict,
    beats: list[int],
    duration_ms: int | None,
    aspect: str,
    effects: list | None,
    reuse: str,
    attributes: dict[str, Any] | None = None,
    mode: str = "append",
    at_ms: int | None = None,
) -> dict[str, Any]:
    """Core bridge: library item → one shared artifact → visual slot + video
    clip per placement (clip model unchanged). ``effects`` attach to each clip.

    ``mode`` / ``at_ms`` choose WHERE clips land (see ``_resolve_placements``):
    demo placement overlays at chosen beats/time (arbitrary, repeat, multiple);
    a new scene (social proof) appends. The SAME artifact is reused across all
    placements → free repetition (napkin cost rule, no re-spend).
    """
    if not beats and at_ms is None:
        raise ValueError("beats must be a non-empty list (or at_ms given)")
    if not item.get("storage_key"):
        raise ValueError("library item has no storage_key")

    per = int(duration_ms or item.get("duration_ms") or _DEFAULT_CLIP_MS)
    ordered = sorted(set(int(b) for b in beats)) if beats else []

    timeline = ensure_timeline(client, run_id=run_id, duration_ms=per * max(1, len(ordered)), aspect=aspect)
    track = ensure_track(
        client, timeline_id=timeline["id"], kind="video", label="Video", ord=0, z_index=0
    )

    existing = (
        client.table("clip")
        .select("start_ms, duration_ms, beat_index")
        .eq("track_id", track["id"])
        .execute()
        .data
    ) or []
    base_end = max(
        (int(c["start_ms"]) + int(c.get("duration_ms") or 0) for c in existing), default=0
    )
    existing_by_beat: dict[int, tuple[int, int]] = {
        int(c["beat_index"]): (int(c["start_ms"]), int(c.get("duration_ms") or per))
        for c in existing
        if c.get("beat_index") is not None
    }

    placements = _resolve_placements(ordered, existing_by_beat, per, base_end, at_ms=at_ms, mode=mode)
    if not placements:
        raise ValueError("no placements resolved")

    first = placements[0]
    first_slot = _ensure_slot(
        client,
        run_id=run_id,
        track="visual",
        beat_index=int(first["beat"]),
        start_ms=int(first["start_ms"]),
        end_ms=int(first["start_ms"]) + int(first["duration_ms"]),
    )
    artifact = _artifact_from_item(client, run_id=run_id, slot_id=first_slot["id"], item=item, reuse=reuse)

    clip_ids: list[str] = []
    for p in placements:
        beat = int(p["beat"])
        start_ms = int(p["start_ms"])
        dur = int(p["duration_ms"])
        slot = _ensure_slot(
            client, run_id=run_id, track="visual", beat_index=beat, start_ms=start_ms, end_ms=start_ms + dur
        )
        client.table("slot").update({"current_artifact_id": artifact["id"]}).eq("id", slot["id"]).execute()
        clip = create_clip(
            client,
            track_id=track["id"],
            artifact_id=artifact["id"],
            start_ms=start_ms,
            duration_ms=dur,
            beat_index=beat,
            effects=effects,
            attributes=attributes,
        )
        clip_ids.append(clip["id"])

    new_end = max((p["start_ms"] + p["duration_ms"] for p in placements), default=base_end)
    if new_end > int(timeline.get("duration_ms") or 0):
        client.table("timeline").update({"duration_ms": new_end}).eq("id", timeline["id"]).execute()

    return {
        "run_id": run_id,
        "timeline_id": timeline["id"],
        "track_id": track["id"],
        "artifact_id": artifact["id"],
        "storage_key": item["storage_key"],
        "beats": ordered,
        "clip_ids": clip_ids,
        "per_clip_ms": per,
        "start_ms": int(placements[0]["start_ms"]),
        "end_ms": int(new_end),
    }


def place_library_video(
    client: Client,
    *,
    run_id: str,
    item: dict,
    beats: list[int] | None = None,
    at_ms: int | None = None,
    duration_ms: int | None = None,
    aspect: str = "9:16",
    attributes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Place a library video into ``run_id`` at an ARBITRARY beat / time.

    ``item`` is an ``asset_library_item`` row. Placement comes from the human /
    script — NOT a fixed beat:
      * ``beats=[0]`` → the demo plays in the HOOK (overlays that beat).
      * ``beats=[0, 3, 5]`` → REPEAT for emphasis / MULTIPLE positions.
      * ``at_ms=12000`` → drop it at an exact timeline position.
    The same artifact is reused across every placement, so repetition is FREE
    (napkin cost rule — no re-generation).
    """
    if item.get("kind") not in (None, "video"):
        raise ValueError(f"library item is kind={item.get('kind')!r}, expected video")
    return _place_media(
        client,
        run_id=run_id,
        item=item,
        beats=beats or [],
        at_ms=at_ms,
        duration_ms=duration_ms,
        aspect=aspect,
        effects=None,
        attributes=attributes,
        reuse="library_placement",
        mode="at_beat",
    )


def _is_image_item(item: dict) -> bool:
    return item.get("kind") == "image" or str(item.get("mime") or "").startswith("image/")


def place_social_proof(
    client: Client,
    *,
    run_id: str,
    item: dict,
    beat: int = 0,
    duration_ms: int | None = None,
    aspect: str = "9:16",
    quote: str | None = None,
    attributes: dict[str, Any] | None = None,
    mode: str = "append",
) -> dict[str, Any]:
    """Place a real social-proof asset as a proof scene (T6).

    Treatment is chosen per asset type (Q4 "mix per script"), reusing the
    renderer's existing proof treatment:
      * image (review screenshot/rating) → clip carries the ``proof-frame``
        effect → renders as a framed review CARD.
      * video (testimonial) → plain clip → renders FULL-BLEED (OffthreadVideo).

    ``quote`` optionally adds an overlay text line over the proof (the review
    text is usually already baked into the image, so it defaults to none).
    """
    is_image = _is_image_item(item)
    effects = [{"kind": "proof-frame"}] if is_image else None
    res = _place_media(
        client,
        run_id=run_id,
        item=item,
        beats=[beat],
        duration_ms=duration_ms,
        aspect=aspect,
        effects=effects,
        attributes=attributes,
        reuse="social_proof_placement",
        mode=mode,
    )
    res["treatment"] = "card (proof-frame)" if is_image else "full-bleed video"
    res["proof_frame"] = is_image

    if quote:
        overlay_track = ensure_track(
            client, timeline_id=res["timeline_id"], kind="overlay", label="Proof Text", ord=5, z_index=5
        )
        quote_clip = create_clip(
            client,
            track_id=overlay_track["id"],
            artifact_id=None,
            start_ms=res["start_ms"],  # align the quote with the proof clip
            duration_ms=res["per_clip_ms"],
            beat_index=int(beat),
            text_content=quote,
            attributes=attributes,
        )
        res["quote_clip_id"] = quote_clip["id"]
    return res


def place_demo_broll(client: Client, *, run_id: str, item: dict, beat: int, reuse: str = "demo_broll") -> dict[str, Any]:
    """NAPKIN §REAL ASSETS: the EXPLANATION scene shows the brand's REAL social
    proof / demo as B-roll (자료화면). Points the existing video clip + visual slot
    at ``beat`` to a fresh artifact mirroring ``item`` (a social-proof / demo
    asset_library_item), so that scene renders the real footage full-bleed.

    Overwrites in place (no extra clip) so there is exactly one image per beat
    (no z-fighting). Returns the new artifact id + the affected clip id.
    """
    timeline = (
        client.table("timeline").select("id").eq("run_id", run_id).limit(1).execute().data
    ) or []
    if not timeline:
        raise ValueError("no timeline for run")
    video_tracks = (
        client.table("timeline_track").select("id").eq("timeline_id", timeline[0]["id"]).eq("kind", "video").execute().data
    ) or []
    if not video_tracks:
        raise ValueError("no video track")
    clip_rows = (
        client.table("clip").select("id, start_ms, duration_ms").eq("track_id", video_tracks[0]["id"]).eq("beat_index", int(beat)).limit(1).execute().data
    ) or []
    if not clip_rows:
        raise ValueError(f"no video clip at beat {beat}")
    clip = clip_rows[0]
    slot = _ensure_slot(
        client, run_id=run_id, track="visual", beat_index=int(beat),
        start_ms=int(clip["start_ms"]), end_ms=int(clip["start_ms"]) + int(clip.get("duration_ms") or 0),
    )
    artifact = _artifact_from_item(client, run_id=run_id, slot_id=slot["id"], item=item, reuse=reuse)
    client.table("slot").update({"current_artifact_id": artifact["id"]}).eq("id", slot["id"]).execute()
    client.table("clip").update({"artifact_id": artifact["id"], "updated_at": "now()"}).eq("id", clip["id"]).execute()
    return {"run_id": run_id, "beat": int(beat), "artifact_id": artifact["id"], "clip_id": clip["id"], "storage_key": item["storage_key"]}


def place_sticker(
    client: Client,
    *,
    run_id: str,
    item: dict,
    beats: list[int],
    transforms: dict | None = None,
    effects: list | None = None,
    reuse: str = "sticker_placement",
) -> dict[str, Any]:
    """NAPKIN §STICKERS/SPECIAL FX (화룡점정): stick an FX/sticker overlay (emoji ·
    arrow · zoom-burst · badge · highlight) on top of the scene, timed per beat.
    The sticker is its OWN layer at z-index 15 — ABOVE image(0)/overlay(10),
    BELOW caption(20) — so it never fights the base image or the captions.

    ``item`` is an asset_library_item (category:effect) holding a (usually
    transparent) overlay PNG/video. One sticker clip is created per beat, aligned
    to that beat's existing video window. ``transforms`` (scale/x/y) optionally
    shrink + position it; a full-frame transparent PNG needs none.
    """
    if not beats:
        raise ValueError("beats required")
    timeline = (
        client.table("timeline").select("id").eq("run_id", run_id).limit(1).execute().data
    ) or []
    if not timeline:
        raise ValueError("no timeline for run")
    timeline_id = timeline[0]["id"]
    video_tracks = (
        client.table("timeline_track").select("id").eq("timeline_id", timeline_id).eq("kind", "video").execute().data
    ) or []
    if not video_tracks:
        raise ValueError("no video track")
    # Window per beat from the existing video clips (sticker rides the scene timing).
    clips = (
        client.table("clip").select("beat_index, start_ms, duration_ms").eq("track_id", video_tracks[0]["id"]).execute().data
    ) or []
    win = {int(c["beat_index"]): (int(c["start_ms"]), int(c.get("duration_ms") or 0)) for c in clips if c.get("beat_index") is not None}

    sticker_track = ensure_track(
        client, timeline_id=timeline_id, kind="sticker", label="Stickers", ord=15, z_index=15
    )
    ordered = [int(b) for b in sorted(set(beats)) if int(b) in win]
    if not ordered:
        raise ValueError("none of the requested beats exist on the video track")
    first = ordered[0]
    # The slot is just the artifact container; 'effect' is the allowed slot track
    # for stickers/FX (the timeline TRACK is kind='sticker', z=15).
    slot = _ensure_slot(
        client, run_id=run_id, track="effect", beat_index=first,
        start_ms=win[first][0], end_ms=win[first][0] + win[first][1],
    )
    artifact = _artifact_from_item(client, run_id=run_id, slot_id=slot["id"], item=item, reuse=reuse)
    clip_ids = []
    for b in ordered:
        start_ms, dur = win[b]
        clip = create_clip(
            client, track_id=sticker_track["id"], artifact_id=artifact["id"],
            start_ms=start_ms, duration_ms=dur, beat_index=b,
            transforms=transforms, effects=effects,
        )
        clip_ids.append(clip["id"])
    return {"run_id": run_id, "track_id": sticker_track["id"], "artifact_id": artifact["id"], "beats": ordered, "clip_ids": clip_ids}
