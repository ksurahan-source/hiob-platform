"""Supabase service client for trusted server-side writes.

Workers (Modal, Renderer) use the SECRET key — bypasses RLS.
"""
from __future__ import annotations

import os

from supabase import Client, create_client


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not configured")
    return value


def _required_url() -> str:
    """The Studio (Cloudflare Pages) uses NEXT_PUBLIC_SUPABASE_URL for the
    browser bundle; Modal workers historically used SUPABASE_URL. Accept
    either so the same secret payload works in both environments."""
    for name in ("SUPABASE_URL", "NEXT_PUBLIC_SUPABASE_URL"):
        value = os.environ.get(name)
        if value:
            return value
    raise RuntimeError("SUPABASE_URL (or NEXT_PUBLIC_SUPABASE_URL) is not configured")


def get_service_client() -> Client:
    """Return a Supabase client authenticated with the service (secret) key.

    Env: SUPABASE_URL | NEXT_PUBLIC_SUPABASE_URL, SUPABASE_SECRET_KEY
    """
    return create_client(_required_url(), _required("SUPABASE_SECRET_KEY"))
