"""Tier-1 deterministic bilingual EN/EL room classifier with optional Tier-2 fallback hook."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Protocol

from cyelec.models.building import RoomType
from cyelec.models.electrical import ReviewFlag, ReviewSeverity
from cyelec.standards_loader import RoomTemplatesStandards, normalize_label

_MTEXT_CODE_RE = re.compile(r"\\P|\\[A-Za-z][^;]*;|[{}]")
_AUTOCAD_CTRL_RE = re.compile(r"%%[A-Za-z0-9]")
_AREA_PAREN_RE = re.compile(r"\([^)]*m[²2][^)]*\)", flags=re.IGNORECASE)
_INLINE_DIM_RE = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*(?:m[²2]|mp|sq\.?\s*ft|sf)\b|"
    r"\b\d+['′]-?\s*\d*(?:\s+\d+/\d+|\.\d+)?[\"″]?\s*"
    r"(?:[x×]\s*\d+['′]-?\s*\d*(?:\s+\d+/\d+|\.\d+)?[\"″]?|wide\b)",
    flags=re.IGNORECASE,
)
_SEP_SPLIT_RE = re.compile(r"[/|\n]+")


class RoomClassifierFallback(Protocol):
    """Optional Tier-2 fallback interface (reserved for future LLM disambiguation)."""

    def __call__(self, raw_label: str) -> RoomType | None:
        """Attempt to classify an ambiguous room label when Tier-1 deterministic lookup fails."""


@dataclass(frozen=True)
class RoomClassificationResult:
    """Result of classifying a raw architectural room label."""

    raw_label: str
    cleaned_label: str
    normalized_type: RoomType
    confidence: float
    matched_alias: str | None
    method: Literal[
        "tier1_exact",
        "tier1_substring",
        "tier2_fallback",
        "unclassified_default",
    ]
    flag: ReviewFlag | None = None


def clean_room_label(raw_label: str) -> str:
    """Strip DXF MTEXT/AutoCAD %%u codes and area/dimension annotations while keeping EN/EL text."""
    text = _MTEXT_CODE_RE.sub(" ", raw_label)
    text = _AUTOCAD_CTRL_RE.sub("", text)
    text = _AREA_PAREN_RE.sub(" ", text)
    text = _INLINE_DIM_RE.sub(" ", text)
    return " ".join(text.split()).strip(" -–—:/|")


def _extract_candidate_segments(cleaned: str) -> list[str]:
    """Split bilingual labels like 'ΣΑΛΟΝΙ / Living Room' into normalized candidate segments."""
    segments: list[str] = []
    full_norm = normalize_label(cleaned)
    if full_norm:
        segments.append(full_norm)
    for part in _SEP_SPLIT_RE.split(cleaned):
        norm = normalize_label(part)
        if norm and norm not in segments:
            segments.append(norm)
    # Also handle ' - ' separated bilingual labels without breaking hyphenated words like 'en-suite'
    for part in cleaned.split(" - "):
        norm = normalize_label(part)
        if norm and norm not in segments:
            segments.append(norm)
    return segments


def classify_room_label(
    raw_label: str,
    room_templates: RoomTemplatesStandards,
    *,
    room_id: str | None = None,
    tier2_fallback: RoomClassifierFallback | None = None,
) -> RoomClassificationResult:
    """Classify an English/Greek room label deterministically (Tier-1) with optional Tier-2 hook."""
    cleaned = clean_room_label(raw_label)
    alias_lookup = room_templates.build_alias_lookup()
    segments = _extract_candidate_segments(cleaned)

    # 1. Exact segment match (prefer longest matching alias across segments so
    #    'master bedroom' / 'κυριο υπνοδωματιο' beats generic 'bedroom')
    exact_matches: list[tuple[int, str, RoomType]] = []
    for seg in segments:
        if seg in alias_lookup:
            exact_matches.append((len(seg), seg, alias_lookup[seg]))
    if exact_matches:
        exact_matches.sort(key=lambda item: item[0], reverse=True)
        _, alias, rtype = exact_matches[0]
        return RoomClassificationResult(
            raw_label=raw_label,
            cleaned_label=cleaned,
            normalized_type=rtype,
            confidence=1.0,
            matched_alias=alias,
            method="tier1_exact",
        )

    # 2. Word-boundary substring match sorted longest alias first
    sorted_aliases = sorted(alias_lookup.items(), key=lambda kv: len(kv[0]), reverse=True)
    for seg in segments:
        for alias, rtype in sorted_aliases:
            pattern = rf"(?<!\w){re.escape(alias)}(?!\w)"
            if re.search(pattern, seg):
                return RoomClassificationResult(
                    raw_label=raw_label,
                    cleaned_label=cleaned,
                    normalized_type=rtype,
                    confidence=0.90,
                    matched_alias=alias,
                    method="tier1_substring",
                )

    # 3. Optional Tier-2 fallback hook (never invoked unless explicitly provided)
    if tier2_fallback is not None:
        fallback_type = tier2_fallback(cleaned)
        if fallback_type is not None:
            flag = ReviewFlag(
                code="ROOM_CLASSIFIED_VIA_TIER2",
                severity=ReviewSeverity.INFO,
                category="geometry",
                message=(
                    f"Room {room_id or '?'} label '{cleaned}' resolved to "
                    f"'{fallback_type.value}' via Tier-2 fallback; verify assignment."
                ),
            )
            return RoomClassificationResult(
                raw_label=raw_label,
                cleaned_label=cleaned,
                normalized_type=fallback_type,
                confidence=0.75,
                matched_alias=None,
                method="tier2_fallback",
                flag=flag,
            )

    # 4. Safe conservative default (Storage: lighting only, no A/C or heavy appliances)
    warn_flag = ReviewFlag(
        code="AMBIGUOUS_ROOM_LABEL",
        severity=ReviewSeverity.WARNING,
        category="geometry",
        message=(
            f"Room {room_id or '?'} label '{cleaned or raw_label}' did not match any EN/EL "
            "template alias; defaulted conservatively to 'storage'. ETEK Engineer review required."
        ),
    )
    return RoomClassificationResult(
        raw_label=raw_label,
        cleaned_label=cleaned,
        normalized_type=RoomType.STORAGE,
        confidence=0.0,
        matched_alias=None,
        method="unclassified_default",
        flag=warn_flag,
    )
