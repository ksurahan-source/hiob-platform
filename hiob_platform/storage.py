"""Supabase Storage upload + artifact registration."""
from __future__ import annotations

import hashlib
import re
from typing import Any

from supabase import Client

BUCKET = "hiob-artifacts"

# Known brand display-name → canonical slug aliases. A Korean (or other non-ASCII)
# display name cannot be slugified algorithmically, so its canonical slug is pinned
# here to stop the brand from FORKING at intake (an upload labeled "뷰오케이" must NOT
# create a second brand vs "viewok"). The real fix is a brand table (napkin DATA
# MODEL); this is the interim normalizer. Migration 0019 merged the existing fork.
BRAND_ALIASES: dict[str, str] = {
    "뷰오케이": "viewok",
    "hiob-marketing": "히옵 마케팅",
    "차트의 품격": "chart-leaders",
}


def canonical_brand_slug(name: str) -> str:
    """Canonicalize a brand/product display name to its stable slug.

    Known aliases first (KO → slug), then ASCII slugify. A non-ASCII name with no
    alias is returned trimmed unchanged (we cannot invent a slug without a brand
    table) — but a name that already IS the slug stays stable, so it never forks.
    """
    raw = (name or "").strip()
    if raw in BRAND_ALIASES:
        return BRAND_ALIASES[raw]
    slug = re.sub(r"[^a-z0-9._-]+", "-", raw.lower()).strip("-")
    return slug or raw


def canonicalize_brand_tags(tags: list[str] | None) -> list[str]:
    """Rewrite ``brand:<x>`` / ``product:<x>`` tags to canonical slugs and dedupe
    (order-preserving), so intake can never register a forked brand/product tag."""
    out: list[str] = []
    seen: set[str] = set()
    for tag in tags or []:
        canon = tag
        for prefix in ("brand:", "product:"):
            if tag.startswith(prefix):
                canon = prefix + canonical_brand_slug(tag[len(prefix):])
                break
        if canon not in seen:
            seen.add(canon)
            out.append(canon)
    return out


def upload_artifact(
    client: Client,
    *,
    run_id: str,
    slot_id: str,
    bytes_data: bytes,
    mime: str,
    extension: str,
    produced_by_span: str | None = None,
    duration_ms: int | None = None,
    width: int | None = None,
    height: int | None = None,
    preview_text: str | None = None,
    attributes: dict[str, Any] | None = None,
    set_current: bool = True,
) -> dict:
    """Upload bytes to Supabase Storage, insert artifact row, optionally set slot.current."""
    sha = hashlib.sha256(bytes_data).hexdigest()
    key = f"{run_id}/{slot_id}/{sha[:16]}.{extension}"
    # idempotent upload (upsert)
    client.storage.from_(BUCKET).upload(
        key,
        bytes_data,
        file_options={"content-type": mime, "upsert": "true"},
    )
    # determine next version
    existing = (
        client.table("artifact")
        .select("version")
        .eq("slot_id", slot_id)
        .order("version", desc=True)
        .limit(1)
        .execute()
    )
    version = (existing.data[0]["version"] + 1) if existing.data else 1
    art = (
        client.table("artifact")
        .insert(
            {
                "run_id": run_id,
                "slot_id": slot_id,
                "produced_by_span": produced_by_span,
                "version": version,
                "source": "ai",
                "storage": "supabase",
                "storage_key": key,
                "sha256": sha,
                "mime": mime,
                "bytes": len(bytes_data),
                "duration_ms": duration_ms,
                "width": width,
                "height": height,
                "preview_text": preview_text,
                "attributes": attributes or {},
            }
        )
        .execute()
        .data[0]
    )
    if set_current:
        client.table("slot").update({"current_artifact_id": art["id"]}).eq("id", slot_id).execute()
    register_asset_library_item(
        client,
        run_id=run_id,
        artifact=art,
        kind=_asset_kind(mime),
        source=attributes.get("source", "ai") if attributes else "ai",
        provider=attributes.get("provider") if attributes else None,
        provider_model=attributes.get("provider_model") if attributes else None,
        title=attributes.get("title") if attributes else None,
        preview_text=preview_text,
        license=attributes.get("license") if attributes else None,
        reuse_scope=attributes.get("reuse_scope", "workspace") if attributes else "workspace",
        tags=attributes.get("tags") if attributes else None,
        attributes=attributes or {},
    )
    return art


