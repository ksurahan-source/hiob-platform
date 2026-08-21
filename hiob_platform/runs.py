"""Run + slot + artifact + span write helpers (service-role only)."""
from __future__ import annotations

import os
from typing import Any, Mapping

from supabase import Client

APPROVED_SCRIPT_STATUSES = frozenset({"approved", "queued", "produced"})
REQUIRED_PRODUCTION_WORK_KINDS = frozenset({"visual", "voiceover", "music", "sfx"})
OPTIONAL_PRODUCTION_WORK_KINDS = frozenset({"caption", "title_style"})
PRODUCTION_WORK_KINDS = REQUIRED_PRODUCTION_WORK_KINDS | OPTIONAL_PRODUCTION_WORK_KINDS
_DATABASE_NOW = "now()"

# SEC-C2: mirror Studio lib/editorGate.js env contract.
_GATE_OFF = frozenset({"0", "false", "off", "no"})
_LEGACY_ON = frozenset({"1", "true", "yes", "on"})


def get_run_script_status(client: Client, run_id: str) -> str | None:
    rows = (
        client.table("run")
        .select("id, script_status")
        .eq("id", run_id)
        .limit(1)
        .execute()
        .data
    )
    if not rows:
        return None
    value = rows[0].get("script_status")
    return str(value) if value is not None else None


def set_run_script_status(client: Client, run_id: str, script_status: str) -> dict:
    res = client.table("run").update({"script_status": script_status}).eq("id", run_id).execute()
    return res.data[0] if res.data else {}


def assert_run_script_gate(
    client: Client,
    run_id: str,
    *,
    operation: str,
    allowed: set[str] | frozenset[str] = APPROVED_SCRIPT_STATUSES,
) -> str:
    status = get_run_script_status(client, run_id)
    if status not in allowed:
        allowed_text = ", ".join(sorted(allowed))
        raise RuntimeError(
            f"{operation} blocked until script approval "
            f"(run.script_status={status or 'null'}, allowed={allowed_text})"
        )
    return status


def _env_flag(env: Mapping[str, str] | None, key: str) -> str:
    src = env if env is not None else os.environ
    return str(src.get(key) or "").strip().lower()


