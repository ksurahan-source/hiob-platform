"""Agent output retrieval and caching for downstream workers.

Pure Supabase reads to fetch the latest art_director output.
"""
from __future__ import annotations

from typing import Any


def _latest_art_director_output(sb, run_id: str) -> dict[str, Any]:
    """Fetch the latest successful art_director output for a run.

    Args:
        sb: Service client (Supabase).
        run_id: Run ID to query.

    Returns:
        art_director.output dict with _call_id injected, or {} if not found.
    """
    team = sb.table("agent_team").select("id").eq("run_id", run_id).limit(1).execute().data
    if not team:
        return {}
    res = (
        sb.table("agent_call")
        .select("id, output")
        .eq("team_id", team[0]["id"])
        .eq("role_code", "art_director")
        .eq("status", "ok")
        .order("step_index", desc=True)
        .limit(1)
        .execute()
        .data
    )
    if not res:
        return {}
    output = res[0].get("output") or {}
    if isinstance(output, dict):
        return {**output, "_call_id": res[0].get("id")}
    return {}
