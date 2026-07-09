"""Deterministic Korean pronunciation helpers for TTS-facing script text."""
from __future__ import annotations

import re
from typing import Any

ASCII_ALPHA_RE = re.compile(r"[A-Za-z]")
ASCII_TOKEN_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9_-]*\b")

_TERM_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bhi[-\s]?ob\b", re.IGNORECASE), "히옵"),
    (re.compile(r"\breels\b", re.IGNORECASE), "릴스"),
    # 규격/기관 약어 — 폐쇄 화이트리스트 (2026-07-09 founder: TTS가 'OECD 405'를 '포헌드레드파이브'로 영어 독음)
    (re.compile(r"\bOECD\b"), "오이시디"),
    (re.compile(r"\bISO\b"), "아이에스오"),
    (re.compile(r"\bIEC\b"), "아이이씨"),
    (re.compile(r"\bKC\b"), "케이씨"),
)

# 규격 코드 숫자 독음: 약어(한글 치환 후) 뒤의 맨숫자는 한자어로 ("오이시디 405"→"오이시디 사백오").
# 단위 규칙(_SINO_UNIT_RE)이 못 잡는 '단위 없는 규격 번호' 전용 — 일반 맨숫자는 건드리지 않는다.
_STANDARD_CODE_RE = re.compile(r"(오이시디|아이에스오|아이이씨|케이씨)\s*(\d{2,5})\b")


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
    out = caption_numerals_to_digits(out)   # 대본의 한글 수사 표기를 숫자로 정규화("열초"→"10초")
    out = tts_numeral_reading(out)          # 숫자+단위 → 올바른 독음("10초"→"십초") — TTS 전용 진입점
    for pattern, replacement in _TERM_REPLACEMENTS:
        out = pattern.sub(replacement, out)
    out = _STANDARD_CODE_RE.sub(lambda m: f"{m.group(1)} {sino_reading(int(m.group(2)))}", out)
    out = re.sub(r"\bCTA\b", "지금 문의" if "문의" in out else "지금 신청", out, flags=re.IGNORECASE)
    return _apply_overrides(out, overrides)




# ── 수사(數詞) 처리 (2026-07-08 founder: "열초" 재발 수리) ─────────────────────────
# 규칙(국어 표준): 초·분·년·월·원·퍼센트·배·회 = **한자어 수사**(10초→십초, 1800→천팔백).
# 개·명·번·살·마리·잔·장 = 1~20 고유어(열 개), 21+ 한자어. **자막은 숫자 유지**(음성 전용).
_SINO_D = {0: "", 1: "일", 2: "이", 3: "삼", 4: "사", 5: "오", 6: "육", 7: "칠", 8: "팔", 9: "구"}
_SINO_UNITS = (("억", 100_000_000), ("만", 10_000), ("천", 1_000), ("백", 100), ("십", 10))
_NATIVE = {1: "한", 2: "두", 3: "세", 4: "네", 5: "다섯", 6: "여섯", 7: "일곱", 8: "여덟", 9: "아홉",
           10: "열", 20: "스물"}


def sino_reading(n: int) -> str:
    """1800 → '천팔백'. 관용: 만 단위 선두 '일' 생략(일만→만), 십/백/천 선두 '일' 생략."""
    if n == 0:
        return "영"
    out = ""
    for name, val in _SINO_UNITS:
        q, n = divmod(n, val)
        if q:
            out += (sino_reading(q) if q >= 10 else ("" if q == 1 and val >= 10 else _SINO_D[q])) + name
    return out + _SINO_D.get(n, "")


def native_reading(n: int) -> str:
    """1~20 고유어(관형형): 1→한, 10→열, 12→열두, 20→스무. 범위 밖=sino."""
    if n in _NATIVE:
        return "스무" if n == 20 else _NATIVE[n]
    if 10 < n < 20:
        return "열" + _NATIVE[n - 10]
    return sino_reading(n)


_SINO_UNIT_RE = re.compile(r"(\d[\d,]*)\s*(초|분|년|월(?!요일)|원|퍼센트|%|배|회|미터|킬로|리터)")
_NATIVE_UNIT_RE = re.compile(r"(\d[\d,]*)\s*(개월|개|명|번(?!호)|살|마리|잔|병|장|곡|시간)")


def tts_numeral_reading(text: str) -> str:
    """음성(TTS) 전용: 아라비아 숫자+단위 → 올바른 한글 독음. 자막에는 쓰지 않는다."""
    def _sino(m):
        return sino_reading(int(m.group(1).replace(",", ""))) + ("퍼센트" if m.group(2) == "%" else m.group(2))
    def _nat(m):
        n = int(m.group(1).replace(",", ""))
        unit = m.group(2)
        if unit == "개월":  # 개월=한자어
            return sino_reading(n) + unit
        return (native_reading(n) if 1 <= n <= 20 else sino_reading(n)) + " " + unit
    out = _SINO_UNIT_RE.sub(_sino, text or "")
    return _NATIVE_UNIT_RE.sub(_nat, out)


# 자막용: 한글 수사+단위 → 아라비아 숫자 ("열초"→"10초", "천팔백개"→"1800개")
_HANGUL_NUM_RE = re.compile(
    r"((?:[일이삼사오육칠팔구]?[십백천])+[일이삼사오육칠팔구]?|[일이삼사오육칠팔구십]|"
    r"스무|스물[한두세네]?|열[한두세네다섯여섯일곱여덟아홉]?|[한두세네]|다섯|여섯|일곱|여덟|아홉)"
    r"\s*(초|분|년|원|퍼센트|배|회|개월|개|명|번|살|마리|잔|병|장|곡|시간)")
_S_VAL = {"일": 1, "이": 2, "삼": 3, "사": 4, "오": 5, "육": 6, "칠": 7, "팔": 8, "구": 9}
_S_MUL = {"십": 10, "백": 100, "천": 1000}
_N_VAL = {"한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9,
          "열": 10, "스무": 20, "스물": 20}


def _parse_hangul_number(tok: str) -> int | None:
    if tok in _N_VAL:
        return _N_VAL[tok]
    if tok.startswith("스물") or tok.startswith("열"):
        base = 20 if tok.startswith("스물") else 10
        rest = tok[2:] if tok.startswith("스물") else tok[1:]
        return base + (_N_VAL.get(rest, 0) if rest else 0)
    total, cur = 0, 0
    for ch in tok:
        if ch in _S_VAL:
            cur = _S_VAL[ch]
        elif ch in _S_MUL:
            total += (cur or 1) * _S_MUL[ch]
            cur = 0
        else:
            return None
    return total + cur if (total or cur) else None


def caption_numerals_to_digits(text: str) -> str:
    """자막 전용: 한글 수사 표기 → 아라비아 숫자 (대본이 '열초'로 써도 화면엔 '10초')."""
    def _rep(m):
        n = _parse_hangul_number(m.group(1))
        return f"{n}{m.group(2)}" if n is not None else m.group(0)
    return _HANGUL_NUM_RE.sub(_rep, text or "")


def normalize_script_lines(lines: list[str], overrides: Any = None) -> list[str]:
    return [normalize_korean_pronunciation(line, overrides=overrides).strip() for line in lines]