def is_editor_gate_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Default ON. Off only when HIOB_EDITOR_GATE is 0|false|off|no."""
    return _env_flag(env, "HIOB_EDITOR_GATE") not in _GATE_OFF


def is_editor_gate_legacy_allow(env: Mapping[str, str] | None = None) -> bool:
    """Default OFF. Soft-allow missing approval only when LEGACY_ALLOW truthy."""
    return _env_flag(env, "HIOB_EDITOR_GATE_LEGACY_ALLOW") in _LEGACY_ON


def assert_editor_approved(
    client: Client,
    run_id: str,
    *,
    operation: str = "compose",
    env: Mapping[str, str] | None = None,
) -> str:
    """SEC-C2: require run.attributes.editor_approved_at (Studio L2 parity on Modal).

    Returns:
      * ISO timestamp when approved
      * ``\"bypassed\"`` when HIOB_EDITOR_GATE off
      * ``\"legacy\"`` when LEGACY_ALLOW and missing at

    Raises RuntimeError with EDITOR_APPROVAL_REQUIRED when blocked.
    """
    if not is_editor_gate_enabled(env):
        return "bypassed"
    rows = (
        client.table("run")
        .select("id, attributes")
        .eq("id", run_id)
        .limit(1)
        .execute()
        .data
    )
    attrs = (rows[0].get("attributes") if rows else None) or {}
    if not isinstance(attrs, dict):
        attrs = {}
    at = attrs.get("editor_approved_at")
    if at:
        return str(at)
    if is_editor_gate_legacy_allow(env):
        return "legacy"
    raise RuntimeError(
        f"{operation} blocked: editor approval required (EDITOR_APPROVAL_REQUIRED)"
    )


def update_production_job(
    client: Client,
    job_id: str | None,
    *,
    status: str,
    span_id: str | None = None,
    modal_call_id: str | None = None,
    error: dict | None = None,
    attributes: dict | None = None,
) -> dict:
    if not job_id:
        return {}
    payload: dict[str, Any] = {
        "status": status,
        "updated_at": _DATABASE_NOW,
    }
    if status == "running":
        payload["started_at"] = _DATABASE_NOW
    if status in {"succeeded", "failed", "cancelled", "skipped"}:
        payload["ended_at"] = _DATABASE_NOW
    if span_id is not None:
        payload["span_id"] = span_id
    if modal_call_id is not None:
        payload["modal_call_id"] = modal_call_id
    if error is not None:
        payload["error"] = error
    if attributes is not None:
        payload["attributes"] = attributes
    res = client.table("production_jobs").update(payload).eq("id", job_id).execute()
    return res.data[0] if res.data else {}


def create_production_job(
    client: Client,
    *,
    run_id: str,
    kind: str,
    script_candidate_id: str | None = None,
    target: dict | None = None,
) -> dict:
    payload: dict[str, Any] = {
        "run_id": run_id,
        "kind": kind,
        "status": "queued",
        "target": target or {},
    }
    if script_candidate_id:
        payload["script_candidate_id"] = script_candidate_id
    res = client.table("production_jobs").insert(payload).execute()
    return res.data[0]


def render_job_patch_after_compose(
    result: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build render_jobs update after compose_and_render_v2.

    Terminal ``done`` is allowed only when a non-empty playable URL is present.
    Dispatch-only compose results stay ``processing`` (WS06 bridge fills URL later).
    """
    result = result or {}
    output_url = result.get("output_url") or result.get("mp4_url")
    url = str(output_url or "").strip()
    duration_s = result.get("duration_s")
    if url:
        patch: dict[str, Any] = {
            "status": "done",
            "output_url": url,
            "completed_at": _DATABASE_NOW,
        }
        if duration_s is not None:
            patch["duration_s"] = duration_s
        return patch
    patch = {
        "status": "processing",
        "output_url": None,
        "metadata": {
            "awaiting_output_url": True,
            "snapshot_id": result.get("snapshot_id"),
            "render_dispatched": result.get("render_dispatched"),
        },
    }
    if duration_s is not None:
        patch["duration_s"] = duration_s
    return patch


def _as_nonneg_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _is_skip_reason_soft_fail(skipped: Any) -> bool:
    """True only for soft-fail skip *reasons* (empty pool etc.).

    Worker batch returns use ``skipped`` as:
      - str reason  → soft-fail (empty_music_pool, no_cues, ...)
      - int count   → already-present beat count (NOT a failure by itself)
      - list[dict]  → already_present details (NOT a failure by itself)
      - True        → boolean skip flag (fail unless other positive work signals)
    """
    if skipped is None or skipped is False:
        return False
    if skipped is True:
        return True
    if isinstance(skipped, str):
        return bool(skipped.strip())
    if isinstance(skipped, (int, float)):
        return False
    if isinstance(skipped, (list, tuple)):
        return False
    return False


def is_visual_worker_success(out: Any) -> bool:
    """Hard predicate for visual_run soft-error dicts.

    Workers that return ``{"error": "...", "visuals": 0}`` without raising used to
    mark production_jobs succeeded and green the run. That is forbidden.

    Reuse-only batches (``visuals=0, reused=N``) are success — images already bound.
    """
    if not isinstance(out, dict):
        return False
    if out.get("error"):
        return False
    if "visuals" in out or "reused" in out:
        visuals = _as_nonneg_int(out.get("visuals"))
        reused = _as_nonneg_int(out.get("reused"))
        if visuals + reused <= 0:
            return False
    return True


