"""Discord notification helper. Posts run lifecycle events to a Discord webhook.

Set DISCORD_WEBHOOK_URL in the worker env (Modal secret) to enable. No-op if absent.
"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx

DEFAULT_USERNAME = "Hiob"
COLOR = {
    "started":   0x4f8cff,  # blue
    "succeeded": 0x4ade80,  # green
    "failed":    0xef4444,  # red
    "regen":     0xfacc15,  # yellow
    "info":      0x9ca3af,  # gray
}


def notify(event: str, *, title: str, fields: dict[str, Any] | None = None, url: str | None = None) -> bool:
    hook = os.environ.get("DISCORD_WEBHOOK_URL")
    if not hook:
        return False
    embed = {
        "title": title,
        "color": COLOR.get(event, COLOR["info"]),
        "timestamp": None,
    }
    if fields:
        embed["fields"] = [
            {"name": k, "value": str(v)[:1000], "inline": len(str(v)) < 32} for k, v in fields.items()
        ]
    if url:
        embed["url"] = url
    payload = {"username": DEFAULT_USERNAME, "embeds": [embed]}
    try:
        r = httpx.post(hook, json=payload, timeout=8)
        return 200 <= r.status_code < 300
    except Exception:
        return False


def notify_run_started(run_id: str, brief: dict | None) -> bool:
    title_text = (brief or {}).get("title") or "Untitled brief"
    return notify(
        "started",
        title=f"🎬 New run · {title_text}",
        fields={"run_id": run_id[:8], "brief": json.dumps(brief or {}, ensure_ascii=False)[:200]},
        url=_studio_run_url(run_id),
    )


def notify_run_done(run_id: str, summary: dict) -> bool:
    fields = {k: v for k, v in summary.items() if k != "run_id"}
    return notify(
        "succeeded",
        title="✅ Pipeline complete",
        fields={"run_id": run_id[:8], **fields},
        url=_studio_run_url(run_id),
    )


def notify_regen(run_id: str, slot_label: str, before: str, after: str) -> bool:
    return notify(
        "regen",
        title=f"✏️ Regenerated · {slot_label}",
        fields={"before": before[:200], "after": after[:200]},
        url=_studio_run_url(run_id),
    )


def _studio_run_url(run_id: str) -> str | None:
    base = os.environ.get("STUDIO_BASE_URL")
    return f"{base.rstrip('/')}/studio/runs/{run_id}" if base else None
