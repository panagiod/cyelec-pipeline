"""Unit tests for Tier-1 deterministic bilingual EN/EL room classifier & Tier-2 fallback hook."""

from __future__ import annotations

from cyelec.ingest.room_classifier import classify_room_label, clean_room_label
from cyelec.models.building import RoomType
from cyelec.models.electrical import ReviewSeverity
from cyelec.standards_loader import load_standards


def test_clean_room_label_strips_mtext_codes_and_area() -> None:
    raw = r"{\fArial;ΣΑΛΟΝΙ / Living Room}\P(24.75 m²)"
    assert clean_room_label(raw) == "ΣΑΛΟΝΙ / Living Room"


def test_tier1_bilingual_greek_english_classification() -> None:
    std = load_standards().room_templates

    cases = [
        ("ΣΑΛΟΝΙ / Living Room", RoomType.LIVING_ROOM),
        ("Κουζίνα", RoomType.KITCHEN),
        ("ΚΥΡΙΟ ΥΠΝΟΔΩΜΑΤΙΟ / Master Bedroom", RoomType.MASTER_BEDROOM),
        ("Υπνοδωμάτιο 2 / Bedroom 2", RoomType.BEDROOM),
        ("ΜΠΑΝΙΟ / Bathroom", RoomType.BATHROOM),
        ("ΝΤΟΥΣ / En-Suite", RoomType.ENSUITE),
        ("ΤΟΥΑΛΕΤΑ / Guest WC", RoomType.WC),
        ("Πλυσταριό", RoomType.UTILITY),
        ("Βεράντα (6.00 m²)", RoomType.VERANDA),
        ("Διάδρομος", RoomType.CORRIDOR),
        ("Σαλοκουζίνα", RoomType.KITCHEN_LIVING),
    ]
    for label, expected in cases:
        res = classify_room_label(label, std)
        assert res.normalized_type == expected, f"Failed for {label!r}: got {res}"
        assert res.method in ("tier1_exact", "tier1_substring")
        assert res.flag is None


def test_ambiguous_label_defaults_conservatively_with_warning() -> None:
    std = load_standards().room_templates
    res = classify_room_label("Mystery Loft Annex", std, room_id="R99")

    assert res.normalized_type == RoomType.STORAGE
    assert res.method == "unclassified_default"
    assert res.flag is not None
    assert res.flag.code == "AMBIGUOUS_ROOM_LABEL"
    assert res.flag.severity == ReviewSeverity.WARNING


def test_tier2_fallback_hook_only_invoked_on_tier1_miss() -> None:
    std = load_standards().room_templates
    calls: list[str] = []

    def fake_llm_fallback(label: str) -> RoomType | None:
        calls.append(label)
        return RoomType.BEDROOM if "παιδικο" in label.lower() else None

    # Tier-1 hit must NOT invoke Tier-2
    hit = classify_room_label("Σαλόνι", std, tier2_fallback=fake_llm_fallback)
    assert hit.normalized_type == RoomType.LIVING_ROOM
    assert calls == []

    # Tier-1 miss invokes Tier-2 and emits INFO audit flag
    miss = classify_room_label("Παιδικο", std, room_id="R5", tier2_fallback=fake_llm_fallback)
    assert miss.normalized_type == RoomType.BEDROOM
    assert miss.method == "tier2_fallback"
    assert calls == ["Παιδικο"]
    assert miss.flag is not None
    assert miss.flag.code == "ROOM_CLASSIFIED_VIA_TIER2"
    assert miss.flag.severity == ReviewSeverity.INFO