def _first_work_count(out: dict[str, Any]) -> Any:
    for key in ("created", "voiceovers", "visuals"):
        if out.get(key) is not None:
            return out[key]
    return 0


def _failed_batch_without_output(out: dict[str, Any]) -> bool:
    failed = out.get("failed")
    return bool(isinstance(failed, list) and failed) and _as_nonneg_int(
        _first_work_count(out)
    ) <= 0


def _visual_result_is_success(out: dict[str, Any]) -> bool:
    if "visuals" not in out and "reused" not in out:
        return True
    return is_visual_worker_success(out)


def _voice_result_is_success(out: dict[str, Any]) -> bool:
    skipped = out.get("skipped")
    has_batch_keys = (
        "voiceovers" in out
        or isinstance(skipped, (int, float, list, tuple))
        or "failed" in out
    )
    if not has_batch_keys:
        return True
    skipped_n = len(skipped) if isinstance(skipped, (list, tuple)) else _as_nonneg_int(skipped)
    return (_as_nonneg_int(out.get("voiceovers")) + skipped_n) > 0


def _sfx_result_is_success(out: dict[str, Any]) -> bool:
    if not any(key in out for key in ("created", "failed", "skipped", "details")):
        return True
    if _as_nonneg_int(out.get("created")) > 0:
        return True
    failed = out.get("failed")
    if isinstance(failed, list) and failed:
        return False
    skipped = out.get("skipped")
    return bool(isinstance(skipped, (list, tuple)) and skipped)


def _music_result_is_success(out: dict[str, Any]) -> bool:
    if out.get("music") == "already_present" or out.get("artifact_id"):
        return True
    if out.get("ok") is True:
        return True
    failure_keys = ("skipped", "skip_reason", "status", "error", "created")
    return not any(key in out for key in failure_keys)


def media_worker_result_is_success(out: Any, *, kind: str = "") -> bool:
    """Return true only when a media worker proves work or clean reuse."""
    if not isinstance(out, dict) or out.get("error"):
        return False
    skip_reason = out.get("skip_reason")
    if isinstance(skip_reason, str) and skip_reason.strip():
        return False
    if out.get("status") in {"empty_pool", "no_candidates"}:
        return False
    if _is_skip_reason_soft_fail(out.get("skipped")):
        return False
    if _failed_batch_without_output(out):
        return False

    kind_l = (kind or "").lower()
    if kind_l == "visual" or "visuals" in out or "reused" in out:
        return _visual_result_is_success(out)
    if kind_l == "voiceover" or "voiceovers" in out:
        return _voice_result_is_success(out)
    if kind_l == "sfx" or (
        "created" in out and ("failed" in out or "details" in out)
    ):
        return _sfx_result_is_success(out)
    if kind_l == "music":
        return _music_result_is_success(out)
    return True


def media_job_row_is_clean_success(row: dict[str, Any] | None) -> bool:
    """Latest media job row must be status=succeeded without error attrs."""
    if not row:
        return False
    if str(row.get("status") or "") != "succeeded":
        return False
    attrs = row.get("attributes") if isinstance(row.get("attributes"), dict) else {}
    if attrs.get("error"):
        return False
    # Only string skip *reasons* poison a succeeded row (not skip counts/lists).
    if _is_skip_reason_soft_fail(attrs.get("skipped")):
        return False
    if isinstance(attrs.get("skip_reason"), str) and attrs.get("skip_reason").strip():
        return False
    kind = str(row.get("kind") or "")
    if not media_worker_result_is_success(attrs, kind=kind):
        return False
    return True


def mark_preview_produced(client: Client, run_id: str) -> dict:
    """Mark script_status=produced for editor/preview readiness only.

    Does **not** set run.status=succeeded (customer-done requires playable URL).
    """
    return set_run_script_status(client, run_id, "produced")