def public_url(client: Client, storage_key: str) -> str:
    return client.storage.from_(BUCKET).get_public_url(storage_key)


def resolve_pool_asset_sha256(client: Client, asset: dict[str, Any]) -> str:
    """Return a NOT-NULL-safe sha256 for a curated ``asset_pool`` row.

    The ``artifact`` table requires a non-null ``sha256``. Pool rows ingested by
    older tooling carry ``attributes.sha256``; rows from the current SFX/music
    importer omit it. When it is missing we download the stored object once,
    hash the bytes, and backfill the value onto the pool row (and the in-memory
    ``asset`` dict) so later cues and later runs skip the download. The computed
    hash is returned even when the backfill write fails, so a transient write
    error never blocks placement.
    """
    attrs = asset.get("attributes") or {}
    cached = attrs.get("sha256")
    if isinstance(cached, str) and len(cached) == 64:
        return cached

    storage_key = asset.get("storage_key")
    if not storage_key:
        raise ValueError(f"asset_pool row {asset.get('id')} has no storage_key; cannot compute sha256")

    # All curated pool objects (SFX + music) live in the shared artifacts bucket.
    data = client.storage.from_(BUCKET).download(storage_key)
    sha = hashlib.sha256(data).hexdigest()

    asset_id = asset.get("id")
    if asset_id:
        merged = {**attrs, "sha256": sha}
        try:
            client.table("asset_pool").update({"attributes": merged}).eq("id", asset_id).execute()
        except Exception:
            pass  # best-effort cache; the computed hash below is authoritative
        asset["attributes"] = merged
    return sha


def register_asset_library_item(
    client: Client,
    *,
    run_id: str | None,
    artifact: dict,
    kind: str,
    source: str = "ai",
    provider: str | None = None,
    provider_model: str | None = None,
    title: str | None = None,
    preview_text: str | None = None,
    license: str | None = None,
    reuse_scope: str = "workspace",
    tags: list[str] | None = None,
    attributes: dict[str, Any] | None = None,
    category: str | None = None,
    role_code: str | None = None,
) -> dict | None:
    """Best-effort registration in the reusable asset library.

    The artifact row remains canonical for timeline clips. The library row is
    the cross-run reuse surface; if the migration is not applied yet, artifact
    creation must still succeed.

    ``category`` / ``role_code`` populate the napkin data-model columns so the
    asset bin/picker can filter by ``category`` (logo/product_image/social_proof/
    music/sfx/voice…). They are written only when provided, keeping older callers
    and pre-migration databases unaffected.
    """
    # Canonicalize brand/product tags at intake so a KO display name never forks
    # the brand (napkin DATA MODEL; D4). e.g. brand:뷰오케이 → brand:viewok.
    canon_tags = canonicalize_brand_tags(tags)
    attrs = dict(attributes or {})
    source_norm = str(source or attrs.get("source") or "").strip().lower()
    generated_image_or_video = (
        kind in {"image", "video"}
        and (
            source_norm in {"ai", "generated", "synthetic"}
            or bool(attrs.get("generated"))
            or "source:ai" in canon_tags
            or "generated:true" in canon_tags
        )
    )
    if generated_image_or_video:
        if reuse_scope == "public":
            reuse_scope = "project"
        canon_tags = [t for t in canon_tags if t != "scope:global"]
        attrs["image_reuse_policy"] = attrs.get("image_reuse_policy") or "no_global_reuse"
        if attrs.get("scope") == "global":
            attrs["scope"] = "project"
    # D5 (napkin REAL ASSETS): flag a degenerate brand upload for human review.
    _role = role_code or next((t.split(":", 1)[1] for t in canon_tags if t.startswith("role:")), None)
    _brand = next((t.split(":", 1)[1] for t in canon_tags if t.startswith("brand:")), None)
    _siblings = (
        _brand_sibling_sha256s(client, _brand)
        if (_role or "").lower().replace("_", "-") == "social-proof"
        else None
    )
    _verdict = validate_brand_asset(
        role=_role, kind=kind, bytes_size=artifact.get("bytes"),
        duration_ms=artifact.get("duration_ms"), sha256=artifact.get("sha256"),
        sibling_asset_sha256s=_siblings,
    )
    if not _verdict["ok"]:
        attrs["needs_human"] = True
        attrs["needs_human_reason"] = _verdict["reason"]
        attrs["needs_human_code"] = _verdict["code"]
    payload = {
        "run_id": run_id,
        "artifact_id": artifact.get("id"),
        "kind": kind,
        "title": title,
        "source": source,
        "provider": provider,
        "provider_model": provider_model,
        "storage": artifact.get("storage", "supabase"),
        "storage_key": artifact["storage_key"],
        "sha256": artifact["sha256"],
        "mime": artifact["mime"],
        "bytes": artifact.get("bytes"),
        "duration_ms": artifact.get("duration_ms"),
        "width": artifact.get("width"),
        "height": artifact.get("height"),
        "preview_text": preview_text or artifact.get("preview_text"),
        "thumbnail_key": artifact.get("thumbnail_key"),
        "tags": canon_tags,
        "license": license,
        "reuse_scope": reuse_scope,
        "attributes": attrs,
    }
    # Napkin data-model columns — only set when provided so existing callers and
    # pre-0007 databases (no category/role_code columns) keep working unchanged.
    if category is not None:
        payload["category"] = category
    if role_code is not None:
        payload["role_code"] = role_code
    try:
        res = client.table("asset_library_item").upsert(
            payload,
            on_conflict="storage,storage_key",
        ).execute()
        return res.data[0] if res.data else None
    except Exception:
        return None


