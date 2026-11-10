"""Conservative entity resolution and temporal alignment (spec section 10).

Principles:
- Use the strongest available identifiers first (IDs, VINs, FEIN, parcel).
- Fuzzy name similarity alone NEVER confirms identity.
- The same city or a similar address is not proof of the same premises.
- Every decision records why a match was accepted, rejected, or left
  unresolved.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import List, Optional

from app.normalization import normalize_address, normalize_entity_name
from app.schemas.canonical import LocationRecord


@dataclass
class AlignmentResult:
    state: str  # MATCHED | PARTIAL | UNRESOLVED
    rationale: str
    identifiers_used: List[str] = field(default_factory=list)


def match_names(name_a: Optional[str], name_b: Optional[str]) -> AlignmentResult:
    """Name similarity is evidence, not proof: returns at most PARTIAL."""
    na, nb = normalize_entity_name(name_a), normalize_entity_name(name_b)
    if not na or not nb:
        return AlignmentResult("UNRESOLVED", "Name missing on one or both sides")
    if na == nb:
        return AlignmentResult(
            "PARTIAL",
            "Normalized legal names match exactly; name match alone does not establish identity",
            ["legal_name"],
        )
    ratio = SequenceMatcher(None, na, nb).ratio()
    if ratio >= 0.85:
        return AlignmentResult(
            "PARTIAL",
            f"Names are similar (score {ratio:.2f}); requires corroborating identifier",
            ["legal_name"],
        )
    return AlignmentResult("UNRESOLVED", f"Names differ (score {ratio:.2f})", ["legal_name"])


def match_locations(a: LocationRecord, b: LocationRecord) -> AlignmentResult:
    """Match two location records conservatively.

    MATCHED requires a strong identifier: equal location_id within the
    same submission, or equal normalized full address (street + city +
    state). Address-only equality across documents is PARTIAL when the
    state/city components are incomplete.
    """
    used: List[str] = []
    if a.location_id and b.location_id and a.location_id == b.location_id:
        # A repeated row/location number is useful for alignment, but it
        # cannot make two visibly different street addresses the same site.
        # Let the reconciliation rule surface that disagreement explicitly.
        street_a = normalize_address(a.address)
        street_b = normalize_address(b.address)
        if street_a and street_b and street_a != street_b:
            return AlignmentResult(
                "UNRESOLVED", "Identical location_id but street addresses conflict",
                ["location_id", "address"],
            )
        return AlignmentResult("MATCHED", "Identical location_id with no street conflict", ["location_id"])
    addr_a = normalize_address(" ".join(filter(None, [a.address, a.city, a.state, a.postal_code])))
    addr_b = normalize_address(" ".join(filter(None, [b.address, b.city, b.state, b.postal_code])))
    if addr_a and addr_b:
        used.append("address")
        if addr_a == addr_b:
            if a.state and b.state and a.state != b.state:
                return AlignmentResult("UNRESOLVED", "Addresses normalize equal but states conflict", used)
            if a.city and b.city and a.state and b.state:
                return AlignmentResult("MATCHED", "Full normalized address incl. city/state matches", used)
            return AlignmentResult(
                "PARTIAL",
                "Normalized address matches but city/state incomplete on one side",
                used,
            )
        street_a, street_b = normalize_address(a.address), normalize_address(b.address)
        if street_a and street_b and street_a == street_b:
            if a.state and b.state and a.state == b.state:
                return AlignmentResult(
                    "PARTIAL",
                    "Street matches and states agree, but full address differs (city/zip); "
                    "same premises not established",
                    used,
                )
    if a.state and b.state and a.state != b.state:
        return AlignmentResult("UNRESOLVED", "Different states", ["state"])
    return AlignmentResult("UNRESOLVED", "Insufficient identifiers to match locations", used)


def align_periods(period_a: Optional[str], period_b: Optional[str],
                  effective_date: Optional[str] = None) -> AlignmentResult:
    """Align comparison periods. Strings here are period labels (e.g.
    policy year, valuation date) already normalized to ISO where possible."""
    if not period_a or not period_b:
        return AlignmentResult(
            "PARTIAL" if (period_a or period_b) else "UNRESOLVED",
            "Period missing on one or both sides; comparison is qualified",
        )
    if period_a == period_b:
        return AlignmentResult("MATCHED", f"Identical period {period_a}")
    # Same calendar year = partial alignment (policy vs fiscal ambiguity).
    if period_a[:4] == period_b[:4]:
        return AlignmentResult(
            "PARTIAL",
            f"Same year but different period basis ({period_a} vs {period_b}); "
            "policy-year vs fiscal-year not confirmed",
        )
    return AlignmentResult("UNRESOLVED", f"Different periods: {period_a} vs {period_b}")