def set_run_attr(client: Client, run_id: str, key: str, value: Any) -> None:
    """Atomic partial write of ``run.attributes[key]`` via ``set_run_attr`` RPC.

    Full-bag ``UPDATE run.attributes = {only_some_keys}`` wipes concurrent keys
    (T09/T12). Single-key stamps must use this helper (jsonb_set merge-on-write).
    """
    client.rpc(
        "set_run_attr",
        {"p_run_id": str(run_id), "p_key": str(key), "p_value": value},
    ).execute()


def mark_run_media_failed(client: Client, run_id: str, *, reason: str) -> dict:
    """Fail-loud terminal when required media jobs die — never stuck queued forever.

    Status/script_status/ended_at via column update only. Fail stamps
    (produce_error, fail_loud, fail_stage) via per-key ``set_run_attr`` so we
    never partial-bag-wipe existing run.attributes (N2 residual / T12).
    """
    reason_s = str(reason)[:500]
    payload: dict[str, Any] = {
        "status": "failed",
        "script_status": "failed",
        "ended_at": _DATABASE_NOW,
    }
    res = client.table("run").update(payload).eq("id", run_id).execute()
    set_run_attr(client, run_id, "produce_error", reason_s)
    set_run_attr(client, run_id, "fail_loud", True)
    set_run_attr(client, run_id, "fail_stage", "media_jobs")
    row = res.data[0] if res.data else {}
    # Surface fail stamps on the returned dict for callers; does not write bag.
    if row:
        attrs = dict(row["attributes"]) if isinstance(row.get("attributes"), dict) else {}
        attrs.update(
            {
                "produce_error": reason_s,
                "fail_loud": True,
                "fail_stage": "media_jobs",
            }
        )
        return {**row, "attributes": attrs}
    return {
        "status": "failed",
        "script_status": "failed",
        "attributes": {
            "produce_error": reason_s,
            "fail_loud": True,
            "fail_stage": "media_jobs",
        },
    }


def maybe_mark_run_produced(client: Client, run_id: str) -> dict:
    """Mark a run produced when all approval-created media jobs finish cleanly.

    The derive job only queues parallel media work. Render must wait until the
    visual/voiceover/music/sfx jobs have each reached a clean succeeded state.
    Soft-error attributes or zero visuals block produce. Any failed required job
    marks the run failed (fail-loud — no eternal queued/producing).
    """
    rows = (
        client.table("production_jobs")
        .select("id, kind, status, attributes, queued_at, created_at")
        .eq("run_id", run_id)
        .in_("kind", list(PRODUCTION_WORK_KINDS))
        .order("queued_at", desc=True)
        .order("created_at", desc=True)
        .execute()
        .data
        or []
    )
    if not rows:
        return {}
    by_kind: dict[str, dict[str, Any]] = {}
    for row in rows:
        kind = row.get("kind")
        if kind in PRODUCTION_WORK_KINDS and kind not in by_kind:
            by_kind[kind] = row
    if not REQUIRED_PRODUCTION_WORK_KINDS.issubset(by_kind):
        return {}
    relevant = {
        kind: row
        for kind, row in by_kind.items()
        if kind in REQUIRED_PRODUCTION_WORK_KINDS or kind in OPTIONAL_PRODUCTION_WORK_KINDS
    }
    statuses = {kind: str(row.get("status") or "") for kind, row in relevant.items()}
    if any(st in {"queued", "running"} for st in statuses.values()):
        return {}
    # Fail-loud: required kind failed → terminal fail (never leave producing forever).
    failed_required = [
        kind
        for kind in REQUIRED_PRODUCTION_WORK_KINDS
        if statuses.get(kind) == "failed"
    ]
    if failed_required:
        return mark_run_media_failed(
            client,
            run_id,
            reason=f"required media jobs failed: {','.join(sorted(failed_required))}",
        )
    # Required kinds must be clean succeeded (not skipped/cancelled/soft).
    for kind in REQUIRED_PRODUCTION_WORK_KINDS:
        if not media_job_row_is_clean_success(relevant.get(kind)):
            return {}
    return mark_preview_produced(client, run_id)