# D5 intake validation (napkin REAL ASSETS): reject degenerate brand uploads.
_PLACEHOLDER_VIDEO_MIN_BYTES = 50_000  # a "video" smaller than this is a near-black/empty placeholder
_PLACEHOLDER_VIDEO_MIN_MS = 500        # ...or shorter than half a second


def validate_brand_asset(
    *,
    role: str | None,
    kind: str | None,
    bytes_size: int | None,
    duration_ms: int | None = None,
    sha256: str | None = None,
    sibling_asset_sha256s: set[str] | frozenset[str] | None = None,
) -> dict[str, Any]:
    """Validate a brand asset at intake (napkin REAL ASSETS; D5). Returns
    ``{"ok": bool, "code": str|None, "reason": str|None}``. Flags (caller sets
    ``attributes.needs_human=true``; the napkin keeps claims with the human, so we
    flag for review rather than hard-delete):
      (a) a near-black/placeholder VIDEO — too few bytes or too short;
      (b) a SOCIAL-PROOF asset byte-identical to an existing brand logo/product
          asset (a re-used brand asset, NOT a real review/testimonial).
    """
    role_l = (role or "").lower().replace("_", "-")
    if (kind or "") == "video":
        if bytes_size is not None and bytes_size < _PLACEHOLDER_VIDEO_MIN_BYTES:
            return {"ok": False, "code": "placeholder_video",
                    "reason": f"video is only {bytes_size} bytes — a near-black/empty placeholder, not real footage"}
        if duration_ms is not None and duration_ms < _PLACEHOLDER_VIDEO_MIN_MS:
            return {"ok": False, "code": "placeholder_video",
                    "reason": f"video is only {duration_ms}ms — too short to be real footage"}
    if role_l == "social-proof" and sha256 and sibling_asset_sha256s and sha256 in sibling_asset_sha256s:
        return {"ok": False, "code": "proof_is_brand_asset",
                "reason": "social proof is byte-identical to an existing brand logo/product asset — not a real review/testimonial"}
    return {"ok": True, "code": None, "reason": None}


def _brand_sibling_sha256s(client: Client, brand: str) -> set[str]:
    """sha256s of a brand's existing logo/product assets — the set a social-proof
    upload must NOT duplicate (else it is a re-used brand asset, not real proof)."""
    out: set[str] = set()
    if not brand:
        return out
    try:
        rows = (
            client.table("asset_library_item")
            .select("sha256, tags")
            .contains("tags", [f"brand:{brand}"])
            .execute()
            .data
        ) or []
    except Exception:
        return out
    for r in rows:
        tags = r.get("tags") or []
        if r.get("sha256") and ("role:logo" in tags or "role:product" in tags):
            out.add(r["sha256"])
    return out


def _asset_kind(mime: str) -> str:
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("text/"):
        return "text"
    return "template"
