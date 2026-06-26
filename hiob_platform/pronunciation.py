"""Deterministic Korean pronunciation helpers for TTS-facing script text."""
from __future__ import annotations

import re
from typing import Any

ASCII_ALPHA_RE = re.compile(r"[A-Za-z]")
ASCII_TOKEN_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9_-]*\b")

_TERM_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bhi[-\s]?ob\b", re.IGNORECASE), "히옵"),
    (re.compile(r"\breels\b", re.IGNORECASE), "릴스"),
)


def has_ascii_alpha(text: str) -> bool:
    return bool(ASCII_ALPHA_RE.search(text or ""))


def unknown_ascii_terms(text: str) -> list[str]:
    """Return ASCII terms that still need human pronunciation edits."""
    return sorted({m.group(0) for m in ASCII_TOKEN_RE.finditer(text or "")})


def _override_pairs(overrides: Any) -> list[tuple[str, str]]:
    if not overrides:
        return []
    if isinstance(overrides, dict):
        raw_pairs = overrides.items()
    elif isinstance(overrides, list):
        raw_pairs = []
        for item in overrides:
            if isinstance(item, dict):
                source = item.get("source") or item.get("from") or item.get("term")
                replacement = item.get("replacement") or item.get("to") or item.get("pronunciation")
                raw_pairs.append((source, replacement))
    else:
        return []

    pairs: list[tuple[str, str]] = []
    for source, replacement in raw_pairs:
        src = str(source or "").strip()
        dst = str(replacement or "").strip()
        if src and dst:
            pairs.append((src, dst))
    pairs.sort(key=lambda pair: len(pair[0]), reverse=True)
    return pairs


def _apply_overrides(text: str, overrides: Any) -> str:
    out = text
    for source, replacement in _override_pairs(overrides):
        if has_ascii_alpha(source):
            pattern = rf"(?<![A-Za-z0-9_-]){re.escape(source)}(?![A-Za-z0-9_-])"
            out = re.sub(pattern, replacement, out, flags=re.IGNORECASE)
        else:
            out = out.replace(source, replacement)
    return out


def normalize_korean_pronunciation(text: str, overrides: Any = None) -> str:
    """Replace only known/user-approved tokens before Korean TTS.

    This is intentionally deterministic and closed-ended. It does not learn or
    persist a pronunciation dictionary from user content, and it does not guess
    Hangul pronunciations for unknown ASCII terms.
    """
    out = str(text or "")
    for pattern, replacement in _TERM_REPLACEMENTS:
        out = pattern.sub(replacement, out)
    out = re.sub(r"\bCTA\b", "지금 문의" if "문의" in out else "지금 신청", out, flags=re.IGNORECASE)
    return _apply_overrides(out, overrides)


def normalize_script_lines(lines: list[str], overrides: Any = None) -> list[str]:
    return [normalize_korean_pronunciation(line, overrides=overrides).strip() for line in lines]