def end_run(client: Client, run_id: str, status: str = "succeeded", **fields: Any) -> dict:
    payload = {"status": status, "ended_at": _DATABASE_NOW, **fields}
    res = client.table("run").update(payload).eq("id", run_id).execute()
    return res.data[0] if res.data else {}


def start_span(
    client: Client,
    *,
    run_id: str,
    name: str,
    kind: str,
    service: str,
    target_slot_id: str | None = None,
    parent_span_id: str | None = None,
    input_preview: str | None = None,
    attributes: dict | None = None,
) -> dict:
    res = (
        client.table("span")
        .insert(
            {
                "run_id": run_id,
                "name": name,
                "kind": kind,
                "service": service,
                "target_slot_id": target_slot_id,
                "parent_span_id": parent_span_id,
                "input_preview": input_preview,
                "attributes": attributes or {},
            }
        )
        .execute()
    )
    return res.data[0]


def end_span(
    client: Client,
    span_id: str,
    *,
    status: str = "ok",
    output_preview: str | None = None,
    error: dict | None = None,
    attributes_patch: dict | None = None,
) -> dict:
    payload: dict[str, Any] = {"status": status, "ended_at": _DATABASE_NOW}
    if output_preview is not None:
        payload["output_preview"] = output_preview
    if error is not None:
        payload["error"] = error
    if attributes_patch is not None:
        rows = (
            client.table("span")
            .select("attributes")
            .eq("id", span_id)
            .limit(1)
            .execute()
            .data
        )
        current = dict(rows[0].get("attributes") or {}) if rows else {}
        payload["attributes"] = {**current, **attributes_patch}
    res = client.table("span").update(payload).eq("id", span_id).execute()
    return res.data[0] if res.data else {}


def create_slot(
    client: Client,
    *,
    run_id: str,
    track: str,
    start_ms: int,
    end_ms: int,
    beat_index: int | None = None,
) -> dict:
    res = (
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
    )
    return res.data[0]


def create_text_artifact(
    client: Client,
    *,
    run_id: str,
    slot_id: str,
    text: str,
    produced_by_span: str | None = None,
    sha256: str | None = None,
    role_code: str | None = None,
    category: str | None = None,
    attributes: dict | None = None,
) -> dict:
    """Inline text artifact (script/caption). Stored in `text_content`, no R2 needed.

    `role_code` / `category` are written as both first-class columns (when the
    schema supports them, post-0007) and as JSON `attributes` so the AssetBin
    can group these artifacts regardless of how new the live DB is.
    """
    import hashlib

    if sha256 is None:
        sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    attrs = dict(attributes or {})
    if role_code:
        attrs.setdefault("role_code", role_code)
    if category:
        attrs.setdefault("category", category)

    payload = {
        "run_id": run_id,
        "slot_id": slot_id,
        "produced_by_span": produced_by_span,
        "version": 1,
        "source": "ai",
        "storage": "inline",
        "storage_key": f"inline:{sha256[:16]}",
        "sha256": sha256,
        "mime": "text/plain",
        "text_content": text,
        "bytes": len(text.encode("utf-8")),
        "preview_text": text[:200],
        "attributes": attrs,
    }
    if role_code:
        payload["role_code"] = role_code
    if category:
        payload["category"] = category

    try:
        res = client.table("artifact").insert(payload).execute()
    except Exception:
        # role_code / category columns may not exist on a pre-0007 schema;
        # keep them in the JSONB `attributes` so the editor still groups
        # correctly.
        legacy = {k: v for k, v in payload.items() if k not in ("role_code", "category")}
        res = client.table("artifact").insert(legacy).execute()
    artifact = res.data[0]
    client.table("slot").update({"current_artifact_id": artifact["id"]}).eq(
        "id", slot_id
    ).execute()
    return artifact
