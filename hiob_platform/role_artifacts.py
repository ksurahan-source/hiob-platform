"""Materialize agent role outputs as first-class artifacts.

Without this module, every non-media role (PD, researcher, marketer,
art_director, sound_designer, editor, QA) leaves its work buried inside
`agent_call.output` JSON. The editor never sees it as an asset.

This module takes a role output and produces:
  * one or more `artifact` rows (storage='inline', text_content set),
    with `role_code` + `category` filled so the AssetBin can group them.
  * one matching `asset_library_item` row per artifact so the same
    grouping works across runs.

`slot_id` is NULL for meta artifacts — this is allowed after migration
0007. Beat-aligned text (script lines, captions) still flows through
the existing slot-based path in `_write_script_slots_and_artifacts`.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable

from supabase import Client

from hiob_platform.storage import register_asset_library_item


# Stable category vocabulary the AssetBin renders as sections.
CATEGORY_BRIEF      = "brief"
CATEGORY_RESEARCH   = "research"
CATEGORY_HOOK       = "hook"
CATEGORY_SCRIPT     = "script"
CATEGORY_STYLE      = "style"
CATEGORY_VISUAL     = "visual"
CATEGORY_VOICE      = "voice"
CATEGORY_MUSIC      = "music"
CATEGORY_SFX        = "sfx"
CATEGORY_CUTS       = "cuts"
CATEGORY_NOTES      = "notes"
CATEGORY_QA         = "qa"
CATEGORY_TEAM       = "team"


@dataclass(frozen=True)
class RoleArtifact:
    """A single artifact extracted from a role output."""
    text: str
    title: str
    category: str
    kind: str = "text"           # 'text'|'template' (no media here)
    attributes: dict[str, Any] | None = None


# ----------------------------------------------------------------
# Per-role extractors
# Each takes the parsed JSON output and yields RoleArtifact items.
# Keep them defensive: missing keys are normal, type drift is normal.
# ----------------------------------------------------------------

def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except Exception:
        return str(value)


def _truncate_title(text: str, limit: int = 80) -> str:
    one_line = " ".join(text.split())
    if len(one_line) <= limit:
        return one_line
    return one_line[: limit - 1] + "…"


def extract_pd(output: dict) -> list[RoleArtifact]:
    if not output:
        return []
    blob_lines: list[str] = []
    for key in ("goal", "audience", "tone", "format", "north_star_metric"):
        if output.get(key):
            blob_lines.append(f"{key}: {_stringify(output[key])}")
    constraints = _as_list(output.get("constraints"))
    if constraints:
        blob_lines.append("constraints:")
        for c in constraints:
            blob_lines.append(f"  - {_stringify(c)}")
    if not blob_lines:
        blob_lines.append(_stringify(output))
    text = "\n".join(blob_lines)
    return [RoleArtifact(text=text, title=f"Brief: {_truncate_title(_stringify(output.get('goal') or 'brief'))}", category=CATEGORY_BRIEF)]


def extract_researcher(output: dict) -> list[RoleArtifact]:
    if not output:
        return []
    out: list[RoleArtifact] = []
    for fact in _as_list(output.get("key_facts")):
        text = _stringify(fact)
        if text:
            out.append(RoleArtifact(text=text, title=f"Fact: {_truncate_title(text)}", category=CATEGORY_RESEARCH))
    for ref in _as_list(output.get("references")):
        text = _stringify(ref)
        if text:
            out.append(RoleArtifact(text=text, title=f"Reference: {_truncate_title(text)}", category=CATEGORY_RESEARCH))
    for hook in _as_list(output.get("hooks")):
        text = _stringify(hook)
        if text:
            out.append(RoleArtifact(text=text, title=f"Hook candidate: {_truncate_title(text)}", category=CATEGORY_HOOK))
    for risk in _as_list(output.get("risks")):
        text = _stringify(risk)
        if text:
            out.append(RoleArtifact(text=text, title=f"Risk: {_truncate_title(text)}", category=CATEGORY_NOTES))
    return out


def extract_marketer(output: dict) -> list[RoleArtifact]:
    if not output:
        return []
    out: list[RoleArtifact] = []
    if output.get("hook"):
        out.append(RoleArtifact(text=_stringify(output["hook"]), title=f"Hook: {_truncate_title(_stringify(output['hook']))}", category=CATEGORY_HOOK))
    if output.get("value_prop_one_liner"):
        out.append(RoleArtifact(text=_stringify(output["value_prop_one_liner"]), title=f"Value prop: {_truncate_title(_stringify(output['value_prop_one_liner']))}", category=CATEGORY_HOOK))
    if output.get("cta"):
        out.append(RoleArtifact(text=_stringify(output["cta"]), title=f"CTA: {_truncate_title(_stringify(output['cta']))}", category=CATEGORY_HOOK))
    arc = _as_list(output.get("arc"))
    if arc:
        text = "Arc:\n" + "\n".join(f"  {i+1}. {_stringify(stage)}" for i, stage in enumerate(arc))
        out.append(RoleArtifact(text=text, title="Emotional arc", category=CATEGORY_NOTES))
    return out


def extract_scriptwriter(output: dict) -> list[RoleArtifact]:
    """Script beats already become first-class artifacts in
    `_write_script_slots_and_artifacts` (with role_code='scriptwriter' and
    category='script'), so we deliberately do NOT re-emit them here — that
    would cause double-counting in the AssetBin (12 items for n_beats=6).

    We still emit any non-beat scriptwriter outputs (notes, alternates) so
    nothing the writer produced is silently dropped.
    """
    if not output:
        return []
    out: list[RoleArtifact] = []
    notes = _as_list(output.get("notes"))
    if notes:
        out.append(RoleArtifact(
            text="\n".join(_stringify(n) for n in notes),
            title="Scriptwriter notes",
            category=CATEGORY_NOTES,
        ))
    for i, alt in enumerate(_as_list(output.get("alternates"))):
        text = _stringify(alt)
        if text:
            out.append(RoleArtifact(
                text=text,
                title=f"Alt line {i}: {_truncate_title(text)}",
                category=CATEGORY_SCRIPT,
                attributes={"beat_index": i, "is_alternate": True},
            ))
    return out


def extract_art_director(output: dict) -> list[RoleArtifact]:
    if not output:
        return []
    out: list[RoleArtifact] = []
    if output.get("style_bible"):
        out.append(RoleArtifact(text=_stringify(output["style_bible"]), title="Style bible", category=CATEGORY_STYLE, kind="template"))
    for i, prompt in enumerate(_as_list(output.get("prompts"))):
        text = _stringify(prompt)
        if text:
            out.append(RoleArtifact(
                text=text,
                title=f"Visual prompt {i}: {_truncate_title(text)}",
                category=CATEGORY_VISUAL,
                attributes={"beat_index": i, "is_prompt": True},
            ))
    return out


def extract_sound_designer(output: dict) -> list[RoleArtifact]:
    if not output:
        return []
    out: list[RoleArtifact] = []
    music_lines: list[str] = []
    if output.get("music_vibe"):
        music_lines.append(f"vibe: {_stringify(output['music_vibe'])}")
    if output.get("music_bpm"):
        music_lines.append(f"bpm: {_stringify(output['music_bpm'])}")
    if music_lines:
        out.append(RoleArtifact(text="\n".join(music_lines), title="Music plan", category=CATEGORY_MUSIC))
    for cue in _as_list(output.get("sfx_cues")):
        if not isinstance(cue, dict):
            continue
        beat = cue.get("beat")
        cue_text = _stringify(cue.get("cue"))
        if not cue_text:
            continue
        title = f"SFX @ beat {beat}: {_truncate_title(cue_text)}" if beat is not None else f"SFX: {_truncate_title(cue_text)}"
        out.append(RoleArtifact(
            text=cue_text,
            title=title,
            category=CATEGORY_SFX,
            attributes={"beat_index": beat, "is_cue": True},
        ))
    return out


def extract_editor(output: dict) -> list[RoleArtifact]:
    if not output:
        return []
    out: list[RoleArtifact] = []
    cuts = _as_list(output.get("cuts"))
    if cuts:
        lines = []
        for cut in cuts:
            if not isinstance(cut, dict):
                continue
            beat = cut.get("beat")
            trans = cut.get("transition")
            dur = cut.get("duration_ms")
            lines.append(f"beat {beat}: {trans}{f' ({dur}ms)' if dur else ''}")
        if lines:
            out.append(RoleArtifact(text="\n".join(lines), title="Cut sheet", category=CATEGORY_CUTS))
    notes = _as_list(output.get("notes"))
    if notes:
        out.append(RoleArtifact(text="\n".join(_stringify(n) for n in notes), title="Editor notes", category=CATEGORY_NOTES))
    return out


def extract_qa(output: dict) -> list[RoleArtifact]:
    if not output:
        return []
    body = []
    if "score" in output:
        body.append(f"score: {output['score']}")
    if "passed" in output:
        body.append(f"passed: {output['passed']}")
    for label in ("blockers", "warnings"):
        items = _as_list(output.get(label))
        if items:
            body.append(f"{label}:")
            for item in items:
                body.append(f"  - {_stringify(item)}")
    return [RoleArtifact(
        text="\n".join(body) if body else _stringify(output),
        title=f"QA report (score {output.get('score', '?')})",
        category=CATEGORY_QA,
    )]


def extract_team_leader(output: dict) -> list[RoleArtifact]:
    if not output:
        return []
    lines = []
    if output.get("reasoning"):
        lines.append(f"reasoning: {_stringify(output['reasoning'])}")
    if output.get("selected_roles"):
        lines.append(f"selected_roles: {_stringify(output['selected_roles'])}")
    if output.get("expected_artifacts"):
        lines.append("expected_artifacts:")
        for item in _as_list(output["expected_artifacts"]):
            lines.append(f"  - {_stringify(item)}")
    if output.get("risks"):
        lines.append("risks:")
        for risk in _as_list(output["risks"]):
            lines.append(f"  - {_stringify(risk)}")
    return [RoleArtifact(text="\n".join(lines) or _stringify(output), title="Team formation", category=CATEGORY_TEAM)]


EXTRACTORS = {
    "pd":             extract_pd,
    "researcher":     extract_researcher,
    "marketer":       extract_marketer,
    "scriptwriter":   extract_scriptwriter,
    "art_director":   extract_art_director,
    "sound_designer": extract_sound_designer,
    "editor":         extract_editor,
    "qa":             extract_qa,
    "team_leader":    extract_team_leader,
}


# ----------------------------------------------------------------
# DB writers
# ----------------------------------------------------------------

def _ensure_meta_slot(client: Client, run_id: str, role_code: str) -> str | None:
    """Get-or-create a per-(run,role) synthetic slot on the 'effect' track.

    Needed because the live DB enforces a check constraint
    `artifact_slot_or_composite_check` that requires slot_id NOT NULL, even
    after migration 0007 dropped the column-level NOT NULL. The 'effect'
    track allows beat_index=NULL and accepts arbitrary slots, so we use it
    as a sink for meta artifacts (one slot per role to keep grouping clean).
    """
    try:
        existing = (
            client.table("slot")
            .select("id")
            .eq("run_id", run_id)
            .eq("track", "effect")
            .contains("attributes", {"kind": "role_meta", "role_code": role_code})
            .limit(1)
            .execute()
        )
        if existing.data:
            return existing.data[0]["id"]
    except Exception:
        # `attributes` filter may not be supported on older PostgREST; fall
        # through to insert and rely on it being cheap.
        pass

    try:
        res = (
            client.table("slot")
            .insert(
                {
                    "run_id": run_id,
                    "track": "effect",
                    "beat_index": None,
                    "start_ms": 0,
                    "end_ms": 1,
                    "attributes": {"kind": "role_meta", "role_code": role_code},
                }
            )
            .execute()
        )
        return res.data[0]["id"] if res.data else None
    except Exception:
        return None


def _backfill_role_category(
    client: Client,
    artifact: dict,
    *,
    role_code: str,
    category: str,
    attrs: dict[str, Any],
) -> dict:
    """When we reuse a pre-existing artifact (same sha256) that has no
    role_code / category yet, stamp it now so the AssetBin can group it."""
    needs_role = not artifact.get("role_code")
    needs_cat = not artifact.get("category")
    existing_attrs = artifact.get("attributes") or {}
    merged_attrs = {**existing_attrs, **attrs}
    if not (needs_role or needs_cat or merged_attrs != existing_attrs):
        return artifact

    update_payload: dict[str, Any] = {"attributes": merged_attrs}
    if needs_role:
        update_payload["role_code"] = role_code
    if needs_cat:
        update_payload["category"] = category
    try:
        res = (
            client.table("artifact")
            .update(update_payload)
            .eq("id", artifact["id"])
            .execute()
        )
        if res.data:
            return res.data[0]
    except Exception:
        # role_code/category columns may be absent on a pre-0007 DB; fall
        # back to attributes-only.
        try:
            client.table("artifact").update({"attributes": merged_attrs}).eq("id", artifact["id"]).execute()
        except Exception:
            pass
    return {**artifact, **update_payload}


def _insert_text_artifact(
    client: Client,
    *,
    run_id: str,
    role_code: str,
    category: str,
    kind: str,
    text: str,
    title: str,
    source_call_id: str | None,
    attributes: dict[str, Any] | None,
) -> dict | None:
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    storage_key = f"inline:role/{role_code}/{sha[:24]}"
    attrs = {
        "role_code": role_code,
        "category": category,
        "title": title,
        "source_call_id": source_call_id,
    }
    if attributes:
        attrs.update(attributes)

    # Idempotency: (storage, storage_key) is unique on asset_library_item;
    # for the artifact table, sha256 is indexed but not unique, so we
    # de-dupe ourselves on (run_id, sha256) to avoid double-registering
    # the same text on retries.
    #
    # NOTE: role_code / category were added in migration 0007.  On a DB
    # that hasn't had 0007 applied yet the SELECT below would raise a
    # PostgreSQL column-not-found error, which propagates up through
    # materialize_role_outputs() and silently zeroes out the AssetBin.
    # We guard with a two-attempt pattern: full columns first, pre-0007
    # columns as fallback.
    _full_cols = (
        "id, storage, storage_key, sha256, mime, bytes, duration_ms, "
        "width, height, preview_text, attributes, role_code, category"
    )
    _base_cols = (
        "id, storage, storage_key, sha256, mime, bytes, duration_ms, "
        "width, height, preview_text, attributes"
    )
    try:
        existing = (
            client.table("artifact")
            .select(_full_cols)
            .eq("run_id", run_id)
            .eq("sha256", sha)
            .limit(1)
            .execute()
        )
    except Exception:
        # role_code / category columns absent (pre-0007 schema); fall back
        # to the base column set.  _backfill_role_category will still run
        # but will skip the DB UPDATE for missing columns gracefully.
        try:
            existing = (
                client.table("artifact")
                .select(_base_cols)
                .eq("run_id", run_id)
                .eq("sha256", sha)
                .limit(1)
                .execute()
            )
        except Exception:
            # Network / auth error — treat as "not found" and attempt insert.
            class _Empty:
                data: list = []
            existing = _Empty()
    if existing.data:
        artifact = _backfill_role_category(
            client, existing.data[0], role_code=role_code, category=category, attrs=attrs
        )
    else:
        base_payload = {
            "run_id": run_id,
            "version": 1,
            "source": "ai",
            "storage": "inline",
            "storage_key": storage_key,
            "sha256": sha,
            "mime": "text/plain",
            "text_content": text,
            "bytes": len(text.encode("utf-8")),
            "preview_text": text[:200],
            "role_code": role_code,
            "category": category,
            "attributes": attrs,
        }
        artifact = _insert_artifact_resilient(
            client, run_id=run_id, role_code=role_code, payload=base_payload, attrs=attrs
        )
        if artifact is None:
            return None

    register_asset_library_item(
        client,
        run_id=run_id,
        artifact=artifact,
        kind=kind,
        source="ai",
        title=title,
        preview_text=text[:200],
        attributes={**attrs, "kind": kind},
    )
    # Also stamp library row with role_code/category if columns exist.
    try:
        client.table("asset_library_item").update(
            {"role_code": role_code, "category": category}
        ).eq("artifact_id", artifact["id"]).execute()
    except Exception:
        pass
    return artifact


def _insert_artifact_resilient(
    client: Client,
    *,
    run_id: str,
    role_code: str,
    payload: dict[str, Any],
    attrs: dict[str, Any],
) -> dict | None:
    """Insert with progressive fallbacks for the three known schema drifts:

      1. The live DB enforces `artifact_slot_or_composite_check` even when
         migration 0007 made `slot_id` nullable at the column level. We
         provision a synthetic meta slot on the 'effect' track and retry.
      2. `role_code` / `category` columns may not exist on a pre-0007 DB.
         Drop them and stuff the same info into `attributes`.
      3. As a last resort, log + skip rather than crash the orchestrator.
    """
    # Pass 1: best-case (post-migration, no constraint quirks).
    first = {**payload, "slot_id": None}
    try:
        return client.table("artifact").insert(first).execute().data[0]
    except Exception as exc_first:
        msg_first = str(exc_first)

    # Pass 2: provision a meta slot and retry. Triggered when the
    # `artifact_slot_or_composite_check` constraint rejects slot_id=NULL.
    meta_slot_id = _ensure_meta_slot(client, run_id, role_code)
    if meta_slot_id:
        with_slot = {**payload, "slot_id": meta_slot_id}
        try:
            return client.table("artifact").insert(with_slot).execute().data[0]
        except Exception as exc_slot:
            msg_first = str(exc_slot)

    # Pass 3: legacy columns missing — strip role_code/category, keep them
    # in attributes so the editor can still see what role produced this.
    legacy = {k: v for k, v in payload.items() if k not in ("role_code", "category")}
    legacy["attributes"] = {**attrs, "fallback_columns": True}
    if meta_slot_id:
        legacy["slot_id"] = meta_slot_id
    else:
        # Still no slot; make one final attempt to provision a meta slot.
        # This handles the case where _ensure_meta_slot() failed on Pass 2
        # but might succeed on a retry (e.g. a transient DB hiccup).
        meta_slot_id = _ensure_meta_slot(client, run_id, role_code)
        legacy["slot_id"] = meta_slot_id  # None if still failing — last resort
    try:
        return client.table("artifact").insert(legacy).execute().data[0]
    except Exception:
        # Truly stuck; emit one warning so an operator can see why no role
        # artifacts are appearing in the AssetBin, then skip.
        try:
            import sys
            print(
                f"[role_artifacts] insert failed for run={run_id} role={role_code}: {msg_first}",
                file=sys.stderr,
            )
        except Exception:
            pass
        return None


def materialize_role_outputs(
    client: Client,
    *,
    run_id: str,
    role_code: str,
    call_id: str | None,
    output: dict | None,
) -> list[dict]:
    """Run the extractor for `role_code` and persist every artifact.

    Returns the list of artifact rows actually written. Never raises;
    failure to materialize one artifact does not block the team run.
    """
    if not output:
        return []
    extractor = EXTRACTORS.get(role_code)
    if extractor is None:
        return []
    try:
        items = list(extractor(output))
    except Exception:
        return []
    artifacts: list[dict] = []
    for item in items:
        if not item.text:
            continue
        try:
            art = _insert_text_artifact(
                client,
                run_id=run_id,
                role_code=role_code,
                category=item.category,
                kind=item.kind,
                text=item.text,
                title=item.title,
                source_call_id=call_id,
                attributes=item.attributes,
            )
            if art:
                artifacts.append(art)
        except Exception:
            continue
    return artifacts


def expected_artifacts_from_team_leader(team_leader_output: dict | None) -> list[dict]:
    """Normalize the team leader's expected_artifacts list to a comparable shape."""
    if not team_leader_output:
        return []
    raw = team_leader_output.get("expected_artifacts")
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip()
        category = str(item.get("category") or "").strip()
        count = item.get("count")
        try:
            count_int = int(count) if count is not None else 1
        except (TypeError, ValueError):
            count_int = 1
        if not role and not category:
            continue
        out.append({
            "role": role or None,
            "category": category or None,
            "count": max(1, count_int),
        })
    return out


def diff_expected_vs_actual(
    expected: Iterable[dict],
    actual_artifacts: Iterable[dict],
) -> list[dict]:
    """Return the list of expected entries that are short of `count`."""
    actuals_by_role: dict[str, int] = {}
    actuals_by_category: dict[str, int] = {}
    for art in actual_artifacts:
        role = art.get("role_code") or (art.get("attributes") or {}).get("role_code")
        cat = art.get("category") or (art.get("attributes") or {}).get("category")
        if role:
            actuals_by_role[role] = actuals_by_role.get(role, 0) + 1
        if cat:
            actuals_by_category[cat] = actuals_by_category.get(cat, 0) + 1
    gaps: list[dict] = []
    for exp in expected:
        want = exp["count"]
        have = 0
        if exp.get("role"):
            have = max(have, actuals_by_role.get(exp["role"], 0))
        if exp.get("category"):
            have = max(have, actuals_by_category.get(exp["category"], 0))
        if have < want:
            gaps.append({**exp, "have": have, "missing": want - have})
    return gaps
