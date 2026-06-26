"""
Brand Kit fetcher for Modal worker context.
Calls the studio /api/brands/{slug}/kit endpoint (service_role authed).
Falls back gracefully if kit unavailable — renderers use hardcoded defaults.
"""
from functools import lru_cache
from typing import Optional, TypedDict
import os

try:
    import httpx
    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False


class BrandKit(TypedDict):
    id: str
    brand_slug: str
    name: str
    version: int
    colors: dict
    fonts: dict
    typography: dict
    spacing: dict
    shadows: dict
    logo_url: Optional[str]
    status: str
    schemaHash: str


@lru_cache(maxsize=32)
def get_brand_kit(brand_slug: str) -> Optional[BrandKit]:
    """
    Fetch the latest active brand kit for a brand. Cached in-process (32 kits).
    Returns None on any error — callers must use hardcoded fallbacks.
    """
    if not _HTTPX_AVAILABLE:
        return None

    studio_url = os.getenv('STUDIO_URL', 'https://studio.hi-ob.com')
    secret_key = os.getenv('SUPABASE_SECRET_KEY')
    if not secret_key:
        return None

    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(
                f"{studio_url}/api/brands/{brand_slug}/kit",
                headers={'Authorization': f'Bearer {secret_key}'},
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            return data.get('kit') if data.get('ok') else None
    except Exception as exc:
        print(f"[brand_kit] fetch {brand_slug} failed: {exc}")
        return None


def resolve_color(kit: Optional[BrandKit], role: str, fallback: str = '#000000') -> str:
    """Return a color by role ('primary', 'accent', …) from the kit, or fallback."""
    if not kit:
        return fallback
    return kit.get('colors', {}).get(role, fallback)


def resolve_font_family(kit: Optional[BrandKit], variant: str = 'body', fallback: str = 'Noto Sans KR') -> str:
    """Return font family for 'body' or 'display' from the kit, or fallback."""
    if not kit:
        return fallback
    return kit.get('fonts', {}).get(variant, {}).get('family', fallback)
