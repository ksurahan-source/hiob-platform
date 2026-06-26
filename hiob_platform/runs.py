"""Run + slot + artifact + span write helpers (service-role only)."""
from __future__ import annotations

from typing import Any

from supabase import Client

APPROVED_SCRIPT_STATUSES = frozenset({"approved", "queued", "produced"})
REQUIRED_PRODUCTION_WORK_KINDS = frozenset({"visual", "voiceover", "music", "sfx"})
OPTIONAL_PRODUCTION_WORK_KINDS = frozenset({"caption", "title_style"})
PRODUCTION_WORK_KINDS = REQUIRED_PRODUCTION_WORK_KINDS | OPTIONAL_PRODUCTION_WORK_KINDS


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
        "updated_at": "now()",
    }
    if status == "running":
        payload["started_at"] = "now()"
    if status in {"succeeded", "failed", "cancelled", "skipped"}:
        payload["ended_at"] = "now()"
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


def maybe_mark_run_produced(client: Client, run_id: str) -> dict:
    """Mark a run produced when all approval-created media jobs finish.

    The derive job only queues parallel media work. Render must wait until the
    visual/voiceover/music/sfx jobs have each reached a terminal non-failed
    state, otherwise the user can render placeholder clips. caption/title_style
    jobs are also respected when present, while older in-flight runs that only
    created the four media jobs can still finish.
    """
    rows = (
        client.table("production_jobs")
        .select("id, kind, status, queued_at, created_at")
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
    by_kind: dict[str, str | None] = {}
    for row in rows:
        kind = row.get("kind")
        if kind in PRODUCTION_WORK_KINDS and kind not in by_kind:
            by_kind[kind] = row.get("status")
    if not REQUIRED_PRODUCTION_WORK_KINDS.issubset(by_kind):
        return {}
    relevant = {
        kind: status
        for kind, status in by_kind.items()
        if kind in REQUIRED_PRODUCTION_WORK_KINDS or kind in OPTIONAL_PRODUCTION_WORK_KINDS
    }
    if any(status in {"queued", "running"} for status in relevant.values()):
        return {}
    if any(status == "failed" for status in relevant.values()):
        return {}
    return set_run_script_status(client, run_id, "produced")


def end_run(client: Client, run_id: str, status: str = "succeeded", **fields: Any) -> dict:
    payload = {"status": status, "ended_at": "now()", **fields}
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
    payload: dict[str, Any] = {"status": status, "ended_at": "now()"}
    if output_preview is not None:
        payload["output_preview"] = output_preview
    if error is not None:
        payload["error"] = error
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
